import json

from tornado.gen import coroutine, Return

import common.admin as a
from common.environment import AppNotFound

from data.gameserver import GameError, GameServerNotFound, GameVersionNotFound, GameServersModel, GameServerExists
from data.host import HostNotFound, HostError

from geoip import geolite2
import socket


class ApplicationController(a.AdminController):
    @coroutine
    def get(self, record_id):

        env_service = self.application.env_service
        gameservers = self.application.gameservers

        try:
            app = yield env_service.get_app_info(self.gamespace, record_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            servers = yield gameservers.list_game_servers(self.gamespace, record_id)
        except GameError as e:
            raise a.ActionError("Failed to list game servers: " + e.message)

        result = {
            "app_id": record_id,
            "app_record_id": app["id"],
            "app_name": app["title"],
            "versions": app["versions"],
            "game_servers": servers
        }

        raise a.Return(result)

    def render(self, data):

        game_name = self.context.get("record_id")

        return [
            a.breadcrumbs([
                a.link("apps", "Applications")
            ], data["app_name"]),
            a.links("Game Servers", links=[
                a.link("game_server", gs.name, icon="rocket", game_server_id=gs.game_server_id, game_name=game_name)
                for gs in data["game_servers"]
            ]),
            a.links("Application '{0}' versions".format(data["app_name"]), links=[
                a.link("app_version", v_name, icon="tags", app_id=game_name,
                       version_id=v_name) for v_name, v_id in data["versions"].iteritems()
            ]),
            a.links("Navigate", [
                a.link("apps", "Go back"),
                a.link("new_game_server", "Create Game Server",
                       icon="plus", game_name=game_name),
                a.link("/environment/app", "Manage app '{0}' at 'Environment' service.".format(data["app_name"]),
                       icon="link text-danger", record_id=data["app_record_id"]),
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class GameServerController(a.AdminController):
    @coroutine
    def get(self, game_server_id, game_name):

        env_service = self.application.env_service
        gameservers = self.application.gameservers

        try:
            app = yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            gs = yield gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        result = {
            "app_name": app["title"],
            "max_players": gs.max_players,
            "game_settings": gs.game_settings,
            "server_settings": gs.server_settings,
            "game_server_name": gs.name,
            "schema": gs.schema
        }

        raise a.Return(result)

    def render(self, data):

        return [
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
            ], data["game_server_name"]),

            a.form("Game Server Settings", fields={
                "game_server_name": a.field(
                    "Game Server Name",
                    "text", "primary", "non-empty", order=0),
                "game_settings": a.field(
                    "Game Configuration", "dorn",
                    "primary", "non-empty", schema=GameServersModel.GAME_SETTINGS_SCHEME, order=1),
                "server_settings": a.field(
                    "The configuration would be send to spawned game server instance as a JSON.",
                    "dorn", "primary", "non-empty", schema=data["schema"], order=2),
                "max_players": a.field("Max players per room", "text", "primary", "number", order=4),
                "schema": a.field(
                    "Game Server Configuration Schema", "json", "primary", "non-empty", order=5)
            }, methods={
                "update": a.method("Update", "primary", order=1),
                "delete": a.method("Delete", "danger", order=2)
            }, data=data),
            a.links("Navigate", [
                a.link("app", "Go back", record_id=self.context.get("game_name")),
                a.link("new_game_server", "Clone Game Server", icon="clone",
                       game_name=self.context.get("game_name"),
                       game_server_id=self.context.get("game_server_id")),
                a.link("https://spacetelescope.github.io/understanding-json-schema/index.html", "See docs", icon="book")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    @coroutine
    def delete(self, **ignored):

        game_server_id = self.context.get("game_server_id")
        game_name = self.context.get("game_name")

        env_service = self.application.env_service
        gameservers = self.application.gameservers

        try:
            yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            yield gameservers.delete_game_server(self.gamespace, game_name, game_server_id)
        except GameError as e:
            raise a.ActionError("Failed to delete game server: " + e.message)

        raise a.Redirect(
            "app",
            message="Game server has been deleted",
            record_id=game_name)

    @coroutine
    def update(self, game_server_name, schema, max_players, game_settings, server_settings):

        game_server_id = self.context.get("game_server_id")
        game_name = self.context.get("game_name")

        env_service = self.application.env_service
        gameservers = self.application.gameservers

        try:
            game_settings = json.loads(game_settings)
            server_settings = json.loads(server_settings)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            schema = json.loads(schema)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            yield gameservers.update_game_server(
                self.gamespace, game_name, game_server_id, game_server_name,
                schema, max_players, game_settings, server_settings)
        except GameError as e:
            raise a.ActionError("Failed: " + e.message)

        raise a.Redirect(
            "game_server",
            message="Settings have been updated",
            game_name=game_name,
            game_server_id=game_server_id)


class NewGameServerController(a.AdminController):
    @coroutine
    def get(self, game_name, game_server_id=None):

        env_service = self.application.env_service
        gameservers = self.application.gameservers

        try:
            app = yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        result = {
            "app_name": app["title"],
            "schema": GameServersModel.DEFAULT_SERVER_SCHEME,
            "max_players": "8"
        }

        if game_server_id:
            try:
                gs = yield gameservers.get_game_server(self.gamespace, game_name, game_server_id)
            except GameServerNotFound:
                raise a.ActionError("No such game server to clone from")

            result.update({
                "max_players": gs.max_players,
                "game_settings": gs.game_settings,
                "server_settings": gs.server_settings,
                "game_server_name": gs.name,
                "schema": gs.schema
            })

        raise a.Return(result)

    def render(self, data):

        return [
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
            ], "New game server"),

            a.form("Game Server Settings", fields={
                "game_server_name": a.field(
                    "Game Server Name",
                    "text", "primary", "non-empty", order=0),
                "game_settings": a.field(
                    "Game Configuration", "dorn",
                    "primary", "non-empty", schema=GameServersModel.GAME_SETTINGS_SCHEME, order=1),
                "max_players": a.field("Max players per room", "text", "primary", "number", order=4),
                "schema": a.field(
                    "Custom Game Server Configuration Schema", "json", "primary", "non-empty", order=5)
            }, methods={
                "create": a.method("Create", "primary", order=1)
            }, data=data),
            a.links("Navigate", [
                a.link("app", "Go back", record_id=self.context.get("game_name")),
                a.link("https://spacetelescope.github.io/understanding-json-schema/index.html", "See docs", icon="book")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    @coroutine
    def create(self, game_server_name, schema, max_players, game_settings):

        game_name = self.context.get("game_name")

        env_service = self.application.env_service
        gameservers = self.application.gameservers

        try:
            game_settings = json.loads(game_settings)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            schema = json.loads(schema)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            game_server_id = yield gameservers.create_game_server(
                self.gamespace, game_name, game_server_name,
                schema, max_players, game_settings, {})
        except GameError as e:
            raise a.ActionError("Failed: " + e.message)
        except GameServerExists:
            raise a.ActionError("Such Game Server already exists")

        raise a.Redirect(
            "game_server",
            message="Settings have been updated",
            game_name=game_name,
            game_server_id=game_server_id)


class GameServerVersionController(a.AdminController):
    @coroutine
    def delete(self, **ignored):

        gameservers = self.application.gameservers

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        game_server_id = self.context.get("game_server_id")

        try:
            yield gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        try:
            yield gameservers.delete_game_version(self.gamespace, game_name, game_version, game_server_id)
        except GameError as e:
            raise a.ActionError("Failed to delete version config: " + e.message)

        raise a.Redirect(
            "app_version",
            message="Version config has been deleted",
            app_id=game_name,
            version_id=game_version)

    @coroutine
    def get(self, game_name, game_version, game_server_id):

        env_service = self.application.env_service
        gameservers = self.application.gameservers

        try:
            app = yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            gs = yield gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        try:
            version_settings = yield gameservers.get_version_game_server(
                self.gamespace, game_name, game_version, game_server_id)

        except GameVersionNotFound:
            version_settings = {}

        result = {
            "app_name": app["title"],
            "version_settings": version_settings,
            "game_server_name": gs.name,
            "schema": gs.schema
        }

        raise a.Return(result)

    def render(self, data):
        config = []

        if not data["version_settings"]:
            config.append(a.notice(
                "Default configuration",
                "This version ({0}) has no configuration, so default configuration ({1}) applied. "
                "Edit the configuration below to overwrite it.".format(
                    self.context.get("game_version"), data["game_server_name"]
                )))

        config.extend([
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
                a.link("app_version", self.context.get("game_version"),
                       app_id=self.context.get("game_name"), version_id=self.context.get("game_version")),

            ], "Game Server Configuration '{0}' for version '{1}'".format(
                data["game_server_name"], self.context.get("game_version"))),

            a.form(title="Server configuration for version {0}".format(
                self.context.get("game_version")), fields={
                "server_settings": a.field("Server Configuration", "dorn", "primary", "non-empty",
                                           schema=data["schema"])
            }, methods={
                "update": a.method("Update", "primary"),
                "delete": a.method("Delete", "danger")
            }, data=data),

            a.links("Navigate", [
                a.link("app_version", "Go back",
                       app_id=self.context.get("game_name"), version_id=self.context.get("game_version"))
            ])
        ])

        return config

    def access_scopes(self):
        return ["game_admin"]

    @coroutine
    def update(self, server_settings):

        gameservers = self.application.gameservers

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        game_server_id = self.context.get("game_server_id")

        try:
            yield gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        try:
            server_settings = json.loads(server_settings)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            yield gameservers.set_version_game_server(
                self.gamespace, game_name, game_version, game_server_id, server_settings)

        except GameError as e:
            raise a.ActionError("Failed to update version config: " + e.message)

        raise a.Redirect(
            "game_server_version",
            message="Version config has been updated",
            game_name=game_name,
            game_version=game_version,
            game_server_id=game_server_id)


class ApplicationVersionController(a.AdminController):

    @coroutine
    def get(self, app_id, version_id):

        env_service = self.application.env_service
        gameservers = self.application.gameservers

        try:
            app = yield env_service.get_app_info(self.gamespace, app_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            servers = yield gameservers.list_game_servers(self.gamespace, app_id)
        except GameError as e:
            raise a.ActionError("Failed to list game servers" + e.message)

        result = {
            "app_id": app_id,
            "app_name": app["title"],
            "servers": servers
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("app_id"))
            ], self.context.get("version_id")),

            a.links("Game Servers configurations for game version {0}".format(self.context.get("version_id")), links=[
                a.link("game_server_version", gs.name, icon="rocket",
                       game_name=self.context.get("app_id"),
                       game_version=self.context.get("version_id"),
                       game_server_id=gs.game_server_id)
                for gs in data["servers"]
            ]),

            a.links("Navigate", [
                a.link("app", "Go back", record_id=self.context.get("app_id"))
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]



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

    def access_scopes(self):
        return ["game_admin"]


class DebugControllerAction(a.StreamAdminController):
    """
    Debug controller action that does nothing except redirecting to the required game controller
    debug action
    """

    @coroutine
    def prepared(self, server):
        hosts = self.application.hosts

        try:
            host = yield hosts.get_host(server)
        except HostNotFound as e:
            raise a.ActionError("Server not found: " + str(server))

        raise a.RedirectStream("debug", host.internal_location)


class DebugHostController(a.AdminController):
    @coroutine
    def get(self, host_id):

        hosts = self.application.hosts

        try:
            host = yield hosts.get_host(host_id)
        except HostNotFound:
            raise a.ActionError("Server not found")

        raise a.Return({
            "host": host
        })

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("hosts", "Hosts"),
                a.link("host", data["host"].name,
                       host_id=self.context.get("host_id"))
            ], "Debug"),
            a.script("static/admin/debug_controller.js", server=self.context.get("host_id")),
            a.links("Navigate", [
                a.link("server", "Go back", host_id=self.context.get("host_id"))
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class NewHostController(a.AdminController):
    @coroutine
    def create(self, name, internal_location, host_default="false"):
        hosts = self.application.hosts
        host_id = yield hosts.new_host(name, internal_location, host_default == "true")

        raise a.Redirect(
            "host",
            message="New host has been created",
            host_id=host_id)

    def render(self, data):
        return [
            a.form("New host", fields={
                "name": a.field("Host name", "text", "primary", "non-empty", order=1),
                "internal_location": a.field("Internal location (including scheme)", "text", "primary", "non-empty", order=2),
                "host_default": a.field("Is Default?", "switch", "primary", "non-empty", order=3),
            }, methods={
                "create": a.method("Create", "primary")
            }, data=data),
            a.links("Navigate", [
                a.link("@back", "Go back")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class RootAdminController(a.AdminController):
    def render(self, data):
        return [
            a.links("Game service", [
                a.link("apps", "Applications", icon="mobile"),
                a.link("hosts", "Hosts", icon="server")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class HostController(a.AdminController):
    @coroutine
    def delete(self, *args, **kwargs):
        host_id = self.context.get("host_id")
        hosts = self.application.hosts

        yield hosts.delete_host(host_id)

        raise a.Redirect(
            "hosts",
            message="Host has been deleted")

    @coroutine
    def get(self, host_id):
        hosts = self.application.hosts
        host = yield hosts.get_host(host_id)

        result = {
            "name": host.name,
            "internal_location": host.internal_location,
            "geo_location": str(host.geo_location),
            "host_default": "true" if host.default else "false"
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("hosts", "Hosts")
            ], data["name"]),
            a.form("Host '{0}' information".format(data["name"]), fields={
                "name": a.field("Host name", "text", "primary", "non-empty", order=1),
                "internal_location": a.field("Internal location (including scheme)", "text", "primary", "non-empty", order=2),
                "geo_location": a.field("Geo location", "readonly", "primary", order=3),
                "host_default": a.field("Is default?", "switch", "primary", order=4),
            }, methods={
                "update": a.method("Update", "primary", order=1),
                "delete": a.method("Delete", "danger", order=2)
            }, data=data),
            a.form("Update geo location".format(data["name"]), fields={
                "external_location": a.field("Paste external host name (or IP) to calculate geo location",
                                       "text", "primary", "non-empty", order=1)
            }, methods={
                "update_geo": a.method("Update", "primary", order=1)
            }, data=data),
            a.links("Navigate", [
                a.link("hosts", "Go back"),
                a.link("debug_host", "Debug host", icon="bug", host_id=self.context.get("host_id")),
                a.link("new_host", "New server", "plus")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    @coroutine
    def update(self, name, internal_location, host_default="false"):
        host_id = self.context.get("host_id")
        hosts = self.application.hosts

        yield hosts.update_host(host_id, name, internal_location, host_default == "true")

        raise a.Redirect("host",
                         message="Host has been updated",
                         host_id=host_id)

    @coroutine
    def update_geo(self, external_location):

        host_id = self.context.get("host_id")

        try:
            external_ip = socket.gethostbyname(external_location)
        except socket.gaierror:
            raise a.ActionError("Failed to lookup hostname")

        geo = geolite2.lookup(external_ip)

        if geo is None:
            raise a.ActionError("Failed to lookup IP address ({0})".format(external_ip))

        x, y = geo.location

        hosts = self.application.hosts

        try:
            yield hosts.update_host_geo_location(host_id, x, y)
        except HostError as e:
            raise a.ActionError(e.message)

        raise a.Redirect("host",
                         message="Geo location updated",
                         host_id=host_id)


class HostsController(a.AdminController):
    @coroutine
    def get(self):
        hosts = self.application.hosts
        hosts_list = yield hosts.list_hosts()

        result = {
            "hosts": hosts_list
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([], "Hosts"),
            a.links("Hosts", links=[
                a.link("host", host.name, icon="server", host_id=host.host_id)
                for host in data["hosts"]
                ]),
            a.links("Navigate", [
                a.link("index", "Go back"),
                a.link("new_host", "New host", "plus")
            ])
        ]

    def access_scopes(self):
        return ["discovery_admin"]
