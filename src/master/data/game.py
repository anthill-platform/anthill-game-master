
from tornado.gen import coroutine, Return

from common.model import Model

import common.database
import ujson


class GameError(Exception):
    pass


class GameNotFound(Exception):
    pass


class GameVersionError(Exception):
    pass


class GameVersionNotFound(Exception):
    pass


class GameSettingsAdapter(object):
    def __init__(self, data):
        self.game_id = data.get("game_id")
        self.server_host = data.get("server_host", "game-ctl")
        self.schema = data.get("schema", GamesModel.DEFAULT_SERVER_SCHEME)
        self.max_players = data.get("max_players", 8)
        self.game_settings = data.get("game_settings", {})
        self.server_settings = data.get("server_settings", {})


class GamesModel(Model):

    DEFAULT_SERVER_SCHEME = {
        "type": "object",
        "properties": {
            "test": {
                "type": "string",
                "title": "A test Option",
                "default": "test",
                "description": "Please see 'Game Server Configuration Schema' at the bottom to configure options."
            }
        },
        "options":
        {
            "disable_edit_json": True,
            "disable_properties": True
        },
        "title": "Game Configuration"
    }

    GAME_SETTINGS_SCHEME = {
        "type": "object",
        "properties": {
            "binary": {
                "type": "string",
                "title": "Application Binary",
                "description": "A binary file would be called at server startup",
                "minLength": 1,
                "propertyOrder": 1
            },
            "ports": {
                "type": "number",
                "format": "number",
                "title": "Ports number",
                "description": "Amount of ports being user by this application (either TCP or UDP)",
                "default": 1,
                "maximum": 4,
                "minimum": 1,
                "propertyOrder": 2
            },
            "check_period": {
                "type": "number",
                "format": "number",
                "title": "Check Period",
                "description": "How often check the game server health (in seconds)",
                "maximum": 600,
                "minimum": 5,
                "propertyOrder": 3,
                "default": 60
            },
            "token": {
                "title": "Access token",
                "description": "Provide an access token for a server instance.",
                "type": "object",
                "properties": {
                    "authenticate": {
                        "type": "boolean",
                        "format": "checkbox",
                        "title": "Provide Server-Side access token",
                        "description": "Please note that this account "
                                       "should have 'auth_non_unique' scope to perform such authentication.",
                        "default": False,
                        "propertyOrder": 1
                    },
                    "scopes": {
                        "type": "string",
                        "pattern": "^([a-zA-Z0-9_,]*)$",
                        "title": "Access scopes",
                        "propertyOrder": 2
                    },
                    "username": {
                        "type": "string",
                        "minLength": 1,
                        "title": "Username to authenticate as",
                        "description": "Credential is 'dev' only, so 'dev:' should be skipped.",
                        "propertyOrder": 3
                    },
                    "password": {
                        "type": "string",
                        "minLength": 1,
                        "title": "Password for the username",
                        "propertyOrder": 4
                    }
                },
                "format": "grid",
                "options":
                {
                    "disable_edit_json": True,
                    "disable_properties": True,
                    "disable_collapse": False,
                    "collapsed": True
                },
                "propertyOrder": 4
            },
            "discover": {
                "title": "Discover Services",
                "description": "A list of service automatically to discover for the game server",
                "type": "array",
                "format": "table",
                "items": {
                    "title": "Service ID",
                    "type": "string"
                },
                "options":
                {
                    "disable_collapse": False,
                    "collapsed": True
                },
                "propertyOrder": 5
            },
            "arguments": {
                "items": {
                    "type": "string",
                    "title": "An Argument",
                    "minLength": 1
                },
                "title": "Additional Command Line Arguments",
                "description": "Command arguments are as follows: [binary] [unix socket] [ports to listen] "
                               "[ * Application Command Line Arguments * ]",
                "type": "array",
                "format": "table",
                "propertyOrder": 6
            },
            "env": {
                "items": {
                    "type": "object",
                    "title": "A Variable",
                    "properties": {
                        "key": {
                            "type": "string",
                            "title": "Key",
                            "minLength": 1
                        },
                        "value": {
                            "type": "string",
                            "title": "Value"
                        }
                    }
                },
                "title": "Environment Variables",
                "type": "array",
                "format": "table",
                "propertyOrder": 7
            }
        },
        "options":
        {
            "disable_edit_json": True,
            "disable_properties": True
        },
        "title": "Game configuration"
    }

    def __init__(self, db):
        self.db = db

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["games", "game_versions"]

    @coroutine
    def delete_game_version(self, gamespace_id, game_id, game_version):
        try:
            yield self.db.get(
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

    def default_game_settings(self):
        return GameSettingsAdapter({})

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

        raise Return(GameSettingsAdapter(result))

    @coroutine
    def get_game_version_server_settings(self, gamespace_id, game_id, game_version):
        try:
            result = yield self.db.get(
                """
                    SELECT `server_settings`
                    FROM `game_versions`
                    WHERE `gamespace_id`=%s AND `game_id`=%s AND `game_version`=%s
                """, gamespace_id, game_id, game_version)
        except common.database.DatabaseError as e:
            raise GameVersionError("Failed to get game:" + e.args[1])

        if result is None:
            raise GameVersionNotFound()

        raise Return(result["server_settings"])

    @coroutine
    def set_game_settings(self, gamespace_id, game_id, game_host, schema,
                          max_players, game_settings, server_settings):

        try:
            yield self.get_game_settings(gamespace_id, game_id)
        except GameNotFound:
            try:
                yield self.db.insert(
                    """
                        INSERT INTO `games`
                        (`game_id`, `gamespace_id`, `server_host`, `schema`,
                            `max_players`, `game_settings`, `server_settings`)
                        VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """, game_id, gamespace_id, game_host, ujson.dumps(schema),
                    max_players, ujson.dumps(game_settings), ujson.dumps(server_settings))
            except common.database.DatabaseError as e:
                raise GameError("Failed to insert game settings:" + e.args[1])
        else:
            try:
                yield self.db.execute(
                    """
                        UPDATE `games`
                        SET `server_host`=%s, `schema`=%s,
                            `max_players`=%s, `game_settings`=%s, `server_settings`=%s
                        WHERE `game_id`=%s AND `gamespace_id`=%s;
                    """, game_host, ujson.dumps(schema), max_players,
                    ujson.dumps(game_settings), ujson.dumps(server_settings), game_id, gamespace_id)
            except common.database.DatabaseError as e:
                raise GameError("Failed to change game settings:" + e.args[1])

    @coroutine
    def set_game_version_server_settings(self, gamespace_id, game_id, game_version, server_settings):
        try:
            yield self.get_game_version_server_settings(gamespace_id, game_id, game_version)
        except GameVersionNotFound:
            try:
                yield self.db.insert(
                    """
                        INSERT INTO `game_versions`
                        (`game_id`, `game_version`, `gamespace_id`, `server_settings`)
                        VALUES (%s, %s, %s, %s);
                    """, game_id, game_version, gamespace_id, ujson.dumps(server_settings))
            except common.database.DatabaseError as e:
                raise GameVersionError("Failed to insert config:" + e.args[1])
        else:
            try:
                yield self.db.execute(
                    """
                        UPDATE `game_versions`
                        SET `server_settings`=%s
                        WHERE `game_id`=%s AND `game_version`=%s AND `gamespace_id`=%s;
                    """, ujson.dumps(server_settings), game_id, game_version, gamespace_id)
            except common.database.DatabaseError as e:
                raise GameVersionError("Failed to update config:" + e.args[1])
