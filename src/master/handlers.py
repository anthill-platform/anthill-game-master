
import ujson

from tornado.gen import coroutine, Return
from tornado.web import HTTPError

from common.access import scoped, internal, AccessToken
from common.handler import AuthenticatedHandler

from data.controller import ControllerError
from data.player import Player, RoomNotFound, PlayerError
from common.internal import InternalError


class ConfigHandler(AuthenticatedHandler):

    @internal
    @coroutine
    def get(self):
        games_data = self.application.games
        games_list = yield games_data.get_all_versions_settings()

        games = {}

        for game_db in games_list:
            game_id = game_db["game_id"]
            game_version = game_db["game_version"]
            gamespace_id = game_db["gamespace_id"]
            config = game_db["game_config"]

            if game_id in games:
                game = games[game_id]
                versions = game["versions"]
            else:
                stt = yield games_data.get_game_settings(gamespace_id, game_id)

                versions = {}
                game = {
                    "versions": versions,
                    "gamespace": gamespace_id,
                    "max_players": stt["max_players"]
                }
                games[game_id] = game

            versions[game_version] = {
                "config": config
            }

        self.dumps(games)


class InternalHandler(object):
    def __init__(self, application):
        self.application = application

    @coroutine
    def controller_action(self, action, room_id, gamespace, payload):
        try:
            result = yield self.application.ctl_client.received(room_id, gamespace, action, payload) or {}
        except ControllerError as e:
            raise InternalError(500, e.message)

        raise Return(result)


class JoinHandler(AuthenticatedHandler):
    @scoped(scopes=[])
    @coroutine
    def post(self, game_id, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account = self.token.account

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        player = Player(self.application, gamespace, game_id, game_version, account, self.token.key)
        auto_create = self.get_argument("auto_create", "true") == "true"

        try:
            yield player.init()
        except PlayerError as e:
            raise HTTPError(400, e.message)

        try:
            result = yield player.join(settings, auto_create=auto_create)
        except RoomNotFound as e:
            raise HTTPError(404, "Room not found: " + e.message)
        except PlayerError as e:
            raise HTTPError(400, e.message)

        self.dumps(result)


class RoomsHandler(AuthenticatedHandler):
    @scoped(scopes=[])
    @coroutine
    def get(self, game_id, game_version):
        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        rooms_data = self.application.rooms
        rooms = yield rooms_data.list_rooms(gamespace, game_id, game_version, settings)
        result = [room for room in rooms]
        self.dumps(result)
