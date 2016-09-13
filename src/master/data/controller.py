
from tornado.gen import coroutine, Return

import logging
from common.internal import Internal

from room import ApproveFailed


class ControllerError(Exception):
    def __init__(self, message):
        self.message = message


class ControllersClientModel(object):
    def __init__(self, rooms):
        self.rooms = rooms
        self.internal = Internal()

    @coroutine
    def joined(self, room_id, gamespace, key=None, extend_token=None, extend_scopes=None, **payload):

        if not key:
            raise ControllerError("No key field")

        try:
            access_token = yield self.rooms.approve_join(gamespace, room_id, key)
        except ApproveFailed:
            raise ControllerError("Failed to approve a join")
        else:
            if extend_token and extend_scopes:
                try:
                    extend = yield self.internal.request(
                        "login", "extend_token",
                        token=access_token, extend_with=extend_token, scopes=extend_scopes
                    )
                except InternalError as e:
                    raise ControllerError("Failed to extend token: {0} {1}".format(str(e.code), e.message))
                else:
                    access_token = extend["access_token"]

            # if everything is ok, return the token
            raise Return({
                "access_token": access_token
            })

    @coroutine
    def left(self, room_id, gamespace, key=None, **payload):

        if not key:
            raise ControllerError("No key field")

        try:
            yield self.rooms.approve_leave(gamespace, room_id, key)
        except ApproveFailed:
            raise ControllerError("Failed to approve a leave")
        else:
            raise Return({})
    @coroutine
    def received(self, room_id, gamespace, action, payload):
        receiver = getattr(self, action)

        if receiver:
            try:
                result = yield receiver(room_id, gamespace, **payload)
            except TypeError as e:
                raise ControllerError("Failed to call action '{0}': {1}".format(action, e.message))
            raise Return(result)
        else:
            raise ControllerError("No such action receiver: " + action)

    @coroutine
    def stopped(self, room_id, gamespace, **payload):
        logging.info("Room '{0}' died.".format(room_id))
        yield self.rooms.remove_room(gamespace, room_id)
        raise Return({})

