
from tornado.gen import coroutine, Return
from common.options import options

import admin
import handlers as h
import common.access
import common.database
import common.environment
import common.keyvalue
import common.server
import common.sign
import common.ratelimit

from model.gameserver import GameServersModel
from model.room import RoomsModel
from model.controller import ControllersClientModel
from model.host import HostsModel
from model.deploy import DeploymentModel
from model.ban import BansModel
from model.heartbeat import HeartbeatModel

import options as _opts


class GameMasterServer(common.server.Server):
    # noinspection PyShadowingNames
    def __init__(self):
        super(GameMasterServer, self).__init__()

        self.db = common.database.Database(
            host=options.db_host,
            database=options.db_name,
            user=options.db_username,
            password=options.db_password)

        self.cache = common.keyvalue.KeyValueStorage(
            host=options.cache_host,
            port=options.cache_port,
            db=options.cache_db,
            max_connections=options.cache_max_connections)

        self.env_service = common.environment.EnvironmentClient(self.cache)

        self.gameservers = GameServersModel(self.db)
        self.hosts = HostsModel(self.db)
        self.rooms = RoomsModel(self.db, self.hosts)
        self.deployments = DeploymentModel(self.db)
        self.bans = BansModel(self.db)
        self.heartbeat = HeartbeatModel(self, self.db)

        self.ctl_client = ControllersClientModel(self.rooms, self.deployments)

        self.ratelimit = common.ratelimit.RateLimit({
            "create_room": options.rate_create_room
        })

    def get_models(self):
        return [self.hosts, self.rooms, self.gameservers, self.deployments, self.bans, self.heartbeat]

    def get_admin(self):
        return {
            "index": admin.RootAdminController,
            "app": admin.ApplicationController,
            "app_version": admin.ApplicationVersionController,
            "deploy": admin.DeployApplicationController,
            "deployment": admin.ApplicationDeploymentController,
            "rooms": admin.RoomsController,
            "room": admin.RoomController,

            "game_server": admin.GameServerController,
            "new_game_server": admin.NewGameServerController,
            "game_server_version": admin.GameServerVersionController,

            "host": admin.HostController,
            "hosts": admin.HostsController,
            "debug_host": admin.DebugHostController,
            "new_host": admin.NewHostController,

            "region": admin.RegionController,
            "new_region": admin.NewRegionController,

            "new_ban": admin.IssueBanController,
            "mass_ban": admin.IssueMultipleBansController,
            "find_ban": admin.FindBanController,
            "ban": admin.BanController
        }

    def get_admin_stream(self):
        return {
            "debug_controller": admin.DebugControllerAction
        }

    def get_internal_handler(self):
        return h.InternalHandler(self)

    def get_metadata(self):
        return {
            "title": "Game",
            "description": "Manage game server instances",
            "icon": "gamepad"
        }

    def get_handlers(self):
        return [
            (r"/rooms/(.*)/(.*)/(.*)", h.RoomsHandler),
            (r"/room/(.*)/(.*)/join", h.JoinRoomHandler),
            (r"/join/multi/(.*)/(.*)/(.*)", h.JoinMultiHandler),
            (r"/join/(.*)/(.*)/(.*)", h.JoinHandler),
            (r"/create/multi/(.*)/(.*)/(.*)", h.CreateMultiHandler),
            (r"/create/(.*)/(.*)/(.*)", h.CreateHandler),
            (r"/status", h.StatusHandler),
            (r"/players", h.MultiplePlayersRecordsHandler),
            (r"/player/(.*)", h.PlayerRecordsHandler),
            (r"/regions", h.RegionsHandler)
        ]


if __name__ == "__main__":
    stt = common.server.init()

    common.access.AccessToken.init([common.access.public()])
    common.server.start(GameMasterServer)
