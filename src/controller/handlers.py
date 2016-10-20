
from tornado.gen import coroutine, Return
from tornado.web import HTTPError

from common.internal import InternalError
from common.access import internal
from common.handler import AuthenticatedHandler
from data.server import SpawnError

import ujson


class InternalHandler(object):
    def __init__(self, application):
        self.application = application


class SpawnHandler(AuthenticatedHandler):
    @coroutine
    @internal
    def post(self):

        game_id = self.get_argument("game_id")
        game_version = self.get_argument("game_version")
        game_server_name = self.get_argument("game_server_name")
        gamespace = self.get_argument("gamespace")
        room_id = self.get_argument("room_id")

        try:
            settings = ujson.loads(self.get_argument("settings"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted settings")

        gs = self.application.gs
        rooms = self.application.rooms

        room = rooms.new(gamespace, room_id, settings)

        try:
            result = yield gs.spawn(game_id, game_version, game_server_name, room)
        except SpawnError as e:
            raise InternalError(500, "Failed to spawn: " + e.message)

        self.dumps(result)
