
import ujson

from tornado.gen import coroutine, Return
from tornado.web import HTTPError

from common.access import scoped, internal, AccessToken
from common.handler import AuthenticatedHandler

from data.controller import ControllerError
from data.player import Player, RoomNotFound, PlayerError
from common.internal import InternalError


class InternalHandler(object):
    def __init__(self, application):
        self.application = application

    @coroutine
    def controller_action(self, action, gamespace, room_id, payload):
        try:
            result = yield self.application.ctl_client.received(gamespace, room_id, action, payload) or {}
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

        player = Player(self.application, gamespace, game_name, game_version,
                        game_server_name, account, self.token.key)

        auto_create = self.get_argument("auto_create", "true") == "true"

        try:
            yield player.init()
        except PlayerError as e:
            raise HTTPError(400, e.message)

        try:
            result = yield player.join(settings, auto_create=auto_create, create_room_settings=create_settings)
        except RoomNotFound as e:
            raise HTTPError(404, "No such room found")
        except PlayerError as e:
            raise HTTPError(400, e.message)

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

        player = Player(self.application, gamespace, game_name, game_version,
                        game_server_name, account, self.token.key)

        try:
            yield player.init()
        except PlayerError as e:
            raise HTTPError(400, e.message)

        try:
            result = yield player.create(settings)
        except PlayerError as e:
            raise HTTPError(400, e.message)

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

        rooms_data = self.application.rooms
        rooms = yield rooms_data.list_rooms(gamespace, game_name, game_version, settings)
        result = [room.dump() for room in rooms]
        self.dumps(result)
