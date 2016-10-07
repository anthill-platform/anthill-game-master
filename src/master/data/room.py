
import ujson
import logging

from common.model import Model
from tornado.gen import coroutine, Return

import common.database
from common.internal import Internal, InternalError


class ApproveFailed(Exception):
    pass


class RoomError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class RoomNotFound(Exception):
    pass


class RoomAdapter(object):
    def __init__(self, data):
        self.room_id = data.get("room_id")
        self.room_settings = data.get("room_settings", {})
        self.players = data.get("players", 0)
        self.location = data.get("location", {})
        self.game_id = data.get("game_id")
        self.game_version = data.get("game_version")
        self.max_players = data.get("max_players", 8)

    def dump(self):
        return {
            "id": self.room_id,
            "room_settings": self.room_settings,
            "players": self.players,
            "location": self.location,
            "game_id": self.game_id,
            "game_version": self.game_version,
            "max_players": self.max_players
        }


class RoomsModel(Model):
    @coroutine
    def __inc_players_num__(self, room_id, db):
        yield db.execute(
            """
            UPDATE `rooms` r
            SET `players`=`players`+1
            WHERE `room_id`=%s
            """, room_id
        )

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["rooms", "players"]

    def __init__(self, db):
        self.db = db
        self.internal = Internal()

    @coroutine
    def __insert_player__(self, gamespace, account_id, room_id, key, access_token, db):
        record_id = yield db.insert(
            """
            INSERT INTO `players`
            (`gamespace_id`, `account_id`, `room_id`, `key`, `access_token`)
            VALUES (%s, %s, %s, %s, %s);
            """, gamespace, account_id, room_id, key, access_token
        )
        raise Return(record_id)

    @coroutine
    def __update_players_num__(self, room_id, db):
        yield db.execute(
            """
            UPDATE `rooms` r
            SET `players`=(SELECT COUNT(*) FROM `players` p WHERE p.room_id = r.room_id)
            WHERE `room_id`=%s
            """, room_id
        )

    @coroutine
    def approve_join(self, gamespace, room_id, key):

        with (yield self.db.acquire(auto_commit=False)) as db:
            try:
                select = yield db.get(
                    """
                    SELECT `access_token`, `record_id`
                    FROM `players`
                    WHERE `gamespace_id`=%s AND `room_id`=%s AND `key`=%s
                    FOR UPDATE;
                    """, gamespace, room_id, key
                )
            except common.database.DatabaseError as e:
                raise RoomError("Failed to approve a join: " + e.args[1])

            if select is None:
                raise ApproveFailed()

            record_id = select["record_id"]
            access_token = select["access_token"]

            try:
                yield db.execute(
                    """
                    UPDATE `players`
                    SET `state`='JOINED'
                    WHERE `gamespace_id`=%s AND `record_id`=%s;
                    """, gamespace, record_id
                )
                yield db.commit()

            except common.database.DatabaseError as e:
                raise RoomError("Failed to approve a join: " + e.args[1])

            raise Return(access_token)

    @coroutine
    def approve_leave(self, gamespace, room_id, key):
        try:
            with (yield self.db.acquire()) as db:
                affected = yield db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `key`=%s AND `room_id`=%s;
                    """, gamespace, key, room_id
                )
                if affected:
                    logging.info("Deleted players: " + str(affected))
                    yield self.__update_players_num__(room_id, db)
        except common.database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])

    @coroutine
    def assign_location(self, gamespace, room_id, location):

        if not isinstance(location, dict):
            raise RoomError("Location should be a dict")

        try:
            yield self.db.execute(
                """
                UPDATE `rooms`
                SET `location`=%s, `state`='SPAWNED'
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, ujson.dumps(location), gamespace, room_id
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to create room: " + e.args[1])
        else:
            raise Return(room_id)

    @coroutine
    def create_and_join_room(self, gamespace, game_name, game_version, gs, room_settings,
                             account_id, key, access_token):

        max_players = gs.max_players

        try:
            room_id = yield self.db.insert(
                """
                INSERT INTO `rooms`
                (`gamespace_id`, `game_name`, `game_version`, `game_server_id`, `players`,
                  `max_players`, `location`, `settings`, `state`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'NONE')
                """, gamespace, game_name, game_version, gs.game_server_id, 1, max_players,
                "{}", ujson.dumps(room_settings)
            )

            record_id = yield self.__insert_player__(gamespace, account_id, room_id, key, access_token, self.db)
        except common.database.DatabaseError as e:
            raise RoomError("Failed to create a room: " + e.args[1])
        else:
            raise Return((record_id, room_id))

    @coroutine
    def find_and_join_room(self, gamespace, game_name, game_version, game_server_id,
                           account_id, key, access_token, settings):

        """
        Find the room and join into it, if any
        :param gamespace: the gamespace
        :param game_name: the game ID (string)
        :param game_version: the game's version (string, like 1.0)
        :param game_server_id: game server configuration id
        :param account_id: account of the player
        :param key: an unique string to find the record by
        :param access_token: active player's access token
        :param settings: room specific filters, defined like so:
                {"filterA": 5, "filterB": true, "filterC": {"@func": ">", "@value": 10}}
        :returns a pair of record_id for the player and room info
        """
        try:
            body, values = common.database.format_conditions_json('settings', settings)
        except common.database.ConditionError as e:
            raise RoomError(e.message)

        record_id = None

        try:
            with (yield self.db.acquire(auto_commit=False)) as db:

                # search for a room first (and lock it for a while)
                room = yield db.get(
                    """
                    SELECT * FROM `rooms`
                    WHERE
                      `gamespace_id`=%s AND
                      `game_name`=%s AND
                      `game_version`=%s AND
                      `game_server_id`=%s AND
                      `players`<`max_players` AND
                      `state`='SPAWNED'
                      {0} {1}
                    FOR UPDATE;
                    """.format("AND" if body else "", body), gamespace, game_name, game_version,
                    game_server_id, *values
                )

                if room is None:
                    yield db.commit()
                    raise RoomNotFound()

                room_id = room["room_id"]

                # at last, join into the player list
                record_id = yield self.join_room(gamespace, room_id, account_id, key, access_token, db)

        except common.database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

        result = (record_id, room)

        raise Return(result)

    @coroutine
    def find_room(self, gamespace, game_name, game_version, game_server_id, settings):

        try:
            body, values = common.database.format_conditions_json('settings', settings)
        except common.database.ConditionError as e:
            raise RoomError(e.message)

        try:
            room = yield self.db.get(
                """
                SELECT * FROM `rooms`
                WHERE
                  `gamespace_id`=%s AND
                  `game_name`=%s AND
                  `game_version`=%s AND
                  `game_server_id`=%s AND
                  `players`<`max_players` AND
                  `state`='SPAWNED'
                  {0} {1}
                """.format("AND" if body else "", body), gamespace, game_name, game_version, game_server_id, *values
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        if room is None:
            raise RoomNotFound()

        raise Return(RoomAdapter(room))

    @coroutine
    def update_room_settings(self, gamespace, room_id, room_settings):

        if not isinstance(room_settings, dict):
            raise RoomError("Room settings is not a dict")

        try:
            yield self.db.execute(
                """
                UPDATE `rooms`
                SET `settings`=%s
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, ujson.dumps(room_settings), gamespace, room_id
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to update a room: " + e.args[1])

    @coroutine
    def get_room(self, gamespace, room_id):
        try:
            room = yield self.db.get(
                """
                SELECT * FROM `rooms`
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, gamespace, room_id
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        if room is None:
            raise RoomNotFound()

        raise Return(RoomAdapter(room))

    @coroutine
    def instantiate(self, gamespace, game_id, game_version, room_id, server_host, settings):

        try:
            result = yield self.internal.request(
                server_host, "spawn",
                game_id=game_id,
                game_version=game_version,
                room_id=room_id,
                gamespace=gamespace,
                settings=settings)

        except InternalError as e:
            raise RoomError("Failed to spawn a new game server: " + str(e.code) + " " + e.body)

        raise Return(result)

    @coroutine
    def join_room(self, gamespace, room_id, account_id, key, access_token, db):
        """
        Joins the player to the room
        :param gamespace: the gamespace
        :param room_id: a room to join to
        :param account_id: account of the player
        :param key: an unique string to find the record by
        :param access_token: active player's access token
        :param db: a reference to database instance (optional)
        """
        try:
            # increment player count (virtually)
            yield self.__inc_players_num__(room_id, db)
            yield db.commit()

            record_id = yield self.__insert_player__(gamespace, account_id, room_id, key, access_token, db)
            yield db.commit()

        except common.database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

        raise Return(record_id)

    @coroutine
    def leave_room(self, gamespace, room_id, account_id, remove_room=False):
        try:
            with (yield self.db.acquire()) as db:
                affected = yield db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `account_id`=%s AND `room_id`=%s;
                    """, gamespace, account_id, room_id
                )
                if affected:
                    logging.info("Deleted players: " + str(affected))
                    yield self.__update_players_num__(room_id, db)
        except common.database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])
        finally:
            if remove_room:
                yield self.remove_room(gamespace, room_id)

    @coroutine
    def leave_room_reservation(self, gamespace, room_id, account_id):
        try:
            with (yield self.db.acquire()) as db:
                affected = yield db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `account_id`=%s AND `room_id`=%s AND `state`='RESERVED';
                    """, gamespace, account_id, room_id
                )
                if affected:
                    logging.info("Deleted reserved players: " + str(affected))
                    yield self.__update_players_num__(room_id, db)
        except common.database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])

    @coroutine
    def list_rooms(self, gamespace, game_name, game_version, game_server_id, settings):

        try:
            body, values = common.database.format_conditions_json('settings', settings)
        except common.database.ConditionError as e:
            raise RoomError(e.message)

        try:
            rooms = yield self.db.query(
                """
                SELECT * FROM `rooms`
                WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s
                  AND `game_server_id`=%s AND `players`<`max_players`
                  {0} {1}
                """.format("AND" if body else "", body), gamespace, game_name, game_version, game_server_id, *values
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        raise Return(map(RoomAdapter, rooms))

    @coroutine
    def remove_room(self, gamespace, room_id):
        try:
            # cleanup empty room

            with (yield self.db.acquire()) as db:
                yield db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `room_id`=%s;
                    """, gamespace, room_id
                )
                yield db.execute(
                    """
                    DELETE FROM `rooms`
                    WHERE `room_id`=%s AND `gamespace_id`=%s;
                    """, room_id, gamespace
                )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])

    @coroutine
    def spawn_server(self, gamespace, game_id, game_version, room_id, server_host, settings):

        result = yield self.instantiate(gamespace, game_id, game_version, room_id, server_host, settings)

        if "location" not in result:
            raise RoomError("No location in result.")

        location = result["location"]

        yield self.assign_location(gamespace, room_id, location)

        raise Return(result)
