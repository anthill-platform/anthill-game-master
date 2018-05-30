
from tornado.ioloop import PeriodicCallback
from tornado.gen import coroutine, Return

from common.model import Model
from common.options import options
from common.internal import Internal, InternalError
from common import to_int

from host import HostAdapter

import logging


class HeartbeatReport(object):
    def __init__(self, data):

        load = data.get("load", {})

        self.memory = to_int(load.get("memory", 999))
        self.cpu = to_int(load.get("cpu", 999))
        self.rooms = data.get("rooms", [])


class HeartbeatError(Exception):
    pass


class HeartbeatModel(Model):

    MEMORY_OVERLOAD = 95

    def __init__(self, app, db):

        self.app = app
        self.db = db
        self.update_cb = PeriodicCallback(self.update, options.heartbeat_time * 1000)
        self.internal = Internal()
        self.processing = False

    @coroutine
    def started(self, application):
        yield super(HeartbeatModel, self).started(application)
        self.update_cb.start()

    @coroutine
    def stopped(self):
        self.update_cb.stop()

    @coroutine
    def __check_host__(self, host):
        logging.debug("Checking host {0} ({1})".format(host.host_id, host.internal_location))

        try:
            report = yield self.internal.get(
                host.internal_location,
                "heartbeat",
                {},
                use_json=True,
                discover_service=False,
                timeout=5)
        except InternalError as e:
            logging.warning("Failed to heartbeat host {0}: {1}".format(
                host.host_id, str(e)
            ))

            self.app.monitor_rate("heartbeats", "failed")

            raise HeartbeatError()
        else:
            raise Return(HeartbeatReport(report))

    @coroutine
    def update(self):

        if self.processing:
            return

        self.processing = True

        with (yield self.db.acquire()) as db:

            try:
                hosts = yield db.query(
                    """
                        SELECT * FROM `hosts`
                        WHERE `host_enabled`=1;
                    """)

                hosts = map(HostAdapter, hosts)

                if hosts:
                    failed = []

                    for host in hosts:
                        # noinspection PyBroadException
                        try:
                            # process hosts one by one
                            report = yield self.__check_host__(host)
                        except:
                            failed.append(host.host_id)
                        else:
                            if report.memory > HeartbeatModel.MEMORY_OVERLOAD:
                                state = 'OVERLOAD'
                            else:
                                state = 'ACTIVE'

                            # update load in case of success
                            yield self.app.hosts.update_host_load(host.host_id, report.memory, report.cpu, state, db=db)

                            # delete rooms not listed in that list
                            yield self.app.rooms.remove_host_rooms(host.host_id, except_rooms=report.rooms)

                    if failed:
                        yield db.execute(
                            """
                                UPDATE `hosts`
                                SET `host_state`='ERROR'
                                WHERE `host_id` IN %s;
                            """, failed)
            finally:
                self.processing = False
