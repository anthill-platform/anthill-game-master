
from anthill.common.internal import Internal, InternalError
from anthill.common.access import AccessToken

from . room import ApproveFailed, RoomError
from . deploy import NoCurrentDeployment, DeploymentError

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

    async def joined(self, gamespace, room_id, key, extend_token=None, extend_scopes=None, **payload):

        try:
            access_token, info = await self.rooms.approve_join(gamespace, room_id, key)
        except ApproveFailed:
            raise ControllerError("Failed to approve a join")
        else:
            if extend_token and extend_scopes:
                try:
                    extend = await self.internal.request(
                        "login", "extend_token",
                        token=access_token, extend_with=extend_token, scopes=extend_scopes
                    )
                except InternalError as e:
                    raise ControllerError("Failed to extend token: {0} {1}".format(str(e.code), str(e)))
                else:
                    access_token = extend["access_token"]

            parsed = AccessToken(access_token)

            # if everything is ok, return the token
            return {
                "access_token": access_token,
                "account": parsed.account if parsed.is_valid() else None,
                "credential": parsed.get(AccessToken.USERNAME) if parsed.is_valid() else None,
                "info": info,
                "scopes": parsed.scopes if parsed.is_valid() else []
            }

    async def update_settings(self, gamespace, room_id, settings, **payload):

        logging.info("Room {0} settings updated".format(room_id))
        try:
            await self.rooms.update_room_settings(gamespace, room_id, settings)
        except RoomError as e:
            raise ControllerError(e.message)
        else:
            return {}

    async def left(self, gamespace, room_id, key=None, **payload):

        if not key:
            raise ControllerError("No key field")

        try:
            await self.rooms.approve_leave(gamespace, room_id, key)
        except ApproveFailed:
            raise ControllerError("Failed to approve a leave")
        else:
            return {}

    async def check_deployment(self, gamespace, room_id, game_name, game_version, deployment_id, **payload):

        try:
            deployment = await self.deployments.get_current_deployment(gamespace, game_name, game_version)
        except NoCurrentDeployment:
            raise ControllerError("No deployment for that version", code=404)
        except DeploymentError as e:
            raise ControllerError(e.message)
        else:
            if not deployment.enabled:
                raise ControllerError("Game version is disabled", code=404)

            if str(deployment.deployment_id) != str(deployment_id):
                raise ControllerError("Deployment is outdated", code=410)

            return {}

    async def received(self, gamespace, room_id, action, args, kwargs):
        receiver = getattr(self, action)

        if receiver:
            try:
                result = await receiver(gamespace, room_id, *args, **kwargs)
            except TypeError as e:
                raise ControllerError("Failed to call action '{0}': {1}".format(action, str(e)))
            return result
        else:
            raise ControllerError("No such action receiver: " + action)

    async def stopped(self, gamespace, room_id, **payload):
        logging.info("Room '{0}' died.".format(room_id))
        await self.rooms.remove_room(gamespace, room_id)
        return {}

