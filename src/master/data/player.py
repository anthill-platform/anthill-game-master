
from tornado.gen import coroutine, Return, sleep

import tornado.ioloop
from room import RoomNotFound, RoomError
from gameserver import GameVersionNotFound
import logging

from common import random_string
from common.ratelimit import RateLimitExceeded
from geoip import geolite2


class Player(object):
    AUTO_REMOVE_TIME = 10
    LOCK_ACTIONS_TIME = 15

    def __init__(self, app, gamespace, game_name, game_version, game_server_name, account_id, access_token, ip):
        self.app = app
        self.hosts = app.hosts
        self.rooms = app.rooms
        self.gameservers = app.gameservers
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

    @coroutine
    def get_closest_host(self):

        location = self.get_location()

        if location:
            x, y = location
            host = yield self.hosts.get_closest_host(x, y)
        else:
            host = yield self.hosts.get_default_host()

        raise Return(host)

    def get_location(self):
        if not self.ip:
            return None

        geo = geolite2.lookup(self.ip)

        if not geo:
            return None

        return geo.location

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
            limit = yield self.app.ratelimit.limit("create_room", self.account_id)
        except RateLimitExceeded:
            raise PlayerError(429, "Too many requests")
        else:
            host = yield self.get_closest_host()

            self.record_id, key, self.room_id = yield self.rooms.create_and_join_room(
                self.gamespace, self.game_name, self.game_version,
                self.gs, room_settings, self.account_id, self.access_token,
                host.host_id)

            logging.info("Created a room: '{0}'".format(self.room_id))

            try:
                combined_settings = {
                    "game": self.game_settings,
                    "server": self.server_settings,
                    "room": room_settings
                }

                result = yield self.rooms.spawn_server(
                    self.gamespace, self.game_name, self.game_version, self.game_server_name,
                    self.room_id, host, combined_settings
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

            result.update({
                "id": self.room_id,
                "slot": self.record_id,
                "key": key
            })

            raise Return(result)

    @coroutine
    def join(self, search_settings, auto_create=False, create_room_settings=None):
        """
        Joins a player to the first available room. Waits until the room is
        :param search_settings: filters to search the rooms
        :param auto_create: if no such room, create one
        :param create_room_settings: in case room auto creation is triggered, will be use to fill the new room's
               settings
        """

        hosts_order = None

        geo = self.get_location()

        if geo:
            x, y = geo
            hosts = yield self.hosts.list_closest_hosts(x, y)
            hosts_order = [host.host_id for host in hosts]

        try:
            self.record_id, key, self.room = yield self.rooms.find_and_join_room(
                self.gamespace, self.game_name, self.game_version, self.gs.game_server_id,
                self.account_id, self.access_token, search_settings, hosts_order)

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
