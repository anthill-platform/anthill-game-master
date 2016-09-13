
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

from data.game import GamesModel
from data.room import RoomsModel
from data.controller import ControllersClientModel
from data.server import ServersModel

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

        self.games = GamesModel(self.db)
        self.rooms = RoomsModel(self.db)
        self.servers = ServersModel(self.db)

        self.ctl_client = ControllersClientModel(self.rooms)

        self.ratelimit = common.ratelimit.RateLimit({
            "create_room": options.rate_create_room
        })

    def get_admin(self):
        return {
            "index": admin.RootAdminController,
            "apps": admin.ApplicationsController,
            "app": admin.ApplicationController,
            "app_settings": admin.ApplicationSettingsController,
            "app_version": admin.ApplicationVersionController,

            "servers": admin.ServersController,
            "server": admin.ServerController,
            "debug_server": admin.DebugServerController,
            "new_server": admin.NewServerController
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
            "icon": "rocket"
        }

    def get_handlers(self):
        return [
            (r"/config", h.ConfigHandler),
            (r"/rooms/(.*)/(.*)", h.RoomsHandler),
            (r"/join/(.*)/(.*)", h.JoinHandler),
        ]


if __name__ == "__main__":
    stt = common.server.init()

    common.access.AccessToken.init([common.access.public()])
    common.server.start(GameMasterServer)
