
from tornado.gen import coroutine, Return, sleep

import tornado.ioloop

from room import RoomNotFound, RoomError
from host import HostNotFound, RegionNotFound

from gameserver import GameVersionNotFound
from deploy import NoCurrentDeployment

import logging
import uuid

from common import random_string
from common.access import AccessToken
from common.ratelimit import RateLimitExceeded
from geoip import geolite2


class PlayerBanned(Exception):
    def __init__(self, ban):
        self.ban = ban


class Player(object):
    def __init__(self, app, gamespace, game_name, game_version, game_server_name,
                 account_id, access_token, player_info, ip):
        self.app = app
        self.hosts = app.hosts
        self.rooms = app.rooms
        self.gameservers = app.gameservers
        self.bans = app.bans
        self.gamespace = gamespace
        self.ip = ip
        self.player_info = player_info

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
            raise PlayerError(404, "No deployment defined for {0}/{1}".format(
                self.game_name, self.game_version
            ))

        if not deployment.enabled:
            raise PlayerError(410, "Deployment is disabled for {0}/{1}".format(
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
                self.gs, room_settings, self.account_id, self.access_token, self.player_info,
                host, deployment_id, False)

            logging.info("Created a room: '{0}'".format(self.room_id))

            try:
                result = yield self.rooms.spawn_server(
                    self.gamespace, self.game_name, self.game_version, self.game_server_name,
                    deployment_id, self.room_id, host, self.game_settings, self.server_settings,
                    room_settings)
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

            self.rooms.trigger_remove_temp_reservation(self.record_id)

            result.update({
                "id": str(self.room_id),
                "slot": str(self.record_id),
                "key": key
            })

            raise Return(result)

    @coroutine
    def join(self, search_settings,
             auto_create=False,
             create_room_settings=None,
             lock_my_region=False,
             selected_region=None):
        """
        Joins a player to the first available room. Waits until the room is
        :param search_settings: filters to search the rooms
        :param auto_create: if no such room, create one
        :param create_room_settings: in case room auto creation is triggered, will be use to fill the new room's
               settings
        :param lock_my_region: should be search applied to the player's region only
        :param selected_region: a name of the region to apply the search on
        """

        regions_order = None

        if selected_region:
            try:
                region_lock = yield self.hosts.find_region(selected_region)
            except RegionNotFound:
                raise PlayerError(404, "No such region")
        else:
            geo = self.get_location()
            region_lock = None

            if geo:
                p_lat, p_long = geo

                if lock_my_region:
                    try:
                        region_lock = yield self.hosts.get_closest_region(p_long, p_lat)
                    except RegionNotFound:
                        pass

                if not region_lock:
                    regions = yield self.hosts.list_closest_regions(p_long, p_lat)
                    regions_order = [region.region_id for region in regions]

        try:
            self.record_id, key, self.room = yield self.rooms.find_and_join_room(
                self.gamespace, self.game_name, self.game_version, self.gs.game_server_id,
                self.account_id, self.access_token, self.player_info, search_settings,

                regions_order=regions_order,
                region=region_lock)

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
            "id": str(self.room_id),
            "slot": str(self.record_id),
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


class PlayersGroup(object):
    def __init__(self, app, gamespace, game_name, game_version, game_server_name, account_records, ip):
        self.app = app
        self.hosts = app.hosts
        self.rooms = app.rooms
        self.gameservers = app.gameservers
        self.bans = app.bans
        self.gamespace = gamespace
        self.ip = ip
        self.group_id = str(uuid.uuid4())

        self.game_name = game_name
        self.game_version = game_version
        self.game_server_name = game_server_name

        self.gs = None
        self.game_settings = None

        self.account_records = account_records
        self.tokens = []

        self.server_settings = {}
        self.players = []
        self.room = None
        self.room_id = None

    def __parse_account__(self, account, out_accounts, out_ips):
        if not isinstance(account, dict):
            raise PlayerError(400, "Account record is expected to be {\"token\": ..., \"ip\": <ip>}")

        token_key = account.get("token", None)
        ip = account.get("ip", None)

        if not token_key or not ip:
            raise PlayerError(400, "Account record is expected to be {\"token\": ..., \"ip\": <ip>}")

        if not isinstance(token_key, (unicode, str)):
            return PlayerError(400, "Account token key is not a string")

        if not isinstance(ip, (unicode, str)):
            return PlayerError(400, "Account ip key is not a string")

        token = AccessToken(token_key)

        if not token.validate():
            return False

        if token.account in out_accounts:
            # such account is already in list (to prevent join several times)
            return False

        self.tokens.append(token)
        out_ips.append(ip)
        out_accounts.append(token.account)

    @coroutine
    def init(self):

        if not self.account_records:
            raise PlayerError(400, "Accounts is empty")

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

        _accounts = []
        _ips = []

        for account in self.account_records:
            self.__parse_account__(account, _accounts, _ips)

        if not _accounts:
            raise PlayerError(404, "No valid players posted")

        # and the end of the day, we have:

        # accounts = [1, 2, 3, ...]
        # ips = ["1.2.3.4", "1.2.3.5", "1.2.3.6", ...]
        # self.tokens = [AccessToken(1), AccessToken(2), AccessToken(3), ...]

        banned_accounts = yield self.bans.find_bans(self.gamespace, _accounts, _ips)

        def filter_banned(check):
            if int(check.account) in banned_accounts:
                logging.info("Banned account tried to join group: @" + str(check.account))
                return False
            return True

        # remove tokens that are banned
        self.tokens = filter(filter_banned, self.tokens)

        if not self.tokens:
            raise PlayerError(404, "No valid players posted (all players are either banned or nor valid)")

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
            raise PlayerError(404, "No deployment defined for {0}/{1}".format(
                self.game_name, self.game_version
            ))

        if not deployment.enabled:
            raise PlayerError(410, "Deployment is disabled for {0}/{1}".format(
                self.game_name, self.game_version
            ))

        deployment_id = deployment.deployment_id

        # there's no ratelimit check here, because this operation requires a token
        # that user doesn't normally have

        try:
            region = yield self.get_closest_region()
        except RegionNotFound:
            raise PlayerError(404, "Host not found")

        try:
            host = yield self.get_best_host(region)
        except HostNotFound:
            raise PlayerError(503, "Not enough hosts")

        create_members = [
            (token, {
                "multi:id": self.group_id
            })
            for token in self.tokens
        ]

        records, self.room_id = yield self.rooms.create_and_join_room_multi(
            self.gamespace, self.game_name, self.game_version,
            self.gs, room_settings, create_members,
            host, deployment_id, False)

        logging.info("Created a room: '{0}'".format(self.room_id))

        try:
            result = yield self.rooms.spawn_server(
                self.gamespace, self.game_name, self.game_version, self.game_server_name,
                deployment_id, self.room_id, host, self.game_settings, self.server_settings,
                room_settings)
        except RoomError as e:
            # failed to spawn a server, then leave
            # this will likely to cause the room to be deleted
            yield self.leave(True)
            logging.exception("Failed to spawn a server")
            raise e

        updated_room_settings = result.get("settings")

        if updated_room_settings:
            room_settings.update(updated_room_settings)

            yield self.rooms.update_room_settings(self.gamespace, self.room_id, room_settings)

        accounts = [token.account for token in self.tokens]

        self.rooms.trigger_remove_temp_reservation_multi(self.gamespace, self.room_id, accounts)

        result.update({
            "id": str(self.room_id),
            "slots": {
                str(account): {
                    "slot": str(record_id),
                    "key": str(key)
                }
                for account, (record_id, key) in records.iteritems()
            }
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

        join_members = [
            (token, {
                "multi:id": self.group_id
            })
            for token in self.tokens
        ]

        try:
            records, self.room = yield self.rooms.find_and_join_room_multi(
                self.gamespace, self.game_name, self.game_version, self.gs.game_server_id,
                join_members, search_settings,

                regions_order=regions_order,
                region=my_region_only.region_id if my_region_only else None)

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
            "id": str(self.room_id),
            "slots": {
                str(account): {
                    "slot": str(record_id),
                    "key": str(key)
                }
                for account, (record_id, key) in records.iteritems()
            },
            "location": location,
            "settings": settings
        })

    @coroutine
    def leave(self, remove_room=False):
        if (self.tokens is None) or (self.room_id is None):
            return

        accounts = [token.account for token in self.tokens]

        yield self.rooms.leave_room_multi(
            self.gamespace, self.room_id, accounts, remove_room=remove_room)

        self.room = None
        self.tokens = None


class PlayerError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message


class Room(object):
    def __init__(self, room):
        self.room_id = room["room_id"]
        self.room = room
