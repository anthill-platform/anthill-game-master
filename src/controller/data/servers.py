
from tornado.gen import coroutine, Return
import tornado.ioloop

import logging
import os
import server
import common.events
import random
import datetime


class GameServersData(object):
    def __init__(self, app,
                 sock_path="/tmp",
                 binaries_path="/opt/gs",
                 ports_pool_from=2000,
                 ports_pool_to=3000):

        self.app = app
        self.sock_path = sock_path
        self.binaries_path = binaries_path

        self.pool = PortsPool(ports_pool_from, ports_pool_to)
        self.servers = {}
        self.sub = common.events.Subscriber(self)
        self.pub = common.events.Publisher()

    def get_server(self, name):
        return self.servers.get(name, None)

    def get_servers(self):
        return self.servers

    @coroutine
    def instantiate(self, name, game_id, game_version, room):
        gs = server.GameServer(self, game_id, game_version, name, room)
        self.servers[name] = gs

        self.sub.subscribe(gs.pub, ["server_status"])
        self.pub.notify("new_server", server=gs)

        raise Return(gs)

    @coroutine
    def server_status(self, name, status):
        self.pub.notify("server_status", name=name, status=status)

    @coroutine
    def spawn(self, game_id, game_version, room):
        name = "game_" + game_id + "_" + str(room.id())

        game_settings = room.game_settings()

        try:
            binary = game_settings["binary"]
            arguments = game_settings["arguments"]
        except (KeyError, ValueError) as e:
            raise server.SpawnError("Failed to spawn game server: " + e.message)

        instance = yield self.instantiate(name, game_id, game_version, room)

        app_path = os.path.join(self.binaries_path, game_id, game_version)
        sock_path = os.path.join(self.sock_path, name)

        try:
            init = yield instance.spawn(app_path, binary, sock_path, arguments, game_settings)
        except server.SpawnError as e:
            logging.error("Failed to spawn server instance: " + e.message)
            raise e

        logging.info("New server instance spawned: " + name)

        result = {
            "host": self.app.get_gs_host(),
            "ports": instance.ports,
            "init": init or {}
        }

        raise Return(result)

    @coroutine
    def stopped(self, instance):
        self.sub.unsubscribe(instance.pub, ["server_status"])
        self.pub.notify("server_removed", server=instance)

        def remove_server():
            self.servers.pop(instance.name)

        yield instance.release()

        tornado.ioloop.IOLoop.current().add_timeout(datetime.timedelta(minutes=10), remove_server)

    @coroutine
    def terminate_all(self, kill=False):
        for s in self.servers:
            yield s.terminate(kill=kill)


class PoolError(Exception):
    def __init__(self, message):
        self.message = message


class PortsPool(object):
    def __init__(self, port_from, port_to):
        self.ports = list(range(port_from, port_to))

    def acquire(self):
        try:
            return self.ports.pop(random.randrange(len(self.ports)))
        except KeyError:
            raise PoolError("No ports in pool left")

    def put(self, port):
        self.ports.append(port)
