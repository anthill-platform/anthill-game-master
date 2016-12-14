
from tornado.gen import coroutine, Return
import common.database
from common.model import Model


class HostError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class RegionError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class RegionNotFound(Exception):
    pass


class HostNotFound(Exception):
    pass


class HostAdapter(object):
    def __init__(self, data):
        self.host_id = str(data.get("host_id"))
        self.name = data.get("host_name")
        self.internal_location = data.get("internal_location")
        self.region = data.get("host_region")
        self.enabled = data.get("host_enabled", 0) == 1

        self.memory = int(data.get("host_memory"))
        self.heartbeat = data.get("host_heartbeat")
        self.cpu = int(data.get("host_cpu"))
        self.load = int(data.get("host_load", 0) * 100.0)

        self.state = data.get("host_state", "ERROR")
        self.active = self.state == "ACTIVE"


class RegionAdapter(object):
    def __init__(self, data):
        self.region_id = str(data.get("region_id"))
        self.name = data.get("region_name")
        self.default = data.get("region_default", 0)
        self.geo_location = tuple((data.get("region_location_x", 0), data.get("region_location_y", 0)))


class HostsModel(Model):
    def __init__(self, db):
        self.db = db

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["hosts", "regions"]

    @coroutine
    def new_region(self, name, default):
        try:
            region_id = yield self.db.insert(
                """
                INSERT INTO `regions`
                (`region_name`, `region_location`, `region_default`)
                VALUES (%s, point(0, 0), %s);
                """, name, int(bool(default))
            )
        except common.database.DatabaseError as e:
            raise RegionError("Failed to create a region: " + e.args[1])
        else:
            raise Return(region_id)

    @coroutine
    def get_region(self, region_id):
        try:
            region = yield self.db.get(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`
                FROM `regions`
                WHERE `region_id`=%s
                LIMIT 1;
                """, region_id
            )
        except common.database.DatabaseError as e:
            raise RegionError("Failed to get region: " + e.args[1])

        if region is None:
            raise RegionNotFound()

        raise Return(RegionAdapter(region))

    @coroutine
    def get_best_host(self, region_id):
        try:
            host = yield self.db.get(
                """
                SELECT *
                FROM `hosts`
                WHERE `host_region`=%s AND `host_state`='ACTIVE'
                ORDER BY `host_load` ASC
                LIMIT 1;
                """, region_id
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get host: " + e.args[1])

        if host is None:
            raise HostNotFound()

        raise Return(HostAdapter(host))

    @coroutine
    def get_closest_region(self, x, y):
        try:
            region = yield self.db.get(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`,
                    ST_Distance(`region_location`, point(%s, %s)) AS distance
                FROM `regions`
                ORDER BY distance ASC
                LIMIT 1;
                """, x, y
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if region is None:
            raise RegionNotFound()

        raise Return(RegionAdapter(region))

    @coroutine
    def list_closest_regions(self, x, y):
        try:
            hosts = yield self.db.query(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`,
                    ST_Distance(`region_location`, point(%s, %s)) AS distance
                FROM `regions`
                ORDER BY distance ASC;
                """, x, y
            )
        except common.database.DatabaseError as e:
            raise RegionError("Failed to get server: " + e.args[1])

        raise Return(map(RegionAdapter, hosts))

    @coroutine
    def list_regions(self):
        try:
            regions = yield self.db.query(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`
                FROM `regions`;
                """
            )
        except common.database.DatabaseError as e:
            raise RegionError("Failed to list regions: " + e.args[1])

        raise Return(map(RegionAdapter, regions))

    @coroutine
    def get_default_region(self):
        try:
            region = yield self.db.get(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`
                FROM `regions`
                WHERE `region_default`=1
                LIMIT 1;
                """
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if region is None:
            raise RegionNotFound()

        raise Return(RegionAdapter(region))

    @coroutine
    def update_region(self, region_id, name, default):
        try:
            yield self.db.execute(
                """
                UPDATE `regions`
                SET `region_name`=%s, `region_default`=%s
                WHERE `region_id`=%s;
                """, name, int(bool(default)), region_id
            )
        except common.database.DatabaseError as e:
            raise RegionError("Failed to update region: " + e.args[1])

    @coroutine
    def update_region_geo_location(self, region_id, x, y):
        try:
            yield self.db.execute(
                """
                UPDATE `regions`
                SET `region_location`=point(%s, %s)
                WHERE `region_id`=%s
                """, x, y, region_id
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to update host geo location: " + e.args[1])

    @coroutine
    def delete_region(self, region_id):
        try:
            yield self.db.execute(
                """
                DELETE FROM `regions`
                WHERE `region_id`=%s
                """, region_id
            )
        except common.database.ConstraintsError:
            raise RegionError("Dependent host exists")
        except common.database.DatabaseError as e:
            raise RegionError("Failed to delete a region: " + e.args[1])

    @coroutine
    def new_host(self, name, internal_location, region, enabled=True):

        try:
            host_id = yield self.db.insert(
                """
                INSERT INTO `hosts`
                (`host_name`, `internal_location`, `host_region`,  `host_enabled`)
                VALUES (%s, %s, %s, %s)
                """, name, internal_location, region, int(bool(enabled))
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to create a host: " + e.args[1])
        else:
            raise Return(host_id)

    @coroutine
    def update_host(self, host_id, name, internal_location, enabled):
        try:
            yield self.db.execute(
                """
                UPDATE `hosts`
                SET `host_name`=%s, `internal_location`=%s, `host_enabled`=%s
                WHERE `host_id`=%s
                """, name, internal_location, int(bool(enabled)), host_id
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to update host: " + e.args[1])

    @coroutine
    def update_host_load(self, host_id, memory, cpu, state='ACTIVE', db=None):

        total_load = max(memory, cpu) / 100.0

        try:
            yield (db or self.db).execute(
                """
                UPDATE `hosts`
                SET `host_load`=%s, `host_memory`=%s, `host_cpu`=%s, `host_state`=%s,
                    `host_heartbeat`=NOW(),
                    `host_processing`=0
                WHERE `host_id`=%s
                """, total_load, memory, cpu, state, host_id)
        except common.database.DatabaseError as e:
            raise HostError("Failed to update host load: " + e.args[1])

    @coroutine
    def find_host(self, host_name):
        try:
            host = yield self.db.get(
                """
                SELECT *
                FROM `hosts`
                WHERE `host_name`=%s
                LIMIT 1;
                """, host_name
            )
        except common.database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if host is None:
            raise HostNotFound()

        raise Return(HostAdapter(host))

    @coroutine
    def get_host(self, host_id):
        try:
            host = yield self.db.get(
                """
                SELECT *
                FROM `hosts`
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
    def list_enabled_hosts(self):
        try:
            hosts = yield self.db.query(
                """
                SELECT *
                FROM `hosts`
                WHERE `host_enabled`=1;
                """)
        except common.database.DatabaseError as e:
            raise HostError("Failed to get hosts: " + e.args[1])

        raise Return(map(HostAdapter, hosts))

    @coroutine
    def list_hosts(self, region_id=None):
        try:
            if region_id:
                hosts = yield self.db.query(
                    """
                    SELECT *
                    FROM `hosts`
                    WHERE `host_region`=%s;
                    """, region_id)
            else:
                hosts = yield self.db.query(
                    """
                    SELECT *
                    FROM `hosts`;
                    """)
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
