
from tornado.gen import coroutine, Return, sleep, with_timeout, Task, TimeoutError

import tornado.ioloop

import os
import asyncproc
import logging
import signal
import msg
import datetime
import common.events
import common.jsonrpc
import common.discover

from common.discover import DiscoveryError
from common.internal import Internal, InternalError

import ujson


class BufferedLog(object):
    COLLECT_TIME = 2

    def __init__(self, callback):
        self.buffer = []
        self.callback = callback
        self.log = ""

    def add(self, data):
        if not self.buffer:
            tornado.ioloop.IOLoop.current().add_timeout(
                datetime.timedelta(seconds=BufferedLog.COLLECT_TIME), self.flush)
        self.buffer.append(data)

    def get_log(self):
        return self.log

    def flush(self):
        if self.buffer:
            data = "\n".join(self.buffer) + "\n"
            self.log += data
            self.callback(data)
            self.buffer = []


class LineStream:
    def __init__(self):
        self.stream = ""

    def add(self, data, callback):

        if data is "":
            return

        self.stream += data

        while True:
            index = self.stream.find("\n")
            if index >= 0:
                string = self.stream[:index]
                self.stream = self.stream[index + 1:]
                callback(string.replace("\n", "<br>"))
            else:
                break


class SpawnError(Exception):
    def __init__(self, message):
        self.message = message


class GameServer(object):
    STATUS_LOADING = "loading"
    STATUS_INITIALIZING = "initializing"
    STATUS_STOPPED = "stopped"
    STATUS_RUNNING = "running"
    STATUS_ERROR = "error"
    STATUS_NONE = "none"

    SPAWN_TIMEOUT = 30
    CHECK_PERIOD = 60

    def __init__(self, gs, game_id, game_version, name, room):
        self.gs = gs

        self.game_id = game_id
        self.game_version = game_version

        self.name = name
        self.room = room
        self.ioloop = tornado.ioloop.IOLoop.instance()
        self.pipe = None
        self.status = GameServer.STATUS_NONE
        self.msg = None
        self.on_stopped = None
        self.pub = common.events.Publisher()

        # message handlers
        self.handlers = {}

        # and common game config
        game_settings = room.game_settings()

        ports_num = game_settings.get("ports", 1)
        self.ports = []

        # get ports from the pool
        for i in xrange(0, ports_num):
            self.ports.append(gs.pool.acquire())

        self.check_period = game_settings.get("check_period", GameServer.CHECK_PERIOD)

        self.str_data = LineStream()
        self.err_data = LineStream()
        self.log = BufferedLog(self.__flush_log__)

    def is_running(self):
        return self.status == GameServer.STATUS_RUNNING

    def set_status(self, status):
        self.status = status
        self.log.flush()
        self.pub.notify("server_status", name=self.name, status=status)

    @coroutine
    def __check_status__(self):
        while self.is_running():
            yield sleep(self.check_period)
            # is_running could change while we've been sleeping
            if self.is_running():
                try:
                    response = yield self.msg.request(self, "status")
                except common.jsonrpc.JsonRPCTimeout:
                    self.__notify__("Timeout to check status")
                    yield self.terminate(False)
                else:
                    status = response.get("status", "bad")
                    self.__notify__("Status: " + status)
                    if status != "ok":
                        self.__notify__("Bad status")
                        yield self.terminate(False)

    @coroutine
    def inited(self):
        self.__notify__("Inited.")
        self.set_status(GameServer.STATUS_RUNNING)

        tornado.ioloop.IOLoop.current().spawn_callback(self.__check_status__)

        # return the game settings to the game server that called the 'inited' callback
        server_settings = self.room.server_settings()
        raise Return(server_settings)

    @coroutine
    def __prepare__(self, game_settings):
        env = {
            "gamespace_id": str(self.room.gamespace)
        }

        token = game_settings.get("token", {})
        authenticate = token.get("authenticate", False)

        if authenticate:
            self.__notify__("Authenticating for server-side use.")

            username = token.get("username")
            password = token.get("password")
            scopes = token.get("scopes", "")

            if not username:
                raise SpawnError("No 'token.username' field.")

            internal = Internal()

            try:
                access_token = yield internal.request(
                    "login", "authenticate",
                    credential="dev", username=username, key=password, scopes=scopes,
                    gamespace_id=self.room.gamespace, unique="false")
            except InternalError as e:
                raise SpawnError("Failed to authenticate for server-side access token: " + str(e.code) + ": " + e.body)
            else:
                self.__notify__("Authenticated for server-side use!")
                env["access_token"] = access_token["token"]

        discover = game_settings.get("discover", [])

        if discover:
            self.__notify__("Discovering services for server-side use.")

            try:
                services = yield common.discover.cache.get_services(discover, network="external")
            except DiscoveryError as e:
                raise SpawnError("Failed to discover services for server-side use: " + e.message)
            else:
                env["services"] = ujson.dumps(services, escape_forward_slashes=False)

        raise Return(env)

    @coroutine
    def spawn(self, path, binary, sock_path, cmd_arguments, game_settings):

        yield self.listen(sock_path)

        env = yield self.__prepare__(game_settings)

        arguments = [
            # application binary
            os.path.join(path, binary),
            # first the socket
            sock_path,
            # then the ports
            ",".join(str(port) for port in self.ports)
        ]
        # and then custom arguments
        arguments.extend(cmd_arguments)

        cmd = " ".join(arguments)
        self.__notify__("Spawning: " + cmd)

        self.__notify__("Environment:")

        for name, value in env.iteritems():
            self.__notify__("  " + name + " = " + value + ";")

        self.set_status(GameServer.STATUS_INITIALIZING)

        try:
            self.pipe = asyncproc.Process(cmd, shell=True, cwd=path, preexec_fn=os.setsid, env=env)
        except OSError as e:
            reason = "Failed to spawn a server: " + e.args[1]
            self.__notify__(reason)
            yield self.__stopped__()

            raise SpawnError(reason)
        else:
            self.set_status(GameServer.STATUS_LOADING)
            self.ioloop.add_callback(self.__recv__)

        self.__notify__("Server '{0}' spawned, waiting for init command.".format(self.name))

        def wait(callback):
            @coroutine
            def stopped(*args, **kwargs):
                self.__clear_handle__("stopped")
                callback(SpawnError("Stopped before 'inited' command received."))

            @coroutine
            def inited(*args, **kwargs):
                self.__clear_handle__("inited")
                self.__clear_handle__("stopped")

                # call it, the message will be passed
                callback(*args, **kwargs)

                # we're done initializing
                res_ = yield self.inited()
                raise Return(res_)

            # catch the init message
            self.__handle__("inited", inited)
            # and the stopped (if one)
            self.__handle__("stopped", stopped)

        # wait, until the 'init' command is received
        # or, the server is stopped (that's bad) earlier
        try:
            result = yield with_timeout(
                datetime.timedelta(seconds=GameServer.SPAWN_TIMEOUT),
                Task(wait))

            # if the result is an Exception, that means
            # the 'wait' told us so
            if isinstance(result, Exception):
                raise result

            raise Return(result)
        except TimeoutError:
            self.__notify__("Timeout to spawn.")
            yield self.terminate(True)
            raise SpawnError("Failed to spawn a game server: timeout")

    @coroutine
    def terminate(self, kill=False):
        self.__notify__("Terminating... (kill={0})".format(kill))

        try:
            self.pipe.kill(signal.SIGKILL if kill else signal.SIGTERM)
        except OSError as e:
            self.__notify__("Server terminate error: " + e.args[1])
            if not kill:
                yield self.terminate(kill=True)

    def get_log(self):
        return self.log.get_log()

    def __recv__(self):
        if self.status in [GameServer.STATUS_STOPPED]:
            return

        self.err_data.add(self.pipe.readerr(), self.__notify__)
        self.str_data.add(self.pipe.read(), self.__notify__)

        poll = self.pipe.wait(os.WNOHANG)
        if poll is None:
            self.ioloop.add_callback(self.__recv__)
        else:
            self.ioloop.spawn_callback(self.__stopped__)

    @coroutine
    def __stopped__(self):
        self.__notify__("Stopped.")
        self.set_status(GameServer.STATUS_STOPPED)

        # notify the master server that this server is died
        yield self.command(self, "stopped")

        yield self.gs.stopped(self)
        yield self.release()

    @coroutine
    def release(self):
        # put back the ports acquired at spawn
        for port in self.ports:
            self.gs.pool.put(port)
        self.ports = []

    def __flush_log__(self, data):
        self.pub.notify("log", name=self.name, data=data)
        logging.info("[{0}] {1}".format(self.name, data))

    def __notify__(self, data):
        self.log.add(data)

    def __handle__(self, action, handlers):
        self.handlers[action] = handlers

    def __clear_handle__(self, action):
        self.handlers.pop(action)

    @coroutine
    def command(self, context, method, *args, **kwargs):
        if method in self.handlers:
            # if this action is registered
            # inside of the internal handlers
            # then catch it
            response = yield self.handlers[method](*args, **kwargs)
        else:
            response = yield self.room.notify(method, *args, **kwargs)

        raise Return(response or {})

    @coroutine
    def listen(self, sock_path):
        self.msg = msg.ProcessMessages(path=sock_path)
        self.msg.set_receive(self.command)
        yield self.msg.server()

