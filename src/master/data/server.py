
from tornado.gen import coroutine, Return
import common.database


class ServerError(Exception):
    pass


class ServerNotFound(Exception):
    pass


class ServersModel(object):
    def __init__(self, db):
        self.db = db

    @coroutine
    def new_server(self, internal_location):

        try:
            server_id = yield self.db.insert(
                """
                INSERT INTO `servers`
                (`internal_location`)
                VALUES (%s)
                """, internal_location
            )
        except common.database.DatabaseError as e:
            raise ServerError("Failed to create a server: " + e.args[1])
        else:
            raise Return(server_id)

    @coroutine
    def update_server(self, server_id, internal_location):
        try:
            yield self.db.execute(
                """
                UPDATE `servers`
                SET `internal_location`=%s
                WHERE `server_id`=%s
                """, internal_location, server_id
            )
        except common.database.DatabaseError as e:
            raise ServerError("Failed to create server: " + e.args[1])

    @coroutine
    def get_server(self, server_id):
        try:
            room = yield self.db.get(
                """
                SELECT * FROM `servers`
                WHERE `server_id`=%s
                """, server_id
            )
        except common.database.DatabaseError as e:
            raise ServerError("Failed to get server: " + e.args[1])

        if room is None:
            raise ServerNotFound()

        raise Return(room)

    @coroutine
    def list_servers(self):
        try:
            rooms = yield self.db.query(
                """
                SELECT * FROM `servers`
                """
            )
        except common.database.DatabaseError as e:
            raise ServerError("Failed to get server: " + e.args[1])

        raise Return(rooms)

    @coroutine
    def delete_server(self, server_id):

        try:
            affected = yield self.db.execute(
                """
                DELETE FROM `servers`
                WHERE `server_id`=%s
                """, server_id
            )
        except common.database.DatabaseError as e:
            raise ServerError("Failed to delete a server: " + e.args[1])
