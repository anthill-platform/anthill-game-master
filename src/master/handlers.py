import ujson

from tornado.gen import coroutine, Return
from tornado.web import HTTPError

from common.access import scoped, internal, AccessToken, remote_ip
from common.handler import AuthenticatedHandler

from model.host import RegionNotFound, HostNotFound, HostError
from model.controller import ControllerError
from model.player import Player, PlayersGroup, RoomNotFound, PlayerError, RoomError, PlayerBanned
from model.gameserver import GameServerNotFound
from common.internal import InternalError

import logging

from geoip import geolite2


class InternalHandler(object):
    def __init__(self, application):
        self.application = application

    @coroutine
    def host_heartbeat(self, name, memory, cpu):
        logging.info("Host '{0}' load updated: {1} memory {2} cpu".format(name, memory, cpu))

        hosts = self.application.hosts

        try:
            host = yield hosts.find_host(name)
        except HostNotFound:
            raise InternalError(404, "Host not found")

        try:
            yield hosts.update_host_load(host.host_id, memory, cpu)
        except HostError as e:
            raise InternalError(500, str(e))

    @coroutine
    def controller_action(self, action, gamespace, room_id, args, kwargs):
        try:
            result = yield self.application.ctl_client.received(gamespace, room_id, action, args, kwargs) or {}
        except ControllerError as e:
            raise InternalError(500, e.message)

        raise Return(result)


class JoinHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def post(self, game_name, game_server_name, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account = self.token.account

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
            create_settings = ujson.loads(self.get_argument("create_settings", "{}"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        player = Player(self.application, gamespace, game_name, game_version,
                        game_server_name, account, self.token.key, ip)

        lock_my_region = self.get_argument("my_region_only", "false") == "true"
        auto_create = self.get_argument("auto_create", "true") == "true"

        try:
            yield player.init()
        except PlayerBanned as e:
            ban = e.ban

            logging.info("Banned user trying to join a game: @{0} ban {1}".format(ban.account, ban.ban_id))

            self.set_header("X-Ban-Until", ban.expires)
            self.set_header("X-Ban-Id", ban.ban_id)
            self.set_header("X-Ban-Reason", ban.reason)
            self.set_status(423, "You have been banned until: " + str(ban.expires))
            return
        except PlayerError as e:
            raise HTTPError(e.code, e.message)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        try:
            result = yield player.join(
                settings,
                auto_create=auto_create,
                create_room_settings=create_settings,
                lock_my_region=lock_my_region)
        except RoomNotFound as e:
            raise HTTPError(404, "No such room found")
        except PlayerError as e:
            raise HTTPError(e.code, e.message)

        self.dumps(result)


class JoinMultiHandler(AuthenticatedHandler):
    @scoped(scopes=["game", "game_multi"])
    @coroutine
    def post(self, game_name, game_server_name, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
            create_settings = ujson.loads(self.get_argument("create_settings", "{}"))
            accounts = ujson.loads(self.get_argument("accounts"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        lock_my_region = self.get_argument("my_region_only", "false") == "true"
        auto_create = self.get_argument("auto_create", "true") == "true"

        if not isinstance(accounts, list):
            raise HTTPError(400, "Accounts should be a list")

        ip = self.get_argument("ip", None)

        if ip:
            if not isinstance(ip, (str, unicode)):
                raise HTTPError(400, "ip is not a string")
        else:
            ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        players = PlayersGroup(self.application, gamespace, game_name, game_version,
                               game_server_name, accounts, ip)

        try:
            yield players.init()
        except PlayerError as e:
            raise HTTPError(e.code, e.message)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        try:
            results = yield players.join(
                settings,
                auto_create=auto_create,
                create_room_settings=create_settings,
                lock_my_region=lock_my_region)
        except RoomNotFound as e:
            raise HTTPError(404, "No such room found")
        except PlayerError as e:
            raise HTTPError(e.code, e.message)

        self.dumps(results)


class JoinRoomHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def post(self, game_name, room_id):

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account = self.token.account

        ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        ban = yield self.application.bans.lookup_ban(gamespace, account, ip)

        if ban:
            logging.info("Banned user trying to join a game: @{0} ban {1}".format(ban.account, ban.ban_id))

            self.set_header("X-Ban-Until", ban.expires)
            self.set_header("X-Ban-Id", ban.ban_id)
            self.set_header("X-Ban-Reason", ban.reason)
            self.set_status(423, "You have been banned until: " + str(ban.expires))
            return

        try:
            record_id, key, room = yield self.application.rooms.join_room(
                gamespace, game_name, room_id, account, self.token.key)
        except RoomNotFound:
            raise HTTPError(404, "Room not found")
        except RoomError as e:
            raise HTTPError(400, e.message)

        result = room.dump()
        result.update({
            "key": key,
            "slot": record_id
        })

        self.dumps(result)


class CreateHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def post(self, game_name, game_server_name, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account = self.token.account

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        player = Player(self.application, gamespace, game_name, game_version,
                        game_server_name, account, self.token.key, ip)

        try:
            yield player.init()
        except PlayerBanned as e:
            ban = e.ban

            logging.info("Banned user trying to join a game: @{0} ban {1}".format(ban.account, ban.ban_id))

            self.set_header("X-Ban-Until", ban.expires)
            self.set_header("X-Ban-Id", ban.ban_id)
            self.set_header("X-Ban-Reason", ban.reason)
            self.set_status(423, "You have been banned until: " + str(ban.expires))
            return
        except PlayerError as e:
            raise HTTPError(e.code, e.message)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        try:
            result = yield player.create(settings)
        except PlayerError as e:
            raise HTTPError(e.code, e.message)

        self.dumps(result)


class CreateMultiHandler(AuthenticatedHandler):
    @scoped(scopes=["game", "game_multi"])
    @coroutine
    def post(self, game_name, game_server_name, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
            accounts = ujson.loads(self.get_argument("accounts"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        ip = self.get_argument("ip", None)

        if ip:
            if not isinstance(ip, (str, unicode)):
                raise HTTPError(400, "ip is not a string")
        else:
            ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        if not isinstance(accounts, list):
            raise HTTPError(400, "Accounts should be a list")

        player = PlayersGroup(
            self.application, gamespace, game_name, game_version,
            game_server_name, accounts, ip)

        try:
            yield player.init()
        except PlayerError as e:
            raise HTTPError(e.code, e.message)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        try:
            result = yield player.create(settings)
        except PlayerError as e:
            raise HTTPError(e.code, e.message)

        self.dumps(result)


class RoomsHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def get(self, game_name, game_server_name, game_version):
        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        try:
            gs = yield self.application.gameservers.find_game_server(
                gamespace, game_name, game_server_name)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        game_server_id = gs.game_server_id

        ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        geo = geolite2.lookup(ip)

        rooms_data = self.application.rooms
        hosts = self.application.hosts
        my_region_only = None
        ordered_regions = None

        show_full = self.get_argument("show_full", "true") == "true"
        lock_my_region = self.get_argument("my_region_only", "false") == "true"

        if geo:
            p_lat, p_long = geo.location

            if lock_my_region:
                try:
                    my_region_only = yield hosts.get_closest_region(p_long, p_lat)
                except RegionNotFound:
                    pass

            if not my_region_only:
                closest_regions = yield hosts.list_closest_regions(p_long, p_lat)
                ordered_regions = [region.region_id for region in closest_regions]
        else:
            ordered_regions = None

        try:
            rooms = yield rooms_data.list_rooms(
                gamespace, game_name, game_version,
                game_server_id, settings,
                regions_order=ordered_regions,
                show_full=show_full,
                region=my_region_only)
        except RoomError as e:
            raise HTTPError(400, e.message)

        self.dumps({
            "rooms": [
                room.dump()
                for room in rooms
                ]
        })


class StatusHandler(AuthenticatedHandler):
    @coroutine
    def get(self):

        try:
            players_count = yield self.application.rooms.get_players_count()
        except RoomError as e:
            raise HTTPError(500, e.message)

        self.dumps({
            "players": players_count
        })
