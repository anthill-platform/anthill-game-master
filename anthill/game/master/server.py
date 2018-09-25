
from anthill.common import database, keyvalue, server, ratelimit, access
from anthill.common.options import options

from . import admin
from . import handlers as h
from . import options as _opts

from . model.gameserver import GameServersModel
from . model.room import RoomsModel
from . model.controller import ControllersClientModel
from . model.host import HostsModel
from . model.deploy import DeploymentModel
from . model.ban import BansModel
from . model.heartbeat import HeartbeatModel
from . model.party import PartyModel


class GameMasterServer(server.Server):
    # noinspection PyShadowingNames
    def __init__(self):
        super(GameMasterServer, self).__init__()

        self.db = database.Database(
            host=options.db_host,
            database=options.db_name,
            user=options.db_username,
            password=options.db_password)

        self.cache = keyvalue.KeyValueStorage(
            host=options.cache_host,
            port=options.cache_port,
            db=options.cache_db,
            max_connections=options.cache_max_connections)

        self.gameservers = GameServersModel(self.db)
        self.hosts = HostsModel(self.db)
        self.rooms = RoomsModel(self, self.db, self.hosts)
        self.deployments = DeploymentModel(self.db)
        self.bans = BansModel(self.db)
        self.heartbeat = HeartbeatModel(self, self.db)

        self.ctl_client = ControllersClientModel(self.rooms, self.deployments)

        self.ratelimit = ratelimit.RateLimit({
            "create_room": options.rate_create_room
        })

        self.parties = PartyModel(self.db, self.gameservers, self.deployments,
                                  self.ratelimit, self.hosts, self.rooms)

    def get_models(self):
        return [self.hosts, self.rooms, self.gameservers, self.deployments, self.bans, self.heartbeat, self.parties]

    def get_admin(self):
        return {
            "index": admin.RootAdminController,
            "app": admin.ApplicationController,
            "app_version": admin.ApplicationVersionController,
            "deploy": admin.DeployApplicationController,
            "deployment": admin.ApplicationDeploymentController,
            "rooms": admin.RoomsController,
            "room": admin.RoomController,
            "spawn_room": admin.SpawnRoomController,

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
            "find_active_ban": admin.FindBanController,
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
            (r"/regions", h.RegionsHandler),
            (r"/parties/(.*)/(.*)/(.*)/session", h.PartiesSearchHandler),
            (r"/party/create/(.*)/(.*)/(.*)/session", h.CreatePartySessionHandler),
            (r"/party/create/(.*)/(.*)/(.*)", h.CreatePartySimpleHandler),
            (r"/party/(.*)/session", h.PartySessionHandler),
            (r"/party/(.*)", h.SimplePartyHandler),
            (r"/ban/issue", h.IssueBanHandler),
            (r"/ban/find/(.*)", h.FindBanHandler),
            (r"/ban/(.*)", h.BanHandler),
        ]


if __name__ == "__main__":
    stt = server.init()

    access.AccessToken.init([access.public()])
    server.start(GameMasterServer)
