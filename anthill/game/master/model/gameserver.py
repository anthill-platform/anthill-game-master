
from anthill.common.model import Model
from anthill.common import database

import ujson


class GameError(Exception):
    pass


class GameServerNotFound(Exception):
    pass


class GameVersionError(Exception):
    pass


class GameVersionNotFound(Exception):
    pass


class GameServerExists(Exception):
    pass


class GameServerAdapter(object):
    def __init__(self, data):
        self.name = data.get("game_server_name")
        self.game_name = data.get("game_name")
        self.game_server_id = data.get("game_server_id")
        self.schema = data.get("schema", GameServersModel.DEFAULT_SERVER_SCHEME)
        self.max_players = data.get("max_players", 8)
        self.game_settings = data.get("game_settings", {})
        self.game_settings["max_players"] = self.max_players
        self.server_settings = data.get("server_settings", {})


class GameServersModel(Model):

    DEFAULT_SERVER_SCHEME = {
        "type": "object",
        "properties": {
            "test": {
                "type": "string",
                "title": "A test Option",
                "default": "test",
                "description": "This allows to pass custom variables to the game servers "
                               "depending on the game version (or not)."
            }
        },
        "options":
        {
            "disable_edit_json": True,
            "disable_properties": True
        },
        "title": "Custom Game Server Configuration"
    }

    GAME_SETTINGS_SCHEME = {
        "type": "object",
        "format": "grid",
        "properties": {
            "binary": {
                "type": "string",
                "title": "Application Binary",
                "description": "A binary file would be called at server startup",
                "minLength": 1,
                "propertyOrder": 1,
                "options": {
                    "grid_columns": 4
                }
            },
            "ports": {
                "type": "number",
                "format": "number",
                "title": "Ports amount",
                "description": "Amount of ports being used by this application (either TCP or UDP)",
                "default": 1,
                "maximum": 4,
                "minimum": 1,
                "propertyOrder": 2,
                "options": {
                    "grid_columns": 4
                }
            },
            "check_period": {
                "type": "number",
                "format": "number",
                "title": "Check Period",
                "description": "How often check the game server health (in seconds)",
                "maximum": 600,
                "minimum": 5,
                "propertyOrder": 3,
                "default": 60,
                "options": {
                    "grid_columns": 4
                }
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
                    "collapsed": True,
                    "grid_columns": 12
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
                    "collapsed": True,
                    "grid_columns": 12
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
                "propertyOrder": 6,
                "options": {
                    "grid_columns": 12
                }
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
                "propertyOrder": 7,
                "options": {
                    "grid_columns": 12
                }
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
        return ["game_servers", "game_server_versions"]

    async def delete_game_version(self, gamespace_id, game_name, game_version, game_server_id):
        try:
            await self.db.get(
                """
                    DELETE FROM `game_server_versions`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s AND `game_server_id`=%s;
                """, gamespace_id, game_name, game_version, game_server_id)
        except database.DatabaseError as e:
            raise GameVersionError("Failed to delete game:" + e.args[1])

    async def delete_game_server(self, gamespace_id, game_name, game_server_id):
        try:
            await self.db.get(
                """
                    DELETE FROM `game_server_versions`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_server_id`=%s;
                """, gamespace_id, game_name, game_server_id)
        except database.DatabaseError as e:
            raise GameVersionError("Failed to delete game server version:" + e.args[1])

        try:
            await self.db.get(
                """
                    DELETE FROM `game_server_versions`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_server_id`=%s;
                """, gamespace_id, game_name, game_server_id)
        except database.DatabaseError as e:
            raise GameVersionError("Failed to delete game server:" + e.args[1])

    async def get_all_versions_settings(self):
        try:
            result = await self.db.query(
                """
                    SELECT *
                    FROM `game_server_versions`
                """)
        except database.DatabaseError as e:
            raise GameError("Failed to get game settings:" + e.args[1])

        return result

    def default_game_settings(self):
        return GameServerAdapter({})

    async def list_game_servers(self, gamespace_id, game_name):
        try:
            servers = await self.db.query(
                """
                    SELECT *
                    FROM `game_servers`
                    WHERE `gamespace_id`=%s AND `game_name`=%s
                """, gamespace_id, game_name)
        except database.DatabaseError as e:
            raise GameError("Failed to get game settings:" + e.args[1])

        return map(GameServerAdapter, servers)

    async def find_game_server(self, gamespace_id, game_name, game_server_name):
        try:
            result = await self.db.get(
                """
                    SELECT *
                    FROM `game_servers`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_server_name`=%s
                """, gamespace_id, game_name, game_server_name)
        except database.DatabaseError as e:
            raise GameError("Failed to get game settings:" + e.args[1])

        if result is None:
            raise GameServerNotFound()

        return GameServerAdapter(result)

    async def get_game_server(self, gamespace_id, game_name, game_server_id):
        try:
            result = await self.db.get(
                """
                    SELECT *
                    FROM `game_servers`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_server_id`=%s
                """, gamespace_id, game_name, game_server_id)
        except database.DatabaseError as e:
            raise GameError("Failed to get game settings:" + e.args[1])

        if result is None:
            raise GameServerNotFound()

        return GameServerAdapter(result)

    async def get_version_game_server(self, gamespace_id, game_name, game_version, game_server_id):
        try:
            result = await self.db.get(
                """
                    SELECT `server_settings`
                    FROM `game_server_versions`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s AND `game_server_id`=%s
                """, gamespace_id, game_name, game_version, game_server_id)
        except database.DatabaseError as e:
            raise GameVersionError("Failed to get game:" + e.args[1])

        if result is None:
            raise GameVersionNotFound()

        return result["server_settings"]

    async def create_game_server(self, gamespace_id, game_name, game_server_name, schema,
                           max_players, game_settings, server_settings):
        try:
            game_server_id = await self.db.insert(
                """
                    INSERT INTO `game_servers`
                    (`game_name`, `gamespace_id`, `game_server_name`, `schema`,
                        `max_players`, `game_settings`, `server_settings`)
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, game_name, gamespace_id, game_server_name, ujson.dumps(schema),
                max_players, ujson.dumps(game_settings), ujson.dumps(server_settings))
        except database.DuplicateError:
            raise GameServerExists()
        except database.DatabaseError as e:
            raise GameError("Failed to insert game settings:" + e.args[1])
        else:
            return game_server_id

    async def update_game_server(self, gamespace_id, game_name, game_server_id, game_server_name,
                           schema, max_players, game_settings, server_settings):
        try:
            await self.db.execute(
                """
                    UPDATE `game_servers`
                    SET `schema`=%s, `game_server_name`=%s,
                        `max_players`=%s, `game_settings`=%s, `server_settings`=%s
                    WHERE `game_name`=%s AND `gamespace_id`=%s AND `game_server_id`=%s;
                """, ujson.dumps(schema), game_server_name, max_players,
                ujson.dumps(game_settings), ujson.dumps(server_settings), game_name, gamespace_id, game_server_id)
        except database.DatabaseError as e:
            raise GameError("Failed to change game settings:" + e.args[1])

    async def set_version_game_server(self, gamespace_id, game_name, game_version, game_server_id, server_settings):

        dump = ujson.dumps(server_settings)

        try:
            await self.db.execute(
                """
                    INSERT INTO `game_server_versions`
                    (`game_name`, `game_version`, `game_server_id`, `gamespace_id`, `server_settings`)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE `server_settings`=%s;
                """, game_name, game_version, game_server_id, gamespace_id, dump, dump)
        except database.DatabaseError as e:
            raise GameVersionError("Failed to insert config:" + e.args[1])
