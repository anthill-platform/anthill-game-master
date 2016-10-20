
from common.options import options

import common.server
import common.access
import common.sign
import handlers as h

from data.servers import GameServersData
from data.room import RoomsData

import admin
import options as _opts


class GameControllerServer(common.server.Server):
    # noinspection PyShadowingNames
    def __init__(self):
        super(GameControllerServer, self).__init__()

        self.gs_host = options.gs_host

        self.gs = GameServersData(
            self,
            sock_path=options.sock_path,
            binaries_path=options.binaries_path,
            ports_pool_from=options.ports_pool_from,
            ports_pool_to=options.ports_pool_to)

        self.rooms = RoomsData(self)

    def get_internal_handler(self):
        return h.InternalHandler(self)

    def get_handlers(self):
        return [
            (r"/spawn", h.SpawnHandler)
        ]

    def get_admin_stream(self):
        return {
            "debug": admin.DebugController
        }

    def get_gs_host(self):
        return self.gs_host

if __name__ == "__main__":
    stt = common.server.init()
    common.access.AccessToken.init([common.access.public()])
    common.server.start(GameControllerServer)
