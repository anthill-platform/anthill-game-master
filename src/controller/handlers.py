
from tornado.gen import coroutine, Return
from tornado.web import HTTPError, stream_request_body

from common.internal import InternalError
from common.access import internal
from common.handler import AuthenticatedHandler

from data.server import SpawnError
from data.delivery import DeliveryError

import ujson


class InternalHandler(object):
    def __init__(self, application):
        self.application = application


@stream_request_body
class DeliverDeploymentHandler(AuthenticatedHandler):
    def __init__(self, application, request, **kwargs):
        super(DeliverDeploymentHandler, self).__init__(application, request, **kwargs)
        self.delivery = None

    @coroutine
    @internal
    def put(self):
        try:
            yield self.delivery.complete()
        except DeliveryError as e:
            raise HTTPError(e.code, e.message)

    @coroutine
    def data_received(self, chunk):
        yield self.delivery.data_received(chunk)

    @coroutine
    @internal
    def prepared(self, *args, **kwargs):
        game_name = self.get_argument("game_name")
        game_version = self.get_argument("game_version")
        deployment_id = self.get_argument("deployment_id")
        deployment_hash = self.get_argument("deployment_hash")

        delivery = self.application.delivery

        try:
            self.delivery = yield delivery.deliver(
                game_name, game_version, deployment_id, deployment_hash)
        except DeliveryError as e:
            raise HTTPError(e.code, e.message)


class SpawnHandler(AuthenticatedHandler):
    @coroutine
    @internal
    def post(self):

        game_id = self.get_argument("game_id")
        game_version = self.get_argument("game_version")
        game_server_name = self.get_argument("game_server_name")
        gamespace = self.get_argument("gamespace")
        room_id = self.get_argument("room_id")
        deployment = self.get_argument("deployment")

        try:
            settings = ujson.loads(self.get_argument("settings"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted settings")

        gs = self.application.gs
        rooms = self.application.rooms

        room = rooms.new(gamespace, room_id, settings)

        try:
            result = yield gs.spawn(game_id, game_version, game_server_name, deployment, room)
        except SpawnError as e:
            raise HTTPError(500, "Failed to spawn: " + e.message)

        self.dumps(result)
