
from anthill.common import database
from anthill.common.model import Model
from anthill.common.validate import validate

import ujson


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
        self.region_id = str(data.get("host_region"))
        self.name = data.get("host_name")
        self.internal_location = data.get("internal_location")
        self.region = data.get("host_region")
        self.enabled = data.get("host_enabled", 0) == 1

        self.memory = int(data.get("host_memory"))
        self.heartbeat = data.get("host_heartbeat")
        self.cpu = int(data.get("host_cpu"))
        self.load = int(data.get("host_load", 0) * 100.0)

        self.state = data.get("host_state", "ERROR")
        self.active = self.state in ["ACTIVE", "OVERLOAD"]


class RegionAdapter(object):
    def __init__(self, data):
        self.region_id = str(data.get("region_id"))
        self.name = data.get("region_name")
        self.default = data.get("region_default", 0)
        self.settings = data.get("region_settings", {})
        self.geo_location = tuple((data.get("region_location_x", 0), data.get("region_location_y", 0)))


class HostsModel(Model):
    def __init__(self, db):
        self.db = db

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["regions", "hosts"]

    async def setup_table_regions(self):
        await self.new_region("local", True, {})

    async def setup_table_hosts(self):

        try:
            region = await self.find_region("local")
        except RegionNotFound:
            pass
        else:
            await self.new_host("localhost", "http://localhost:9509", region.region_id, True)

    @validate(name="str_name", default="bool", settings="json")
    async def new_region(self, name, default, settings):
        try:
            region_id = await self.db.insert(
                """
                INSERT INTO `regions`
                (`region_name`, `region_location`, `region_default`, `region_settings`)
                VALUES (%s, point(0, 0), %s, %s);
                """, name, int(bool(default)), ujson.dumps(settings)
            )
        except database.DatabaseError as e:
            raise RegionError("Failed to create a region: " + e.args[1])
        else:
            return region_id

    async def get_region(self, region_id):
        try:
            region = await self.db.get(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`
                FROM `regions`
                WHERE `region_id`=%s
                LIMIT 1;
                """, region_id
            )
        except database.DatabaseError as e:
            raise RegionError("Failed to get region: " + e.args[1])

        if region is None:
            raise RegionNotFound()

        return RegionAdapter(region)

    @validate(region_name="str_name")
    async def find_region(self, region_name):
        try:
            region = await self.db.get(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`
                FROM `regions`
                WHERE `region_name`=%s
                LIMIT 1;
                """, region_name
            )
        except database.DatabaseError as e:
            raise RegionError("Failed to get region: " + e.args[1])

        if region is None:
            raise RegionNotFound()

        return RegionAdapter(region)

    async def get_best_host(self, region_id):
        try:
            host = await self.db.get(
                """
                SELECT *
                FROM `hosts`
                WHERE `host_region`=%s AND `host_enabled`=1 AND `host_state`='ACTIVE'
                ORDER BY `host_load` ASC
                LIMIT 1;
                """, region_id
            )
        except database.DatabaseError as e:
            raise HostError("Failed to get host: " + e.args[1])

        if host is None:
            raise HostNotFound()

        return HostAdapter(host)

    async def get_closest_region(self, p_long, p_lat):
        try:
            region = await self.db.get(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`,
                    ST_Distance_Sphere(`region_location`, point(%s, %s)) AS distance
                FROM `regions`
                ORDER BY distance ASC
                LIMIT 1;
                """, p_long, p_lat
            )
        except database.DatabaseError as e:
            raise RegionError("Failed to get closest region: " + e.args[1])

        if region is None:
            raise RegionNotFound()

        return RegionAdapter(region)

    async def list_closest_regions(self, p_long, p_lat):
        try:
            hosts = await self.db.query(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`,
                    ST_Distance_Sphere(`region_location`, point(%s, %s)) AS distance
                FROM `regions`
                ORDER BY distance ASC;
                """, p_long, p_lat
            )
        except database.DatabaseError as e:
            raise RegionError("Failed to get server: " + e.args[1])

        return list(map(RegionAdapter, hosts))

    async def list_regions(self):
        try:
            regions = await self.db.query(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`
                FROM `regions`;
                """
            )
        except database.DatabaseError as e:
            raise RegionError("Failed to list regions: " + e.args[1])

        return list(map(RegionAdapter, regions))

    async def get_default_region(self):
        try:
            region = await self.db.get(
                """
                SELECT *,
                    ST_X(`region_location`) AS `region_location_x`,
                    ST_Y(`region_location`) AS `region_location_y`
                FROM `regions`
                WHERE `region_default`=1
                LIMIT 1;
                """
            )
        except database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if region is None:
            raise RegionNotFound()

        return RegionAdapter(region)

    @validate(region_id="int", name="str_name", default="bool", setting="json")
    async def update_region(self, region_id, name, default, settings):
        try:
            await self.db.execute(
                """
                UPDATE `regions`
                SET `region_name`=%s, `region_default`=%s, `region_settings`=%s
                WHERE `region_id`=%s;
                """, name, int(bool(default)), ujson.dumps(settings), region_id
            )
        except database.DatabaseError as e:
            raise RegionError("Failed to update region: " + e.args[1])

    async def update_region_geo_location(self, region_id, p_long, p_lat):
        try:
            await self.db.execute(
                """
                UPDATE `regions`
                SET `region_location`=point(%s, %s)
                WHERE `region_id`=%s
                """, p_long, p_lat, region_id
            )
        except database.DatabaseError as e:
            raise HostError("Failed to update host geo location: " + e.args[1])

    async def delete_region(self, region_id):
        try:
            await self.db.execute(
                """
                DELETE FROM `regions`
                WHERE `region_id`=%s
                """, region_id
            )
        except database.ConstraintsError:
            raise RegionError("Dependent host exists")
        except database.DatabaseError as e:
            raise RegionError("Failed to delete a region: " + e.args[1])

    async def new_host(self, name, internal_location, region, enabled=True):

        try:
            host_id = await self.db.insert(
                """
                INSERT INTO `hosts`
                (`host_name`, `internal_location`, `host_region`,  `host_enabled`)
                VALUES (%s, %s, %s, %s)
                """, name, internal_location, region, int(bool(enabled))
            )
        except database.DatabaseError as e:
            raise HostError("Failed to create a host: " + e.args[1])
        else:
            return host_id

    async def update_host(self, host_id, name, internal_location, enabled):
        try:
            await self.db.execute(
                """
                UPDATE `hosts`
                SET `host_name`=%s, `internal_location`=%s, `host_enabled`=%s
                WHERE `host_id`=%s
                """, name, internal_location, int(bool(enabled)), host_id
            )
        except database.DatabaseError as e:
            raise HostError("Failed to update host: " + e.args[1])

    async def update_host_load(self, host_id, memory, cpu, state='ACTIVE', db=None):

        total_load = max(memory, cpu) / 100.0

        try:
            await (db or self.db).execute(
                """
                UPDATE `hosts`
                SET `host_load`=%s, `host_memory`=%s, `host_cpu`=%s, `host_state`=%s,
                    `host_heartbeat`=NOW(),
                    `host_processing`=0
                WHERE `host_id`=%s
                """, total_load, memory, cpu, state, host_id)
        except database.DatabaseError as e:
            raise HostError("Failed to update host load: " + e.args[1])

    async def find_host(self, host_name):
        try:
            host = await self.db.get(
                """
                SELECT *
                FROM `hosts`
                WHERE `host_name`=%s
                LIMIT 1;
                """, host_name
            )
        except database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if host is None:
            raise HostNotFound()

        return HostAdapter(host)

    async def get_host(self, host_id):
        try:
            host = await self.db.get(
                """
                SELECT *
                FROM `hosts`
                WHERE `host_id`=%s
                LIMIT 1;
                """, host_id
            )
        except database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        if host is None:
            raise HostNotFound()

        return HostAdapter(host)

    async def list_enabled_hosts(self):
        try:
            hosts = await self.db.query(
                """
                SELECT *
                FROM `hosts`
                WHERE `host_enabled`=1;
                """)
        except database.DatabaseError as e:
            raise HostError("Failed to get hosts: " + e.args[1])

        return list(map(HostAdapter, hosts))

    async def list_hosts(self, region_id=None):
        try:
            if region_id:
                hosts = await self.db.query(
                    """
                    SELECT *
                    FROM `hosts`
                    WHERE `host_region`=%s;
                    """, region_id)
            else:
                hosts = await self.db.query(
                    """
                    SELECT *
                    FROM `hosts`;
                    """)
        except database.DatabaseError as e:
            raise HostError("Failed to get server: " + e.args[1])

        return list(map(HostAdapter, hosts))

    async def delete_host(self, host_id):

        try:
            await self.db.execute(
                """
                DELETE FROM `hosts`
                WHERE `host_id`=%s
                """, host_id
            )
        except database.DatabaseError as e:
            raise HostError("Failed to delete a server: " + e.args[1])
