
from tornado.gen import coroutine, Return

import common.database
import json


class GameError(Exception):
    pass


class GameNotFound(Exception):
    pass


class GameVersionError(Exception):
    pass


class GameVersionNotFound(Exception):
    pass


class GamesModel(object):
    def __init__(self, db):
        self.db = db

    @coroutine
    def delete_game_version(self, gamespace_id, game_id, game_version):
        try:
            result = yield self.db.get(
                """
                    DELETE FROM `game_versions`
                    WHERE `gamespace_id`=%s AND `game_id`=%s AND `game_version`=%s
                """, gamespace_id, game_id, game_version)
        except common.database.DatabaseError as e:
            raise GameVersionError("Failed to delete game:" + e.args[1])

    @coroutine
    def get_all_versions_settings(self):
        try:
            result = yield self.db.query(
                """
                    SELECT *
                    FROM `game_versions`
                """)
        except common.database.DatabaseError as e:
            raise GameError("Failed to get game settings:" + e.args[1])

        raise Return(result)

    @coroutine
    def get_game_host(self, gamespace_id, game_id):
        try:
            result = yield self.db.get(
                """
                    SELECT `server_host`
                    FROM `games`
                    WHERE `gamespace_id`=%s AND `game_id`=%s
                """, gamespace_id, game_id)
        except common.database.DatabaseError as e:
            raise GameError("Failed to get game settings:" + e.args[1])

        if result is None:
            raise GameNotFound()

        raise Return(result["server_host"])

    @coroutine
    def get_game_settings(self, gamespace_id, game_id):
        try:
            result = yield self.db.get(
                """
                    SELECT *
                    FROM `games`
                    WHERE `gamespace_id`=%s AND `game_id`=%s
                """, gamespace_id, game_id)
        except common.database.DatabaseError as e:
            raise GameError("Failed to get game settings:" + e.args[1])

        if result is None:
            raise GameNotFound()

        raise Return(result)

    @coroutine
    def get_game_version_config(self, gamespace_id, game_id, game_version):
        try:
            result = yield self.db.get(
                """
                    SELECT *
                    FROM `game_versions`
                    WHERE `gamespace_id`=%s AND `game_id`=%s AND `game_version`=%s
                """, gamespace_id, game_id, game_version)
        except common.database.DatabaseError as e:
            raise GameVersionError("Failed to get game:" + e.args[1])

        if result is None:
            raise GameVersionNotFound()

        raise Return(result["game_config"])

    @coroutine
    def set_game_settings(self, gamespace_id, game_id, game_host, schema,
                          max_players, other_settings, default_settings):

        try:
            settings = yield self.get_game_settings(gamespace_id, game_id)
        except GameNotFound:
            try:
                record_id = yield self.db.insert(
                    """
                        INSERT INTO `games`
                        (`game_id`, `gamespace_id`, `server_host`, `schema`,
                            `max_players`, `settings`, `default_settings`)
                        VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """, game_id, gamespace_id, game_host, json.dumps(schema),
                    max_players, json.dumps(other_settings), json.dumps(default_settings))
            except common.database.DatabaseError as e:
                raise GameError("Failed to insert game settings:" + e.args[1])
        else:
            record_id = settings["record_id"]

            try:
                yield self.db.execute(
                    """
                        UPDATE `games`
                        SET `server_host`=%s, `schema`=%s,
                            `max_players`=%s, `settings`=%s, `default_settings`=%s
                        WHERE `record_id`=%s;
                    """, game_host, json.dumps(schema), max_players,
                    json.dumps(other_settings), json.dumps(default_settings), record_id)
            except common.database.DatabaseError as e:
                raise GameError("Failed to change game settings:" + e.args[1])

        raise Return(record_id)

    @coroutine
    def set_game_version_config(self, gamespace_id, game_id, game_version, game_config):
        try:
            yield self.get_game_version_config(gamespace_id, game_id, game_version)
        except GameVersionNotFound:
            try:
                yield self.db.insert(
                    """
                        INSERT INTO `game_versions`
                        (`game_id`, `game_version`, `gamespace_id`, `game_config`)
                        VALUES (%s, %s, %s, %s);
                    """, game_id, game_version, gamespace_id, json.dumps(game_config))
            except common.database.DatabaseError as e:
                raise GameVersionError("Failed to insert config:" + e.args[1])
        else:
            try:
                yield self.db.execute(
                    """
                        UPDATE `game_versions`
                        SET `game_config`=%s
                        WHERE `game_id`=%s AND `game_version`=%s AND `gamespace_id`=%s;
                    """, json.dumps(game_config), game_id, game_version, gamespace_id)
            except common.database.DatabaseError as e:
                raise GameVersionError("Failed to update config:" + e.args[1])
