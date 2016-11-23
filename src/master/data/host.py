
from tornado.gen import coroutine, Return
import common.database
from common.model import Model


class HostError(Exception):
    pass


class HostNotFound(Exception):
    pass


class HostAdapter(object):
    def __init__(self, data):
        self.host_id = str(data.get("host_id"))
        self.name = data.get("host_name")
        self.internal_location = data.get("internal_location")
        self.geo_location = tuple((data.get("geo_location_x", 0), data.get("geo_location_y", 0)))
        self.default = data.get("host_default", 0)
        self.enabled = data.get("host_enabled", 0) == 1


class HostsModel(Model):
    def __init__(self, db):
        self.db = db

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["hosts"]

    @coroutine
    def new_host(self, name, internal_location, default):

        try:
            host_id = yield self.db.insert(
                """
                INSERT INTO `hosts`
                (`host_name`, `internal_location`, `geo_location`, `host_default`, `host_enabled`)
                VALUES (%s, %s, point(0, 0), %s, %s)
                """, name, internal_location, int(bool(default), 0)
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to create a host: " + e.args[1])
        else:
            raise Return(host_id)

    @coroutine
    def update_host(self, host_id, name, internal_location, default, enabled):
        try:
            yield self.db.execute(
                """
                UPDATE `hosts`
                SET `host_name`=%s, `internal_location`=%s, `host_default`=%s, `host_enabled`=%s
                WHERE `host_id`=%s
                """, name, internal_location, int(bool(default)), int(bool(enabled)), host_id
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to update host: " + e.args[1])

    @coroutine
    def update_host_geo_location(self, host_id, x, y):
        try:
            yield self.db.execute(
                """
                UPDATE `hosts`
                SET `geo_location`=point(%s, %s)
                WHERE `host_id`=%s
                """, x, y, host_id
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to update host geo location: " + e.args[1])

    @coroutine
    def get_host(self, host_id):
        try:
            host = yield self.db.get(
                """
                SELECT *,
                    ST_X(`geo_location`) AS `geo_location_x`,
                    ST_Y(`geo_location`) AS `geo_location_y` FROM `hosts`
                WHERE `host_id`=%s
                LIMIT 1;
                """, host_id
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if host is None:
            raise HostNotFound()

        raise Return(HostAdapter(host))

    @coroutine
    def list_closest_hosts(self, x, y):
        try:
            hosts = yield self.db.query(
                """
                SELECT *,
                    ST_X(`geo_location`) AS `geo_location_x`,
                    ST_Y(`geo_location`) AS `geo_location_y`,
                    ST_Distance(`geo_location`, point(%s, %s)) AS distance
                FROM `hosts`
                ORDER BY distance ASC;
                """, x, y
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        raise Return(map(HostAdapter, hosts))

    @coroutine
    def get_default_host(self):
        try:
            host = yield self.db.get(
                """
                SELECT *,
                    ST_X(`geo_location`) AS `geo_location_x`,
                    ST_Y(`geo_location`) AS `geo_location_y`
                FROM `hosts`
                WHERE `host_default`=1 AND `host_enabled`=1
                LIMIT 1;
                """
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if host is None:
            raise HostNotFound()

        raise Return(HostAdapter(host))

    @coroutine
    def get_closest_host(self, x, y):
        try:
            host = yield self.db.get(
                """
                SELECT *,
                    ST_X(`geo_location`) AS `geo_location_x`,
                    ST_Y(`geo_location`) AS `geo_location_y`,
                    ST_Distance(`geo_location`, point(%s, %s)) AS distance
                FROM `hosts`
                WHERE `host_enabled`=1
                ORDER BY distance ASC
                LIMIT 1;
                """, x, y
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if host is None:
            raise HostNotFound()

        raise Return(HostAdapter(host))

    @coroutine
    def list_hosts(self):
        try:
            hosts = yield self.db.query(
                """
                SELECT *,
                    ST_X(`geo_location`) AS `geo_location_x`,
                    ST_Y(`geo_location`) AS `geo_location_y`
                FROM `hosts`;
                """
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        raise Return(map(HostAdapter, hosts))

    @coroutine
    def delete_host(self, host_id):

        try:
            yield self.db.execute(
                """
                DELETE FROM `hosts`
                WHERE `host_id`=%s
                """, host_id
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to delete a server: " + e.args[1])
