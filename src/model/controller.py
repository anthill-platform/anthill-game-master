
from tornado.gen import coroutine, Return

from common.internal import Internal, InternalError
from common.access import AccessToken

from room import ApproveFailed, RoomError
from deploy import NoCurrentDeployment, DeploymentError

import logging


class ControllerError(Exception):
    def __init__(self, message, code=500):
        self.code = code
        self.message = message


class ControllersClientModel(object):
    def __init__(self, rooms, deployments):
        self.rooms = rooms
        self.deployments = deployments
        self.internal = Internal()

    @coroutine
    def joined(self, gamespace, room_id, key, extend_token=None, extend_scopes=None, **payload):

        try:
            access_token, info = yield self.rooms.approve_join(gamespace, room_id, key)
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

            parsed = AccessToken(access_token)

            # if everything is ok, return the token
            raise Return({
                "access_token": access_token,
                "account": parsed.account if parsed.is_valid() else None,
                "credential": parsed.get(AccessToken.USERNAME) if parsed.is_valid() else None,
                "info": info,
                "scopes": parsed.scopes if parsed.is_valid() else []
            })

    @coroutine
    def update_settings(self, gamespace, room_id, settings, **payload):

        logging.info("Room {0} settings updated".format(room_id))
        try:
            yield self.rooms.update_room_settings(gamespace, room_id, settings)
        except RoomError as e:
            raise ControllerError(e.message)
        else:
            raise Return({})

    @coroutine
    def left(self, gamespace, room_id, key=None, **payload):

        if not key:
            raise ControllerError("No key field")

        try:
            yield self.rooms.approve_leave(gamespace, room_id, key)
        except ApproveFailed:
            raise ControllerError("Failed to approve a leave")
        else:
            raise Return({})

    @coroutine
    def check_deployment(self, gamespace, room_id, game_name, game_version, deployment_id, **payload):

        try:
            deployment = yield self.deployments.get_current_deployment(gamespace, game_name, game_version)
        except NoCurrentDeployment:
            raise ControllerError("No deployment for that version", code=404)
        except DeploymentError as e:
            raise ControllerError(e.message)
        else:
            if not deployment.enabled:
                raise ControllerError("Game version is disabled", code=404)

            if str(deployment.deployment_id) != str(deployment_id):
                raise ControllerError("Deployment is outdated", code=410)

            raise Return({})

    @coroutine
    def received(self, gamespace, room_id, action, args, kwargs):
        receiver = getattr(self, action)

        if receiver:
            try:
                result = yield receiver(gamespace, room_id, *args, **kwargs)
            except TypeError as e:
                raise ControllerError("Failed to call action '{0}': {1}".format(action, e.message))
            raise Return(result)
        else:
            raise ControllerError("No such action receiver: " + action)

    @coroutine
    def stopped(self, gamespace, room_id, **payload):
        logging.info("Room '{0}' died.".format(room_id))
        yield self.rooms.remove_room(gamespace, room_id)
        raise Return({})

