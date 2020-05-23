from tornado.gen import sleep
from tornado.ioloop import IOLoop, PeriodicCallback

from anthill.common.model import Model
from anthill.common.internal import Internal, InternalError
from anthill.common.discover import DiscoveryError
from anthill.common.validate import validate
from anthill.common.jsonrpc import JsonRPCTimeout, JsonRPCError, JSONRPC_TIMEOUT
from anthill.common import random_string, database, discover

from .gameserver import GameServerAdapter
from .host import RegionAdapter, HostAdapter, HostNotFound

import ujson
import logging
import platform


class ApproveFailed(Exception):
    pass


class RoomError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class RoomNotFound(Exception):
    pass


class PlayerRecordAdapter(object):
    def __init__(self, data):
        self.room_id = str(data.get("room_id"))
        self.game_name = data.get("game_name")
        self.game_version = data.get("game_version")
        self.game_server = data.get("game_server_name")
        self.players = data.get("players", 0)
        self.max_players = data.get("max_players", 8)
        self.room_settings = data.get("settings", {})

    def dump(self):
        return {
            "id": str(self.room_id),
            "settings": self.room_settings,
            "players": self.players,
            "game_name": self.game_name,
            "game_version": self.game_version,
            "max_players": self.max_players,
            "game_server": self.game_server
        }


class RoomAdapter(object):
    def __init__(self, data):
        self.room_id = str(data.get("room_id"))
        self.host_id = str(data.get("host_id"))
        self.room_settings = data.get("settings", {})
        self.players = data.get("players", 0)
        self.location = data.get("location", {})
        self.game_name = data.get("game_name")
        self.game_version = data.get("game_version")
        self.max_players = data.get("max_players", 8)
        self.deployment_id = str(data.get("deployment_id", ""))
        self.state = data.get("state", "NONE")

    def dump(self):
        return {
            "id": str(self.room_id),
            "settings": self.room_settings,
            "players": self.players,
            "location": self.location,
            "game_name": self.game_name,
            "game_version": self.game_version,
            "max_players": self.max_players
        }


class HostsPlayersCountAdapter(object):
    def __init__(self, data):
        self.players_count = int(data.get("players_count", 0))
        self.host_id = str(data.get("host_id"))
        self.host_name = str(data.get("host_name"))
        self.host_region = str(data.get("host_region"))
        self.host_load = data.get("host_load")
        self.host_memory = data.get("host_memory")
        self.host_cpu = data.get("host_cpu")
        self.host_storage = data.get("host_storage")


class RoomQuery(object):
    def __init__(self, gamespace_id, game_name, game_version=None, game_server_id=None):
        self.gamespace_id = gamespace_id
        self.game_name = game_name
        self.game_version = game_version
        self.game_server_id = game_server_id

        self.room_id = None
        self.host_id = None
        self.region_id = None
        self.deployment_id = None
        self.state = None
        self.show_full = True
        self.regions_order = None
        self.limit = 0
        self.offset = 0
        self.free_slots = 1
        self.other_conditions = []
        self.for_update = False
        self.host_active = False

        self.select_game_servers = False
        self.select_hosts = False
        self.select_regions = False

    def add_conditions(self, conditions):

        if not isinstance(conditions, list):
            raise RuntimeError("conditions expected to be a list")

        self.other_conditions.extend(conditions)

    def __values__(self):
        conditions = [
            "`rooms`.`gamespace_id`=%s",
            "`rooms`.`game_name`=%s"
        ]

        data = [
            str(self.gamespace_id),
            self.game_name
        ]

        if self.game_version:
            conditions.append("`rooms`.`game_version`=%s")
            data.append(self.game_version)

        if self.game_server_id:
            conditions.append("`rooms`.`game_server_id`=%s")
            data.append(str(self.game_server_id))

        if self.state:
            conditions.append("`rooms`.`state`=%s")
            data.append(self.state)

        if not self.show_full and self.free_slots:
            conditions.append("`rooms`.`players` + %s <= `rooms`.`max_players`")
            data.append(self.free_slots)

        if self.host_id:
            conditions.append("`rooms`.`host_id`=%s")
            data.append(str(self.host_id))

        if self.deployment_id:
            conditions.append("`rooms`.`deployment_id`=%s")
            data.append(str(self.deployment_id))

        if self.region_id:
            conditions.append("`rooms`.`region_id`=%s")
            data.append(str(self.region_id))

        if self.room_id:
            conditions.append("`rooms`.`room_id`=%s")
            data.append(str(self.room_id))

        if self.host_active:
            conditions.append("""
                (
                    SELECT `hosts`.`host_state`
                    FROM `hosts`
                    WHERE `hosts`.`host_id` = `rooms`.`host_id`
                ) IN ('ACTIVE', 'OVERLOAD')
            """)

        for condition, values in self.other_conditions:
            conditions.append(condition)
            data.extend(values)

        return conditions, data

    async def query(self, db, one=False, count=False):
        conditions, data = self.__values__()

        query = """
            SELECT {0} * FROM `rooms`
        """.format(
            "SQL_CALC_FOUND_ROWS" if count else "")

        if self.select_game_servers:
            query += ",`game_servers`"
            conditions.append("`game_servers`.`game_server_id`=`rooms`.`game_server_id`")

        if self.select_hosts:
            query += ",`hosts`"
            conditions.append("`hosts`.`host_id`=`rooms`.`host_id`")

        if self.select_regions:
            query += ",`regions`"
            conditions.append("`regions`.`region_id`=`rooms`.`region_id`")

        query += """
            WHERE {0}
        """.format(" AND ".join(conditions))

        if self.regions_order and not self.host_id:
            query += "ORDER BY FIELD(region_id, {0})".format(
                ", ".join(["%s"] * len(self.regions_order))
            )
            data.extend(self.regions_order)

        if self.limit:
            query += """
                LIMIT %s,%s
            """
            data.append(int(self.offset))
            data.append(int(self.limit))

        if self.for_update:
            query += """
                FOR UPDATE
            """

        query += ";"

        if one:
            result = await db.get(query, *data)

            if not result:
                return None

            return RoomAdapter(result)
        else:
            result = await db.query(query, *data)

            count_result = 0

            if count:
                count_result = await db.get(
                    """
                        SELECT FOUND_ROWS() AS count;
                    """)
                count_result = count_result["count"]

            items = map(RoomAdapter, result)

            adapters = []

            if self.select_game_servers:
                adapters.append(map(GameServerAdapter, result))
            if self.select_regions:
                adapters.append(map(RegionAdapter, result))
            if self.select_hosts:
                adapters.append(map(HostAdapter, result))

            if adapters:
                items = zip(items, *adapters)

            if count:
                return (list(items), count_result)

            return list(items)


class RoomsModel(Model):
    AUTO_REMOVE_TIME = 30

    @staticmethod
    def __generate_key__(gamespace_id, account_id):
        return str(gamespace_id) + "_" + str(account_id) + "_" + random_string(32)

    async def __inc_players_num__(self, gamespace_id, room_id, db, amount=1):
        await db.execute(
            """
            UPDATE `rooms` r
            SET `players`=`players` + %s
            WHERE `gamespace_id`=%s AND `room_id`=%s;
            """, amount, gamespace_id, room_id
        )

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["rooms", "players"]

    def get_setup_triggers(self):
        return ["player_removal"]

    def __init__(self, app, db, hosts):
        self.db = db
        self.internal = Internal()
        self.hosts = hosts
        self.app = app

        if app.monitoring:
            logging.info("[room] Players count monitoring enabled.")
            self.monitoring_report_callback = PeriodicCallback(self.__update_monitoring_status__, 30000)
        else:
            self.monitoring_report_callback = None

        self.rpc = app.rpc.acquire_rpc("game_master")

    async def __update_monitoring_status__(self):
        players_count = await self.get_players_count()
        players_count_per_host = await self.list_players_count_per_host()

        self.app.monitor_action(
            "players",
            values={"count": players_count})

        for host in players_count_per_host:
            self.app.monitor_action(
                "players_per_host",
                values={
                    "count": float(host.players_count),
                    "load": host.host_load,
                    "memory": host.host_memory,
                    "cpu": host.host_cpu,
                    "storage": host.host_storage,
                },
                host_name=host.host_name,
                host_id=host.host_id)

    async def started(self, application):
        await super(RoomsModel, self).started(application)
        if self.monitoring_report_callback:
            self.monitoring_report_callback.start()
            await self.__update_monitoring_status__()

    async def stopped(self):
        if self.monitoring_report_callback:
            self.monitoring_report_callback.stop()
        await super(RoomsModel, self).stopped()

    async def get_players_count(self):
        try:
            count = await self.db.get(
                """
                SELECT COUNT(*) AS `count` FROM `players`
                WHERE `state`='JOINED'
                """
            )
        except database.DatabaseError as e:
            raise RoomError("Failed to get players count: " + e.args[1])

        return count["count"]

    async def list_players_count_per_host(self):
        try:
            counts = await self.db.query(
                """
                SELECT COUNT(`players`.`account_id`) AS `players_count`, `hosts`.*
                FROM `players`, `hosts`
                WHERE `players`.`host_id` = `hosts`.`host_id` AND `players`.`state`='JOINED'
                GROUP BY `players`.`host_id`;
                """
            )
        except database.DatabaseError as e:
            raise RoomError("Failed to get players count: " + e.args[1])

        return list(map(HostsPlayersCountAdapter, counts))

    async def list_player_records(self, gamespace, account_id):
        try:
            player_records = await self.db.query(
                """
                SELECT `players`.`room_id`, 
                       `rooms`.`game_name`, 
                       `rooms`.`game_version`, 
                       `rooms`.`players`, 
                       `rooms`.`max_players`, 
                       `rooms`.`settings`,
                       `game_servers`.`game_server_name`
                FROM `players`, `rooms`, `game_servers`
                WHERE `players`.`gamespace_id`=%s AND `account_id`=%s AND `players`.`state`='JOINED' AND 
                    `rooms`.`room_id`=`players`.`room_id` AND `rooms`.`state`='SPAWNED' AND
                    `game_servers`.`game_server_id`=`rooms`.`game_server_id`;
                """, gamespace, account_id
            )
        except database.DatabaseError as e:
            raise RoomError("Failed to get players count: " + e.args[1])
        else:
            return list(map(PlayerRecordAdapter, player_records))

    @validate(gamespace="int", account_ids="json_list_of_ints")
    async def list_players_records(self, gamespace, account_ids):

        if not account_ids:
            return {}

        try:
            player_records = await self.db.query(
                """
                SELECT `players`.`account_id`,
                       `players`.`room_id`, 
                       `rooms`.`game_name`, 
                       `rooms`.`game_version`, 
                       `rooms`.`players`, 
                       `rooms`.`max_players`, 
                       `rooms`.`settings`,
                       `game_servers`.`game_server_name`
                FROM `players`, `rooms`, `game_servers`
                WHERE `players`.`gamespace_id`=%s AND `account_id` IN %s AND `players`.`state`='JOINED' AND 
                    `rooms`.`room_id`=`players`.`room_id` AND `rooms`.`state`='SPAWNED' AND
                    `game_servers`.`game_server_id`=`rooms`.`game_server_id`;
                """, gamespace, account_ids
            )
        except database.DatabaseError as e:
            raise RoomError("Failed to get players count: " + e.args[1])
        else:

            result = {
                account_id: []
                for account_id in account_ids
            }

            for record in player_records:
                result[record["account_id"]].append(PlayerRecordAdapter(record))

            return result

    async def __insert_player__(self, gamespace, account_id, room_id, host_id,
                                key, access_token, info, db, trigger_remove=True):
        record_id = await db.insert(
            """
            INSERT INTO `players`
            (`gamespace_id`, `account_id`, `room_id`, `host_id`, `key`, `access_token`, `info`)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """, gamespace, account_id, room_id, host_id, key, access_token, ujson.dumps(info)
        )

        if trigger_remove:
            self.trigger_remove_temp_reservation(record_id)

        return record_id

    def trigger_remove_temp_reservation_multi(self, gamespace, room_id, accounts):
        IOLoop.current().spawn_callback(self.__remove_temp_reservation_multi__, gamespace, room_id, accounts)

    def trigger_remove_temp_reservation(self, record):
        IOLoop.current().spawn_callback(self.__remove_temp_reservation__, record)

    async def __update_players_num__(self, room_id, db):
        await db.execute(
            """
            UPDATE `rooms` r
            SET `players`=(SELECT COUNT(*) FROM `players` p WHERE p.room_id = r.room_id)
            WHERE `room_id`=%s
            """, room_id
        )

    async def __remove_temp_reservation__(self, record_id):
        """
        Called asynchronously when user joined the room
        Waits a while, and then leaves the room, if the join reservation
            was not approved by game-controller.
        """

        # wait a while
        await sleep(RoomsModel.AUTO_REMOVE_TIME)

        result = await self.leave_room_reservation(record_id)

        if result:
            logging.warning("Removed player reservation: {0}".format(
                record_id
            ))

    async def __remove_temp_reservation_multi__(self, gamespace, room_id, accounts):
        """
        Called asynchronously when users joined the room
        Waits a while, and then leaves the room, if the join reservation
            was not approved by game-controller.
        """

        # wait a while
        await sleep(RoomsModel.AUTO_REMOVE_TIME)
        await self.leave_room_reservation_multi(gamespace, room_id, accounts)

    async def approve_join(self, gamespace, room_id, key):

        async with self.db.acquire(auto_commit=False) as db:
            try:
                select = await db.get(
                    """
                    SELECT `access_token`, `info`, `record_id`
                    FROM `players`
                    WHERE `gamespace_id`=%s AND `room_id`=%s AND `key`=%s
                    LIMIT 1
                    FOR UPDATE;
                    """, gamespace, room_id, key
                )
            except database.DatabaseError as e:
                raise RoomError("Failed to approve a join: " + e.args[1])
            else:
                if select is None:
                    raise ApproveFailed()

                record_id = select["record_id"]
                access_token = select["access_token"]
                info = select["info"]

                try:
                    await db.execute(
                        """
                        UPDATE `players`
                        SET `state`='JOINED'
                        WHERE `gamespace_id`=%s AND `record_id`=%s
                        LIMIT 1;
                        """, gamespace, record_id
                    )
                except database.DatabaseError as e:
                    raise RoomError("Failed to approve a join: " + e.args[1])

                return (access_token, info)
            finally:
                await db.commit()

    async def approve_leave(self, gamespace, room_id, key):
        try:
            async with self.db.acquire() as db:
                await db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `key`=%s AND `room_id`=%s;
                    """, gamespace, key, room_id
                )
        except database.DatabaseError as e:
            # well, a dead lock is possible here, so ignore it as it happens
            pass

    async def assign_location(self, gamespace, room_id, location):

        if not isinstance(location, dict):
            raise RoomError("Location should be a dict")

        try:
            await self.db.execute(
                """
                UPDATE `rooms`
                SET `location`=%s, `state`='SPAWNED'
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, ujson.dumps(location), gamespace, room_id
            )
        except database.DatabaseError as e:
            raise RoomError("Failed to create room: " + e.args[1])
        else:
            return room_id

    async def create_and_join_room(
            self, gamespace, game_name, game_version, gs, room_settings,
            account_id, access_token, player_info, host, deployment_id, trigger_remove=True):

        max_players = gs.max_players

        key = RoomsModel.__generate_key__(gamespace, account_id)

        try:
            room_id = await self.db.insert(
                """
                INSERT INTO `rooms`
                (`gamespace_id`, `game_name`, `game_version`, `game_server_id`, `players`,
                  `max_players`, `location`, `settings`, `state`, `host_id`, `region_id`, `deployment_id`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'NONE', %s, %s, %s)
                """, gamespace, game_name, game_version, gs.game_server_id, 1, max_players,
                "{}", ujson.dumps(room_settings), host.host_id, host.region, deployment_id
            )

            record_id = await self.__insert_player__(
                gamespace, account_id, room_id, host.host_id, key, access_token, player_info, self.db, trigger_remove)

        except database.DatabaseError as e:
            raise RoomError("Failed to create a room: " + e.args[1])
        else:
            return (record_id, key, room_id)

    async def create_room(self, gamespace, game_name, game_version, gs, room_settings, host, deployment_id,
                          max_players=0):

        max_players = max_players or gs.max_players

        try:
            room_id = await self.db.insert(
                """
                INSERT INTO `rooms`
                (`gamespace_id`, `game_name`, `game_version`, `game_server_id`, `players`,
                  `max_players`, `location`, `settings`, `state`, `host_id`, `region_id`, `deployment_id`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'NONE', %s, %s, %s)
                """, gamespace, game_name, game_version, gs.game_server_id, 0, max_players,
                "{}", ujson.dumps(room_settings), host.host_id, host.region, deployment_id
            )

        except database.DatabaseError as e:
            raise RoomError("Failed to create a room: " + e.args[1])
        else:
            return room_id

    async def create_and_join_room_multi(
            self, gamespace, game_name, game_version, gs, room_settings,
            members, host, deployment_id, trigger_remove=True):

        max_players = gs.max_players

        try:
            async with self.db.acquire() as db:

                room_id = await db.insert(
                    """
                    INSERT INTO `rooms`
                    (`gamespace_id`, `game_name`, `game_version`, `game_server_id`, `players`,
                      `max_players`, `location`, `settings`, `state`, `host_id`, `region_id`, `deployment_id`)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'NONE', %s, %s, %s)
                    """, gamespace, game_name, game_version, gs.game_server_id, len(members), max_players,
                    "{}", ujson.dumps(room_settings), host.host_id, host.region, deployment_id
                )

                data = []
                scheme = []
                keys = {}

                for token, info in members:
                    key = RoomsModel.__generate_key__(gamespace, token.account)
                    keys[token.account] = key
                    data.extend([gamespace, token.account, room_id, host.host_id, key, token.key, ujson.dumps(info)])
                    scheme.append('(%s, %s, %s, %s, %s, %s, %s)')

                query_string = """
                    INSERT INTO `players`
                    (`gamespace_id`, `account_id`, `room_id`, `host_id`, `key`, `access_token`, `info`)
                    VALUES {0};
                    """.format(",".join(scheme))

                first_record_id = await db.insert(query_string, *data)

                await db.commit()

                result = {
                    token.account: (record_id, keys[token.account])
                    for record_id, (token, info) in enumerate(members, first_record_id)
                }

                if trigger_remove:
                    accounts = [token.account for token, info in members]
                    self.trigger_remove_temp_reservation_multi(gamespace, room_id, accounts)

        except database.DatabaseError as e:
            raise RoomError("Failed to create a room: " + e.args[1])
        else:
            return (result, room_id)

    async def find_and_join_room_multi(
            self, gamespace, game_name, game_version, game_server_id,
            members, filters, regions_order=None, region=None):

        """
        Find the room and join into it, if any
        :param gamespace: the gamespace
        :param game_name: the game ID (string)
        :param game_version: the game's version (string, like 1.0)
        :param game_server_id: game server configuration id
        :param members: a list of pairs (token, info)
                            token - a token of the player to join to a room
                            info - custom information about the player to be passed to the game server once it joins
        :param filters: room specific filters, defined like so:
                {"filterA": 5, "filterB": true, "filterC": {"@func": ">", "@value": 10}}
        :param regions_order: a list of region id's to order result around
        :param region: an id of the region the search should be locked around
        :returns a pair records (see __join_room_multi__) and room info
        """
        try:
            conditions = database.format_conditions_json('settings', filters)
        except database.ConditionError as e:
            raise RoomError(str(e))

        try:
            async with self.db.acquire(auto_commit=False) as db:

                query = RoomQuery(gamespace, game_name, game_version, game_server_id)

                query.add_conditions(conditions)
                query.regions_order = regions_order
                query.for_update = True
                query.free_slots = len(members)
                query.show_full = False

                if region:
                    query.region_id = region

                query.host_active = True

                room = await query.query(db, one=True)

                if room is None:
                    await db.commit()
                    raise RoomNotFound()

                room_id = room.room_id

                # at last, join into the player list
                records = await self.__join_room_multi__(
                    gamespace, room_id, room.host_id, members, db)

                return (records, room)

        except database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

    async def find_and_join_room(self, gamespace, game_name, game_version, game_server_id,
                                 account_id, access_token, player_info, settings,
                                 regions_order=None, region=None):

        """
        Find the room and join into it, if any
        :param gamespace: the gamespace
        :param game_name: the game ID (string)
        :param game_version: the game's version (string, like 1.0)
        :param game_server_id: game server configuration id
        :param account_id: account of the player
        :param access_token: active player's access token
        :param player_info: A custom information about the player to be passed to the game server once it joins
        :param settings: room specific filters, defined like so:
                {"filterA": 5, "filterB": true, "filterC": {"@func": ">", "@value": 10}}
        :param regions_order: a list of region id's to order result around
        :param region: an id of the region the search should be locked around
        :returns a pair of record_id, a key (an unique string to find the record by) for the player and room info
        """
        try:
            conditions = database.format_conditions_json('settings', settings)
        except database.ConditionError as e:
            raise RoomError(str(e))

        try:
            async with self.db.acquire(auto_commit=False) as db:

                query = RoomQuery(gamespace, game_name, game_version, game_server_id)

                query.add_conditions(conditions)
                query.regions_order = regions_order
                query.for_update = True
                query.show_full = False

                if region:
                    query.region_id = region.region_id

                query.host_active = True

                room = await query.query(db, one=True)

                if room is None:
                    await db.commit()
                    raise RoomNotFound()

                room_id = room.room_id

                # at last, join into the player list
                record_id, key = await self.__join_room__(
                    gamespace, room_id, room.host_id, account_id, access_token, player_info, db)
                return (record_id, key, room)

        except database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

    async def join_room(self, gamespace, game_name, room_id, account_id, access_token, player_info):

        """
        Find the room and join into it, if any
        :param gamespace: the gamespace
        :param game_name: the game ID (string)
        :param room_id: an ID of the room join to
        :param account_id: account of the player
        :param access_token: active player's access token
        :param player_info: A custom information about the player to be passed to the game server once it joins
        :returns a pair of record_id, a key (an unique string to find the record by) for the player and room info
        """

        try:
            async with self.db.acquire(auto_commit=False) as db:

                query = RoomQuery(gamespace, game_name)

                query.room_id = room_id
                query.for_update = True
                query.show_full = False

                room = await query.query(db, one=True)

                if room is None:
                    await db.commit()
                    raise RoomNotFound()

                room_id = room.room_id

                # at last, join into the player list
                record_id, key = await self.__join_room__(
                    gamespace, room_id, room.host_id, account_id, access_token, player_info, db)

                return (record_id, key, room)

        except database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

    async def find_room(self, gamespace, game_name, game_version, game_server_id, settings, regions_order=None):

        try:
            conditions = database.format_conditions_json('settings', settings)
        except database.ConditionError as e:
            raise RoomError(str(e))

        try:

            query = RoomQuery(gamespace, game_name, game_version, game_server_id)

            query.add_conditions(conditions)
            query.regions_order = regions_order
            query.state = 'SPAWNED'
            query.limit = 1

            room = await query.query(self.db, one=True)
        except database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        if room is None:
            raise RoomNotFound()

        return room

    async def update_room_settings(self, gamespace, room_id, room_settings):

        if not isinstance(room_settings, dict):
            raise RoomError("Room settings is not a dict")

        try:
            await self.db.execute(
                """
                UPDATE `rooms`
                SET `settings`=%s
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, ujson.dumps(room_settings), gamespace, room_id
            )
        except database.DatabaseError as e:
            raise RoomError("Failed to update a room: " + e.args[1])

    async def update_rooms_state(self, host_id, state, rooms=None, exclusive=False):

        if rooms and not isinstance(rooms, list):
            raise RoomError("Not a list")

        if rooms is not None and not rooms:
            return

        try:
            if rooms is None:
                await self.db.execute(
                    """
                    UPDATE `rooms`
                    SET `state`=%s
                    WHERE `host_id`=%s;
                    """, state, host_id
                )
            else:
                if exclusive:
                    await self.db.execute(
                        """
                        UPDATE `rooms`
                        SET `state`=%s
                        WHERE `host_id`=%s AND `room_id` NOT IN (%s);
                        """, state, host_id, rooms
                    )
                else:
                    await self.db.execute(
                        """
                        UPDATE `rooms`
                        SET `state`=%s
                        WHERE `host_id`=%s AND `room_id` IN (%s);
                        """, state, host_id, rooms
                    )
        except database.DatabaseError as e:
            raise RoomError("Failed to update a room: " + e.args[1])

    async def get_room(self, gamespace, room_id):
        try:
            room = await self.db.get(
                """
                SELECT * FROM `rooms`
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, gamespace, room_id
            )
        except database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        if room is None:
            raise RoomNotFound()

        return RoomAdapter(room)

    # noinspection PyMethodMayBeStatic
    async def prepare(self, gamespace, settings):

        """
        This method takes the game settings generated by schema in GAME_SETTINGS_SCHEME, and prepares it for usage by
            controller game sever instance. For example, it authenticates if username/password is provided and then
            replaces the whole section with generated token to hide passwords themselves
        """

        token = settings.get("token", {})

        if token:
            username = token.get("username")
            password = token.get("password")
            scopes = token.get("scopes", "")
            authenticate = token.get("authenticate", False)

            del settings["token"]

            if authenticate:

                if not username:
                    raise RoomError("No 'token.username' field.")

                internal = Internal()

                try:
                    access_token = await internal.request(
                        "login", "authenticate",
                        credential="dev", username=username, key=password, scopes=scopes,
                        gamespace_id=gamespace, unique="false")
                except InternalError as e:
                    raise RoomError(
                        "Failed to authenticate for server-side access token: " + str(e.code) + ": " + e.body)
                else:
                    settings["token"] = access_token["token"]

        discovery_settings = settings.get("discover", None)

        if discovery_settings:
            del settings["discover"]

            try:
                services = await discover.cache.get_services(discovery_settings, network="external")
            except DiscoveryError as e:
                raise RoomError("Failed to discover services for server-side use: " + str(e))
            else:
                settings["discover"] = services

    async def instantiate(self, gamespace, game_id, game_version, game_server_name,
                          deployment_id, room_id, host, game_settings, server_settings,
                          room_settings, other_settings=None):

        await self.prepare(gamespace, game_settings)

        settings = {
            "game": game_settings,
            "server": server_settings,
            "room": room_settings
        }

        if other_settings:
            settings["other"] = other_settings

        try:
            result = await self.rpc.send_mq_request(
                "game_host_{0}".format(host.host_id),
                "spawn", JSONRPC_TIMEOUT, game_name=game_id, game_version=game_version,
                game_server_name=game_server_name,
                room_id=room_id, deployment=deployment_id, settings=settings)
        except JsonRPCTimeout as e:
            raise RoomError("Failed to spawn a new game server (timeout)")
        except JsonRPCError as e:
            raise RoomError("Failed to spawn a new game server: " + str(e.code) + " " + e.message)

        return result

    async def __join_room_multi__(self, gamespace, room_id, host_id, members, db):
        """
        Joins a bulk of players to the room. A slot for each token is guaranteed

        :param gamespace: the gamespace
        :param room_id: a room to join to
        :param members: a list of pairs (token, info)
                            token - a token of the player to join to a room
                            info - custom information about the player to be passed to the game server once it joins
        :param db: a reference to database instance

        :returns a dict of pairs of record id and a key {1: (record_id, key), 2: (record_id, key), ...},
                 the key is a corresponding player's account
        """

        try:
            # increment player count (virtually)
            await self.__inc_players_num__(gamespace, room_id, db, len(members))
            await db.commit()

            data = []
            scheme = []
            keys = {}

            for token, info in members:
                key = RoomsModel.__generate_key__(gamespace, token.account)
                keys[token.account] = key
                data.extend([gamespace, token.account, room_id, host_id, key, token.key, ujson.dumps(info)])
                scheme.append('(%s, %s, %s, %s, %s, %s, %s)')

            first_record_id = await db.insert(
                """
                INSERT INTO `players`
                (`gamespace_id`, `account_id`, `room_id`, `host_id`, `key`, `access_token`, `info`)
                VALUES {0};
                """.format(",".join(scheme)), *data
            )
            await db.commit()

            result = {
                token.account: (record_id, keys[token.account])
                for record_id, (token, info) in enumerate(members, first_record_id)
            }

            accounts = [token.account for token, info in members]
            self.trigger_remove_temp_reservation_multi(gamespace, room_id, accounts)

            await db.commit()

        except database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

        return result

    async def __join_room__(self, gamespace, room_id, host_id, account_id, access_token, player_info, db):
        """
        Joins the player to the room
        :param gamespace: the gamespace
        :param room_id: a room to join to
        :param account_id: account of the player
        :param access_token: active player's access token
        :param player_info: A custom information about the player to be passed to the game server once it joins
        :param db: a reference to database instance

        :returns a pair of record id and a key (an unique string to find the record by)
        """

        key = RoomsModel.__generate_key__(gamespace, account_id)

        try:
            # increment player count (virtually)
            await self.__inc_players_num__(gamespace, room_id, db)
            await db.commit()

            record_id = await self.__insert_player__(
                gamespace, account_id, room_id, host_id, key, access_token, player_info, db, True)
            await db.commit()

            await db.commit()

        except database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

        return (record_id, key)

    async def leave_room(self, gamespace, room_id, account_id, remove_room=False):
        try:
            async with self.db.acquire() as db:
                await db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `account_id`=%s AND `room_id`=%s
                    LIMIT 1;
                    """, gamespace, account_id, room_id
                )
        except database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])
        finally:
            if remove_room:
                await self.remove_room(gamespace, room_id)

    async def leave_room_multi(self, gamespace, room_id, accounts, remove_room=False):
        try:
            async with self.db.acquire() as db:
                await db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `account_id` IN %s AND `room_id`=%s;
                    """, gamespace, accounts, room_id
                )
        except database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])
        finally:
            if remove_room:
                await self.remove_room(gamespace, room_id)

    async def leave_room_reservation(self, record_id):
        async with self.db.acquire() as db:
            try:
                result = await db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `record_id`=%s AND `state`='RESERVED'
                    LIMIT 1;
                    """, record_id)
            except database.DatabaseError as e:
                return False
            else:
                return result

    async def leave_room_reservation_multi(self, gamespace, room_id, accounts):
        try:
            async with self.db.acquire() as db:
                result = await db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `account_id` IN %s AND `room_id`=%s AND `state`='RESERVED';
                    """, gamespace, accounts, room_id
                )

                return result
        except database.DatabaseError as e:
            # well, a dead lock is possible here, so ignore it as it happens
            pass

    async def list_rooms(self, gamespace, game_name, game_version, game_server_id, settings,
                         regions_order=None, show_full=True, region=None, host=None):

        try:
            conditions = database.format_conditions_json('settings', settings)
        except database.ConditionError as e:
            raise RoomError(str(e))

        try:
            query = RoomQuery(gamespace, game_name, game_version, game_server_id)

            query.add_conditions(conditions)
            query.regions_order = regions_order
            query.show_full = show_full
            query.state = 'SPAWNED'
            query.host_id = host

            if region:
                query.region_id = region.region_id

            query.host_active = True

            rooms = await query.query(self.db, one=False)
        except database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        return rooms

    async def terminate_room(self, gamespace, room_id, room=None, host=None):

        if not room:
            room = await self.get_room(gamespace, room_id)

        if not host:
            try:
                host = await self.hosts.get_host(room.host_id)
            except HostNotFound:
                raise RoomError("Failed to get host, not found: " + room.host_id)

        try:
            await self.rpc.send_mq_request(
                "game_host_{0}".format(host.host_id),
                "terminate_room", JSONRPC_TIMEOUT, room_id=room_id)
        except JsonRPCTimeout as e:
            raise RoomError("Failed to terminate a room: timeout")
        except JsonRPCError as e:
            raise RoomError("Failed to terminate a room: " + str(e.code) + " " + e.message)

        await self.remove_room(gamespace, room_id)

    async def execute_stdin_command(self, gamespace, room_id, command, room=None, host=None):

        if not room:
            room = await self.get_room(gamespace, room_id)

        if not host:
            try:
                host = await self.hosts.get_host(room.host_id)
            except HostNotFound:
                raise RoomError("Failed to get host, not found: " + room.host_id)

        try:
            await self.rpc.send_mq_request(
                "game_host_{0}".format(host.host_id),
                "execute_stdin", JSONRPC_TIMEOUT, room_id=room_id, command=command)
        except JsonRPCTimeout as e:
            raise RoomError("Failed to execute a command: timeout")
        except JsonRPCError as e:
            raise RoomError("Failed to execute a command: " + str(e.code) + " " + e.message)

    async def remove_host_rooms(self, host_id, except_rooms=None):
        try:
            # cleanup empty room

            async with self.db.acquire() as db:
                if except_rooms:
                    await db.execute(
                        """
                        DELETE FROM `rooms`
                        WHERE `host_id`=%s AND `room_id` NOT IN %s;
                        """, host_id, except_rooms
                    )
                    await db.execute(
                        """
                        DELETE FROM `players`
                        WHERE `host_id`=%s AND `room_id` NOT IN %s;
                        """, host_id, except_rooms
                    )
                else:
                    await db.execute(
                        """
                        DELETE FROM `rooms`
                        WHERE `host_id`=%s;
                        """, host_id
                    )
                    await db.execute(
                        """
                        DELETE FROM `players`
                        WHERE `host_id`=%s
                        """, host_id
                    )
        except database.DatabaseError as e:
            raise RoomError("Failed to remove rooms: " + e.args[1])

    async def remove_room(self, gamespace, room_id):
        try:
            # cleanup empty room

            async with self.db.acquire() as db:
                await db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `room_id`=%s;
                    """, gamespace, room_id
                )
                await db.execute(
                    """
                    DELETE FROM `rooms`
                    WHERE `room_id`=%s AND `gamespace_id`=%s;
                    """, room_id, gamespace
                )
        except database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])

    async def spawn_server(self, gamespace, game_id, game_version, game_server_name, deployment_id,
                           room_id, host, game_settings, server_settings, room_settings, other_settings=None):

        result = await self.instantiate(
            gamespace, game_id, game_version, game_server_name,
            deployment_id, room_id, host,
            game_settings, server_settings, room_settings, other_settings)

        if "location" not in result:
            raise RoomError("No location in result.")

        location = result["location"]

        await self.assign_location(gamespace, room_id, location)

        return result
