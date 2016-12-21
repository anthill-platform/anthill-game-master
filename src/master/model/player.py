
from tornado.gen import coroutine, Return, sleep

import tornado.ioloop

from room import RoomNotFound, RoomError
from host import HostNotFound, RegionNotFound

from gameserver import GameVersionNotFound
from deploy import NoCurrentDeployment
import logging

from common import random_string
from common.ratelimit import RateLimitExceeded
from geoip import geolite2


class PlayerBanned(Exception):
    def __init__(self, ban):
        self.ban = ban


class Player(object):
    def __init__(self, app, gamespace, game_name, game_version, game_server_name, account_id, access_token, ip):
        self.app = app
        self.hosts = app.hosts
        self.rooms = app.rooms
        self.gameservers = app.gameservers
        self.bans = app.bans
        self.gamespace = gamespace
        self.ip = ip

        self.game_name = game_name
        self.game_version = game_version
        self.game_server_name = game_server_name

        self.account_id = str(account_id)

        self.gs = None
        self.game_settings = None

        self.server_settings = {}
        self.players = []
        self.room = None
        self.room_id = None
        self.record_id = None
        self.access_token = access_token

    @coroutine
    def init(self):
        self.gs = yield self.gameservers.find_game_server(
            self.gamespace, self.game_name, self.game_server_name)

        self.game_settings = self.gs.game_settings

        try:
            self.server_settings = yield self.gameservers.get_version_game_server(
                self.gamespace, self.game_name, self.game_version, self.gs.game_server_id)
        except GameVersionNotFound as e:
            logging.info("Applied default config for version '{0}'".format(self.game_version))
            self.server_settings = self.gs.server_settings

            if self.server_settings is None:
                raise PlayerError(500, "No default version configuration")

        ban = yield self.bans.lookup_ban(self.gamespace, self.account_id, self.ip)

        if ban:
            raise PlayerBanned(ban)

    @coroutine
    def get_closest_region(self):

        location = self.get_location()

        if location:
            p_lat, p_long = location
            region = yield self.hosts.get_closest_region(p_long, p_lat)
        else:
            region = yield self.hosts.get_default_region()

        raise Return(region)

    def get_location(self):
        if not self.ip:
            return None

        geo = geolite2.lookup(self.ip)

        if not geo:
            return None

        return geo.location

    @coroutine
    def get_best_host(self, region):
        host = yield self.hosts.get_best_host(region.region_id)
        raise Return(host)

    @coroutine
    def create(self, room_settings):

        if not isinstance(room_settings, dict):
            raise PlayerError(400, "Settings is not a dict")

        room_settings = {
            key: value
            for key, value in room_settings.iteritems()
            if isinstance(value, (str, unicode, int, float, bool))
        }

        try:
            deployment = yield self.app.deployments.get_current_deployment(
                self.gamespace, self.game_name, self.game_version)
        except NoCurrentDeployment:
            raise PlayerError(500, "No deployment defined for {0}/{1}".format(
                self.game_name, self.game_version
            ))

        deployment_id = deployment.deployment_id

        try:
            limit = yield self.app.ratelimit.limit("create_room", self.account_id)
        except RateLimitExceeded:
            raise PlayerError(429, "Too many requests")
        else:
            try:
                region = yield self.get_closest_region()
            except RegionNotFound:
                raise PlayerError(404, "Host not found")

            try:
                host = yield self.get_best_host(region)
            except HostNotFound:
                raise PlayerError(503, "Not enough hosts")

            self.record_id, key, self.room_id = yield self.rooms.create_and_join_room(
                self.gamespace, self.game_name, self.game_version,
                self.gs, room_settings, self.account_id, self.access_token,
                host, deployment_id, False)

            logging.info("Created a room: '{0}'".format(self.room_id))

            try:
                combined_settings = {
                    "game": self.game_settings,
                    "server": self.server_settings,
                    "room": room_settings
                }

                result = yield self.rooms.spawn_server(
                    self.gamespace, self.game_name, self.game_version, self.game_server_name,
                    deployment_id, self.room_id, host, combined_settings
                )
            except RoomError as e:
                # failed to spawn a server, then leave
                # this will likely to cause the room to be deleted
                yield self.leave(True)
                logging.exception("Failed to spawn a server")
                yield limit.rollback()
                raise e

            updated_room_settings = result.get("settings")

            if updated_room_settings:
                room_settings.update(updated_room_settings)

                yield self.rooms.update_room_settings(self.gamespace, self.room_id, room_settings)

            self.rooms.trigger_remove_temp_reservation(self.gamespace, self.room_id, self.account_id)

            result.update({
                "id": self.room_id,
                "slot": self.record_id,
                "key": key
            })

            raise Return(result)

    @coroutine
    def join(self, search_settings,
             auto_create=False,
             create_room_settings=None,
             lock_my_region=False):
        """
        Joins a player to the first available room. Waits until the room is
        :param search_settings: filters to search the rooms
        :param auto_create: if no such room, create one
        :param create_room_settings: in case room auto creation is triggered, will be use to fill the new room's
               settings
        :param lock_my_region: should be search applied to the player's region only
        """

        regions_order = None

        geo = self.get_location()
        my_region_only = None

        if geo:
            p_lat, p_long = geo

            if lock_my_region:
                try:
                    my_region_only = yield self.hosts.get_closest_region(p_long, p_lat)
                except RegionNotFound:
                    pass

            if not my_region_only:
                regions = yield self.hosts.list_closest_regions(p_long, p_lat)
                regions_order = [region.region_id for region in regions]

        try:
            self.record_id, key, self.room = yield self.rooms.find_and_join_room(
                self.gamespace, self.game_name, self.game_version, self.gs.game_server_id,
                self.account_id, self.access_token, search_settings,

                regions_order=regions_order,
                region=my_region_only)

        except RoomNotFound as e:
            if auto_create:
                logging.info("No rooms found, creating one")

                result = yield self.create(create_room_settings or {})
                raise Return(result)

            else:
                raise e
        else:
            self.room_id = self.room.room_id

            location = self.room.location
            settings = self.room.room_settings

        raise Return({
            "id": self.room_id,
            "slot": self.record_id,
            "location": location,
            "settings": settings,
            "key": key
        })

    @coroutine
    def leave(self, remove_room=False):
        if (self.record_id is None) or (self.room_id is None):
            return

        yield self.rooms.leave_room(self.gamespace, self.room_id, self.account_id, remove_room=remove_room)

        self.record_id = None
        self.room = None


class PlayerError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message


class Room(object):
    def __init__(self, room):
        self.room_id = room["room_id"]
        self.room = room
