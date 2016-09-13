import json

from tornado.gen import coroutine, Return

import common.admin as a
from common.environment import AppNotFound

from data.game import GameError, GameNotFound, GameVersionNotFound
from data.server import ServerNotFound


class ApplicationController(a.AdminController):
    @coroutine
    def get(self, record_id):

        env_service = self.application.env_service

        try:
            app = yield env_service.get_app_info(self.gamespace, record_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        result = {
            "app_id": record_id,
            "app_record_id": app["id"],
            "app_name": app["title"],
            "versions": app["versions"]
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("apps", "Applications")
            ], data["app_name"]),
            a.links("Application '{0}' versions".format(data["app_name"]), links=[
                a.link("app_version", v_name, icon="tags", app_id=self.context.get("record_id"),
                       version_id=v_name) for v_name, v_id in data["versions"].iteritems()
                ]),
            a.links("Navigate", [
                a.link("apps", "Go back"),
                a.link("app_settings", "Edit application settings",
                       icon="cog", record_id=self.context.get("record_id")),
                a.link("/environment/app", "Manage app '{0}' at 'Environment' service.".format(data["app_name"]),
                       icon="link text-danger", record_id=data["app_record_id"]),
            ])
        ]

    def scopes_read(self):
        return ["game_admin"]

    def scopes_write(self):
        return ["game_admin"]


class ApplicationSettingsController(a.AdminController):
    @coroutine
    def get(self, record_id):

        env_service = self.application.env_service
        games = self.application.games

        try:
            app = yield env_service.get_app_info(self.gamespace, record_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            game = yield games.get_game_settings(self.gamespace, record_id)
        except GameNotFound:
            game = {}

        result = {
            "app_id": record_id,
            "app_record_id": app["id"],
            "app_name": app["title"],
            "max_players": game.get("max_players", "8"),
            "settings": game.get("settings", {}),
            "default_settings": game.get("default_settings", {}),
            "server_host": game.get("server_host", ""),
            "schema": game.get("schema", {})
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("record_id"))
            ], "Settings"),
            a.form("Application settings", fields={
                "server_host": a.field("Game controller service ID (to be discovered by discovery service)",
                                       "text", "primary", "non-empty"),
                "schema": a.field("Version properties schema", "json", "primary", "non-empty"),
                "default_settings": a.field("Default configuration",
                                            "dorn", "primary", "non-empty", schema=data["schema"]),
                "max_players": a.field("Max players per room", "text", "primary", "number"),
                "settings": a.field("Other game-dependent settings", "json", "primary", "non-empty")
            }, methods={
                "update": a.method("Update", "primary")
            }, data=data),
            a.links("Navigate", [
                a.link("app", "Go back", record_id=data["app_record_id"]),
                a.link("https://spacetelescope.github.io/understanding-json-schema/index.html", "See docs", icon="book")
            ])
        ]

    def scopes_read(self):
        return ["game_admin"]

    def scopes_write(self):
        return ["game_admin"]

    @coroutine
    def update(self, server_host, schema, max_players, settings, default_settings):

        record_id = self.context.get("record_id")

        env_service = self.application.env_service
        games = self.application.games

        try:
            settings = json.loads(settings)
            default_settings = json.loads(default_settings)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            yield env_service.get_app_info(self.gamespace, record_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            schema = json.loads(schema)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            yield games.set_game_settings(
                self.gamespace, record_id, server_host,
                schema, max_players, settings, default_settings)
        except GameError as e:
            raise a.ActionError("Failed: " + e.message)

        raise a.Redirect(
            "app_settings",
            message="Settings have been updated",
            record_id=record_id)


class ApplicationVersionController(a.AdminController):
    @coroutine
    def delete(self, **ignored):

        games = self.application.games
        app_id = self.context.get("app_id")
        version_id = self.context.get("version_id")

        try:
            yield games.delete_game_version(self.gamespace, app_id, version_id)
        except GameError as e:
            raise a.ActionError("Failed to delete version config: " + e.message)

        raise a.Redirect(
            "app_version",
            message="Version config has been deleted",
            app_id=app_id,
            version_id=version_id)

    @coroutine
    def get(self, app_id, version_id):

        env_service = self.application.env_service
        games = self.application.games

        try:
            app = yield env_service.get_app_info(self.gamespace, app_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            game = yield games.get_game_settings(self.gamespace, app_id)
        except GameNotFound:
            game = {}

        try:
            game_config = yield games.get_game_version_config(self.gamespace, app_id, version_id)
        except GameVersionNotFound:
            game_config = {}

        result = {
            "app_id": app_id,
            "app_name": app["title"],
            "game_config": game_config,
            "schema": game.get("schema", {})
        }

        raise a.Return(result)

    def render(self, data):
        config = []

        game_config = data["game_config"]
        if not game_config:
            config.append(a.notice(
                "Default configuration",
                "This version ({0}) has no configuration, so default configuration applied. "
                "Edit the configuration below to overwrite it.".format(
                    self.context.get("version_id")
                )))

        config.extend([
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("app_id"))
            ], "Version '{0}'".format(self.context.get("version_id"))),
            a.form(title="Server instance configuration for version {0}".format(
                self.context.get("version_id")), fields={
                "game_config": a.field("Configuration", "dorn", "primary", "non-empty",
                                       schema=data["schema"])
            }, methods={
                "update": a.method("Update", "primary"),
                "delete": a.method("Delete", "danger")
            }, data=data),
            a.links("Navigate", [
                a.link("app", "Go back", record_id=self.context.get("app_id"))
            ])
        ])

        return config

    def scopes_read(self):
        return ["game_admin"]

    def scopes_write(self):
        return ["game_admin"]

    @coroutine
    def update(self, game_config):

        games = self.application.games
        app_id = self.context.get("app_id")
        version_id = self.context.get("version_id")

        try:
            game_config = json.loads(game_config)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            yield games.set_game_version_config(self.gamespace, app_id, version_id, game_config)
        except GameError as e:
            raise a.ActionError("Failed to update version config: " + e.message)

        raise a.Redirect(
            "app_version",
            message="Version config has been updated",
            app_id=app_id,
            version_id=version_id)


class ApplicationsController(a.AdminController):
    @coroutine
    def get(self):
        env_service = self.application.env_service
        apps = yield env_service.list_apps(self.gamespace)

        result = {
            "apps": apps
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([], "Applications"),
            a.links("Select application", links=[
                a.link("app", app_name, icon="mobile", record_id=app_id)
                for app_id, app_name in data["apps"].iteritems()
                ]),
            a.links("Navigate", [
                a.link("index", "Go back"),
                a.link("/environment/apps", "Manage apps", icon="link text-danger"),
            ])
        ]

    def scopes_read(self):
        return ["game_admin"]

    def scopes_write(self):
        return ["game_admin"]


class DebugControllerAction(a.StreamAdminController):
    """
    Debug controller action that does nothing except redirecting to the required game controller
    debug action
    """

    @coroutine
    def prepared(self, server):
        servers = self.application.servers

        try:
            server_data = yield servers.get_server(server)
        except ServerNotFound as e:
            raise a.ActionError("Server not found: " + str(server))

        internal_location = server_data["internal_location"]

        raise a.RedirectStream("debug", internal_location)


class DebugServerController(a.AdminController):
    @coroutine
    def get(self, server_id):

        servers = self.application.servers

        try:
            server = yield servers.get_server(server_id)
        except ServerNotFound:
            raise a.ActionError("Server not found")

        raise a.Return({})

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("servers", "Servers"),
                a.link("server", "Server '" + str(self.context.get("server_id")) + "'",
                       server_id=self.context.get("server_id"))
            ], "Debug"),
            a.script("static/admin/debug_controller.js", server=self.context.get("server_id")),
            a.links("Navigate", [
                a.link("server", "Go back", server_id=self.context.get("server_id"))
            ])
        ]

    def scopes_read(self):
        return ["game_admin"]

    def scopes_write(self):
        return ["game_admin"]


class NewServerController(a.AdminController):
    @coroutine
    def create(self, internal_location):
        servers = self.application.servers
        server_id = yield servers.new_server(internal_location)

        raise a.Redirect(
            "server",
            message="New server has been created",
            server_id=server_id)

    def render(self, data):
        return [
            a.form("New server", fields={
                "internal_location": a.field("Internal location (for debug purposes)", "text", "primary", "non-empty"),
            }, methods={
                "create": a.method("Create", "primary")
            }, data=data),
            a.links("Navigate", [
                a.link("@back", "Go back")
            ])
        ]

    def scopes_read(self):
        return ["game_admin"]

    def scopes_write(self):
        return ["game_admin"]


class RootAdminController(a.AdminController):
    def render(self, data):
        return [
            a.links("Game service", [
                a.link("apps", "Applications", icon="mobile"),
                a.link("servers", "Servers", icon="server")
            ])
        ]

    def scopes_read(self):
        return ["game_admin"]

    def scopes_write(self):
        return ["game_admin"]


class ServerController(a.AdminController):
    @coroutine
    def delete(self, *args, **kwargs):
        server_id = self.context.get("server_id")
        servers = self.application.servers

        yield servers.delete_server(server_id)

        raise a.Redirect(
            "servers",
            message="Server has been deleted")

    @coroutine
    def get(self, server_id):
        servers = self.application.servers
        server = yield servers.get_server(server_id)

        result = {
            "internal_location": server["internal_location"]
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("servers", "Servers")
            ], "Server '" + str(self.context.get("server_id")) + "'"),
            a.form("Server '{0}' information".format(self.context.get("server_id")), fields={
                "internal_location": a.field("Internal location (for debug purposes)", "text", "primary", "non-empty"),
            }, methods={
                "update": a.method("Update", "primary", order=1),
                "delete": a.method("Delete", "danger", order=2)
            }, data=data),
            a.links("Navigate", [
                a.link("servers", "Go back"),
                a.link("debug_server", "Debug server", icon="bug", server_id=self.context.get("server_id")),
                a.link("new_server", "New server", "plus")
            ])
        ]

    def scopes_read(self):
        return ["game_admin"]

    def scopes_write(self):
        return ["game_admin"]

    @coroutine
    def update(self, internal_location):
        server_id = self.context.get("server_id")
        servers = self.application.servers

        yield servers.update_server(server_id, internal_location)

        result = {
            "internal_location": internal_location
        }

        raise a.Return(result)


class ServersController(a.AdminController):
    @coroutine
    def get(self):
        servers_data = self.application.servers
        servers = yield servers_data.list_servers()

        result = {
            "servers": servers
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([], "Servers"),
            a.links("Servers", links=[
                a.link("server", "#{0} {1}".format(
                    server["server_id"],
                    server["internal_location"]
                ), icon="server", server_id=server["server_id"])
                for server in data["servers"]
                ]),
            a.links("Navigate", [
                a.link("index", "Go back"),
                a.link("new_server", "New server", "plus")
            ])
        ]

    def scopes_read(self):
        return ["discovery_admin"]

    def scopes_write(self):
        return ["discovery_admin"]
