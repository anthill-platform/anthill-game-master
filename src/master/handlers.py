
import ujson

from tornado.gen import coroutine, Return
from tornado.web import HTTPError

from common.access import scoped, internal, AccessToken, remote_ip
from common.handler import AuthenticatedHandler

from data.host import HostNotFound
from data.controller import ControllerError
from data.player import Player, RoomNotFound, PlayerError, RoomError
from data.gameserver import GameServerNotFound
from common.internal import InternalError

from geoip import geolite2


class InternalHandler(object):
    def __init__(self, application):
        self.application = application

    @coroutine
    def controller_action(self, action, gamespace, room_id, args, kwargs):
        try:
            result = yield self.application.ctl_client.received(gamespace, room_id, action, args, kwargs) or {}
        except ControllerError as e:
            raise InternalError(500, e.message)

        raise Return(result)


class JoinHandler(AuthenticatedHandler):
    @scoped(scopes=[])
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


class JoinRoomHandler(AuthenticatedHandler):
    @scoped(scopes=[])
    @coroutine
    def post(self, game_name, room_id):

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account = self.token.account

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
    @scoped(scopes=[])
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
    @scoped(scopes=[])
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
        my_host_only = None
        ordered_hosts = None

        ignore_full = self.get_argument("ignore_full", "false") == "true"
        lock_my_region = self.get_argument("my_region_only", "false") == "true"

        if geo:
            x, y = geo.location

            if lock_my_region:
                try:
                    my_host_only = yield hosts.get_closest_host(x, y)
                except HostNotFound:
                    pass

            if not my_host_only:
                closest_hosts = yield hosts.list_closest_hosts(x, y)
                ordered_hosts = [host.host_id for host in closest_hosts]
        else:
            ordered_hosts = None

        rooms = yield rooms_data.list_rooms(
            gamespace, game_name, game_version,
            game_server_id, settings,
            hosts_order=ordered_hosts,
            ignore_full=ignore_full,
            my_host_only=my_host_only)

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
