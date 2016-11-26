import json

from tornado.gen import coroutine, Return, IOLoop
import tornado.httpclient

import common.admin as a
from common.environment import AppNotFound

from data.gameserver import GameError, GameServerNotFound, GameVersionNotFound, GameServersModel, GameServerExists
from data.host import HostNotFound, HostError
from data.deploy import DeploymentError, DeploymentNotFound, NoCurrentDeployment, DeploymentAdapter
from data.deploy import DeploymentDeliveryError, DeploymentDeliveryAdapter
from data.ban import NoSuchBan, BanError, UserAlreadyBanned

from tornado.concurrent import run_on_executor
from concurrent.futures import ThreadPoolExecutor

from geoip import geolite2
import socket
import logging
import os
import zipfile
import hashlib
import urllib
import datetime


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
            a.links("Application '{0}' versions".format(data["app_name"]), links=[
                a.link("app_version", v_name, icon="tags", app_id=game_name,
                       version_id=v_name) for v_name, v_id in data["versions"].iteritems()
                ]),
            a.links("Game Servers", links=[
                a.link("game_server", gs.name, icon="rocket", game_server_id=gs.game_server_id, game_name=game_name)
                for gs in data["game_servers"]
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

            ], "Game Server {0} version {1}".format(
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

    DEPLOYMENTS_PER_PAGE = 10

    @coroutine
    def switch_deployment(self, **ignored):
        deployments = self.application.deployments

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        deployment_id = self.context.get("deployment_id")

        try:
            deployment = yield deployments.get_deployment(self.gamespace, deployment_id)
        except DeploymentError as e:
            raise a.ActionError("Failed to get game deployment: " + e.message)
        except DeploymentNotFound as e:
            raise a.ActionError("No such deployment")

        if deployment.status != "delivered":
            raise a.ActionError("Deployment is not delivered yet, cannot switch")

        try:
            yield deployments.set_current_deployment(self.gamespace, game_name, game_version, deployment_id)
        except DeploymentError as e:
            raise a.ActionError("Failed to set game deployment: " + e.message)

        raise a.Redirect("app_version",
                         message="Deployment has been switched",
                         app_id=game_name,
                         version_id=game_version)

    @coroutine
    def get(self, app_id, version_id, page=1):

        env_service = self.application.env_service
        gameservers = self.application.gameservers
        deployments = self.application.deployments

        try:
            app = yield env_service.get_app_info(self.gamespace, app_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            servers = yield gameservers.list_game_servers(self.gamespace, app_id)
        except GameError as e:
            raise a.ActionError("Failed to list game servers: " + e.message)

        try:
            game_deployments, pages = yield deployments.list_paged_deployments(
                self.gamespace, app_id, version_id, ApplicationVersionController.DEPLOYMENTS_PER_PAGE, page)
        except DeploymentError as e:
            raise a.ActionError("Failed to list game deployments: " + e.message)

        try:
            current_deployment = yield deployments.get_current_deployment(self.gamespace, app_id, version_id)
        except NoCurrentDeployment:
            current_deployment = None
        except DeploymentError as e:
            raise a.ActionError("Failed to get current deployment: " + e.message)
        else:
            current_deployment = current_deployment.deployment_id

        result = {
            "app_id": app_id,
            "app_name": app["title"],
            "servers": servers,
            "deployments": game_deployments,
            "pages": pages,
            "current_deployment": current_deployment
        }

        raise a.Return(result)

    def render(self, data):

        current_deployment = data["current_deployment"]

        r = [
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("app_id"))
            ], self.context.get("version_id"))
        ]

        if not current_deployment:
            r.append(a.notice(
                "Warning",
                "There is no current deployment set for version <b>{0}</b>. "
                "Therefore, server spawning is not possible. "
                "Please deploy and switch to required deployment.".format(
                    self.context.get("version_id")
                )
            ))

        r.extend([
            a.content("Deployments", headers=[
                {
                    "id": "id",
                    "title": "Deployment"
                }, {
                    "id": "date",
                    "title": "Deployment Date"
                }, {
                    "id": "status",
                    "title": "Deployment Status"
                }, {
                    "id": "actions",
                    "title": "Actions"
                }
            ], items=[
                {
                    "id": [
                        a.link("deployment", item.deployment_id, icon="folder-o", badge=(
                            "current" if current_deployment == item.deployment_id else None
                        ), game_name=self.context.get("app_id"),
                           game_version=self.context.get("version_id"),
                           deployment_id=item.deployment_id)
                    ],
                    "date": str(item.date),
                    "status": [
                        {
                            DeploymentAdapter.STATUS_UPLOADING: a.status("Uploading", "info", "refresh fa-spin"),
                            DeploymentAdapter.STATUS_DELIVERING: a.status("Delivering", "info", "refresh fa-spin"),
                            DeploymentAdapter.STATUS_UPLOADED: a.status("Uploaded", "success", "check"),
                            DeploymentAdapter.STATUS_DELIVERED: a.status("Delivered", "success", "check"),
                            DeploymentAdapter.STATUS_ERROR: a.status("Error", "danger", "exclamation-triangle")
                        }.get(item.status, a.status(item.status, "default", "refresh"))
                    ],
                    "actions": [
                        a.button("app_version", "Set Current", "primary", _method="switch_deployment",
                                 game_name=self.context.get("app_id"),
                                 game_version=self.context.get("version_id"),
                                 deployment_id=item.deployment_id)
                    ] if (current_deployment != item.deployment_id) else "Current deployment"
                }
                for item in data["deployments"]
                ], style="primary", empty="There is no deployments"),
        ])

        if data["pages"] > 1:
            r.append(a.pages(data["pages"]))

        r.extend([
            a.links("Game Servers configurations for game version {0}".format(self.context.get("version_id")), links=[
                a.link(
                    "game_server_version", gs.name, icon="rocket",
                    game_name=self.context.get("app_id"),
                    game_version=self.context.get("version_id"),
                    game_server_id=gs.game_server_id)
                for gs in data["servers"]
                ]),

            a.links("Navigate", [
                a.link("deploy", "Deploy New Game Server", icon="upload",
                       game_name=self.context.get("app_id"),
                       game_version=self.context.get("version_id")),
                a.link("app", "Go back", record_id=self.context.get("app_id"))
            ])
        ])

        return r

    def access_scopes(self):
        return ["game_admin"]


class Delivery(object):
    def __init__(self, application, gamespace):
        self.application = application
        self.gamespace = gamespace

    @coroutine
    def __deliver_host__(self, game_name, game_version, deployment_id, delivery_id, host, deployment_hash):
        client = tornado.httpclient.AsyncHTTPClient()
        deployments = self.application.deployments
        location = deployments.deployments_location

        deployment_path = os.path.join(location, game_name, game_version, deployment_id + ".zip")

        try:
            f = open(deployment_path, "r")
        except Exception as e:
            yield deployments.update_deployment_delivery_status(
                self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_ERROR,
                str(e))

            raise DeploymentDeliveryError(str(e))

        try:
            @coroutine
            def producer(write):
                while True:
                    data = f.read(8192)
                    if not data:
                        break
                    yield write(data)

            request = tornado.httpclient.HTTPRequest(
                url=host.internal_location + "/@deliver_deployment?" + urllib.urlencode({
                    "game_name": game_name,
                    "game_version": game_version,
                    "deployment_id": deployment_id,
                    "deployment_hash": deployment_hash
                }),
                method="PUT",
                request_timeout=2400,
                body_producer=producer
            )

            yield client.fetch(request)

        except Exception as e:
            yield deployments.update_deployment_delivery_status(
                self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_ERROR,
                str(e))

            raise DeploymentDeliveryError(str(e))
        finally:
            try:
                f.close()
            except Exception:
                pass

        yield deployments.update_deployment_delivery_status(
            self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_DELIVERED)

    @coroutine
    def __deliver_upload__(self, game_name, game_version, deployment_id, deliver_list, deployment_hash):

        deployments = self.application.deployments

        tasks = [
            self.__deliver_host__(game_name, game_version, deployment_id, delivery_id, host, deployment_hash)
            for delivery_id, host in deliver_list
        ]

        try:
            yield tasks
        except Exception as e:
            logging.error("Error deliver deployment {0}: {1}".format(
                deployment_id, str(e)
            ))
            yield deployments.update_deployment_status(
                self.gamespace, deployment_id, DeploymentAdapter.STATUS_ERROR)
        else:
            yield deployments.update_deployment_status(
                self.gamespace, deployment_id, DeploymentAdapter.STATUS_DELIVERED)

    @coroutine
    def __deliver__(self, game_name, game_version, deployment_id, deployment_hash):
        hosts = self.application.hosts
        deployments = self.application.deployments

        try:
            hosts_list = yield hosts.list_hosts()
        except HostError as e:
            raise a.ActionError("Failed to list hosts: " + e.message)

        try:
            deliveries = yield deployments.list_deployment_deliveries(self.gamespace, deployment_id)
        except DeploymentDeliveryError as e:
            raise a.ActionError("Failed to list deliveries: " + e.message)

        deliver_list = []
        delivery_ids = {
            item.host_id: item
            for item in deliveries
        }
        host_ids = {
            item.host_id: item
            for item in hosts_list
        }

        for host in hosts_list:
            if host.host_id not in delivery_ids:
                new_delivery_id = yield deployments.new_deployment_delivery(
                    self.gamespace, deployment_id, host.host_id)
                deliver_list.append((new_delivery_id, host))

        for delivery in deliveries:
            if delivery.status == DeploymentDeliveryAdapter.STATUS_ERROR:
                deliver_list.append((delivery.delivery_id, host_ids[delivery.host_id]))

        if not deliver_list:
            raise a.ActionError("Nothing to deliver")

        try:
            yield deployments.update_deployment_status(
                self.gamespace, deployment_id, DeploymentAdapter.STATUS_DELIVERING)
        except DeploymentError as e:
            raise a.ActionError("Failed to update deployment status: " + e.message)

        try:
            yield deployments.update_deployment_deliveries_status(
                self.gamespace, [
                    delivery_id
                    for delivery_id, host in deliver_list
                    ], DeploymentDeliveryAdapter.STATUS_DELIVERING)
        except DeploymentDeliveryError as e:
            yield deployments.update_deployment_status(
                self.gamespace, deployment_id, DeploymentAdapter.STATUS_ERROR)
            raise a.ActionError("Failed to update deployment deliveries status: " + e.message)

        IOLoop.current().spawn_callback(
            self.__deliver_upload__, game_name, game_version, deployment_id, deliver_list, deployment_hash)


class ApplicationDeploymentController(a.AdminController):
    @coroutine
    def get(self, game_name, game_version, deployment_id):

        env_service = self.application.env_service
        deployments = self.application.deployments
        hosts = self.application.hosts

        try:
            app = yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            deployment = yield deployments.get_deployment(self.gamespace, deployment_id)
        except DeploymentNotFound:
            raise a.ActionError("No such deployment")
        else:
            if (deployment.game_name != game_name) or (deployment.game_version != game_version):
                raise a.ActionError("Wrong deployment")

        try:
            deliveries = yield deployments.list_deployment_deliveries(self.gamespace, deployment_id)
        except DeploymentDeliveryError as e:
            raise a.ActionError("Failed to fetch deliveries: " + e.message)

        try:
            hosts_list = yield hosts.list_hosts()
        except HostError as e:
            raise a.ActionError("Failed to list hosts: " + e.message)

        result = {
            "app_name": app["title"],
            "deployment_status": deployment.status,
            "deliveries": deliveries,
            "hosts": {
                item.host_id: item
                for item in hosts_list
            }
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
                a.link("app_version", self.context.get("game_version"),
                       app_id=self.context.get("game_name"), version_id=self.context.get("game_version"))
            ], "Deployment {0}".format(self.context.get("deployment_id"))),

            a.form("Delivery status (refresh for update)", fields={
                "deployment_status": a.field("Deployment Status", "status", {
                    DeploymentAdapter.STATUS_UPLOADING: "info",
                    DeploymentAdapter.STATUS_DELIVERING: "info",
                    DeploymentAdapter.STATUS_UPLOADED: "success",
                    DeploymentAdapter.STATUS_DELIVERED: "success",
                    DeploymentAdapter.STATUS_ERROR: "danger",
                }.get(data["deployment_status"], "info"), icon={
                    DeploymentAdapter.STATUS_UPLOADING: "refresh fa-spin",
                    DeploymentAdapter.STATUS_DELIVERING: "refresh fa-spin",
                    DeploymentAdapter.STATUS_UPLOADED: "check",
                    DeploymentAdapter.STATUS_DELIVERED: "check",
                    DeploymentAdapter.STATUS_ERROR: "exclamation-triangle",
                }.get(data["deployment_status"], "refresh fa-spin"))
            }, methods={
                "deliver": a.method("Deliver again", "primary")
            } if data["deployment_status"] not in [
                DeploymentAdapter.STATUS_DELIVERING,
                DeploymentAdapter.STATUS_UPLOADING
            ] else {}, data=data, icon="cloud-upload"),

            a.content("Host delivery status", [
                {
                    "id": "host_name",
                    "title": "Host Name"
                },
                {
                    "id": "host_location",
                    "title": "Host Location"
                },
                {
                    "id": "delivery_status",
                    "title": "Delivery status"
                },
            ], [
                {
                    "host_name": data["hosts"][item.host_id].name if item.host_id in data["hosts"] else "Unknown",
                    "host_location": data["hosts"][item.host_id].internal_location
                    if item.host_id in data["hosts"] else "Unknown",
                    "delivery_status": [
                        {
                            DeploymentDeliveryAdapter.STATUS_DELIVERING:
                                a.status("Delivering", "info", "refresh fa-spin"),
                            DeploymentDeliveryAdapter.STATUS_DELIVERED: a.status("Delivered", "success", "check"),
                            DeploymentDeliveryAdapter.STATUS_ERROR: a.status("Error: " + item.error_reason,
                                                                             "danger", "exclamation-triangle")
                        }.get(item.status, a.status(item.status, "default", "refresh")),
                    ]
                }
                for item in data["deliveries"]
            ], "primary"),

            a.links("Navigate", [
                a.link("app_version", "Go back",
                       app_id=self.context.get("game_name"),
                       version_id=self.context.get("game_version"))
            ])
        ]

    def access_scopes(self):
        return ["game_deploy_admin"]

    @coroutine
    def deliver(self, **ignored):

        env_service = self.application.env_service
        deployments = self.application.deployments
        hosts = self.application.hosts

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        deployment_id = self.context.get("deployment_id")

        try:
            app = yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            deployment = yield deployments.get_deployment(self.gamespace, deployment_id)
        except DeploymentNotFound:
            raise a.ActionError("No such deployment")
        else:
            if (deployment.game_name != game_name) or (deployment.game_version != game_version):
                raise a.ActionError("Wrong deployment")

        deployment_hash = deployment.hash

        delivery = Delivery(self.application, self.gamespace)

        yield delivery.__deliver__(game_name, game_version, deployment_id, deployment_hash)

        raise a.Redirect("deployment",
                         message="Deployment process started",
                         game_name=game_name,
                         game_version=game_version,
                         deployment_id=deployment_id)


class DeployApplicationController(a.UploadAdminController):
    executor = ThreadPoolExecutor(max_workers=4)

    def __init__(self, app, token):
        super(DeployApplicationController, self).__init__(app, token)
        self.deployment = None
        self.deployment_file = None
        self.deployment_path = None
        self.sha256 = None

    @coroutine
    def get(self, game_name, game_version):

        env_service = self.application.env_service

        try:
            app = yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        result = {
            "app_name": app["title"]
        }

        raise a.Return(result)

    @coroutine
    def receive_started(self, filename):

        if not filename.endswith(".zip"):
            raise a.ActionError("The file passed is not a zip file.")

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")

        deployments = self.application.deployments
        location = deployments.deployments_location

        env_service = self.application.env_service

        try:
            app = yield env_service.get_app_info(self.gamespace, game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")
        else:
            versions = app["versions"]
            if not game_version in versions:
                raise a.ActionError("No such app version")

        if not os.path.isdir(location):
            raise a.ActionError("Bad deployment location (server error)")

        try:
            self.deployment = yield deployments.new_deployment(
                self.gamespace, game_name, game_version, "")
        except DeploymentError as e:
            raise a.ActionError(e.message)

        app_location = os.path.join(location, game_name)

        if not os.path.isdir(app_location):
            os.mkdir(app_location)

        version_location = os.path.join(location, game_name, game_version)

        if not os.path.isdir(version_location):
            os.mkdir(version_location)

        self.deployment_path = os.path.join(location, game_name, game_version, str(self.deployment) + ".zip")
        self.deployment_file = open(self.deployment_path, "w")
        self.sha256 = hashlib.sha256()

    @coroutine
    def receive_completed(self):

        deployments = self.application.deployments

        self.deployment_file.close()

        the_zip_file = zipfile.ZipFile(self.deployment_path)

        try:
            ret = the_zip_file.testzip()
        except Exception as e:
            try:
                yield deployments.update_deployment_status(self.gamespace, self.deployment, "corrupt")
            except DeploymentError as e:
                raise a.ActionError("Corrupted deployment, failed to update: " + e.message)
            raise a.ActionError("Corrupted deployment: " + e.message)
        else:
            if ret:
                try:
                    yield deployments.update_deployment_status(self.gamespace, self.deployment, "corrupt")
                except DeploymentError as e:
                    raise a.ActionError("Corrupted deployment file, failed to update: " + e.message)

                raise a.ActionError("Corrupted deployment file: " + str(ret))

        deployment_hash = self.sha256.hexdigest()

        try:
            yield deployments.update_deployment_hash(self.gamespace, self.deployment, deployment_hash)
        except DeploymentError as e:
            raise a.ActionError("Failed to update hash: " + e.message)

        try:
            yield deployments.update_deployment_status(self.gamespace, self.deployment, "uploaded")
        except DeploymentError as e:
            raise a.ActionError("Failed to update deployment status: " + e.message)

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")

        delivery = Delivery(self.application, self.gamespace)

        yield delivery.__deliver__(game_name, game_version, self.deployment, deployment_hash)

        raise a.Redirect(
            "app_version",
            message="Game server has been deployed",
            app_id=game_name,
            version_id=game_version)

    @run_on_executor
    def receive_data(self, chunk):
        self.deployment_file.write(chunk)
        self.sha256.update(chunk)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("apps", "Applications"),
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
                a.link("app_version", self.context.get("game_version"),
                       app_id=self.context.get("game_name"), version_id=self.context.get("game_version"))
            ], "New Deployment"),

            a.file_upload("Deploy <b>{0}</b> / version <b>{1}</b>".format(
                data["app_name"], self.context.get("game_version")
            )),

            a.links("Navigate", [
                a.link("app_version", "Go back",
                       app_id=self.context.get("game_name"),
                       version_id=self.context.get("game_version"))
            ])
        ]

    def access_scopes(self):
        return ["game_deploy_admin"]


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
                "internal_location": a.field("Internal location (including scheme)", "text", "primary", "non-empty",
                                             order=2),
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
                a.link("hosts", "Hosts", icon="server"),
                a.link("bans", "Bans", icon="ban")
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
            "host_default": "true" if host.default else "false",
            "host_enabled": "true" if host.enabled else "false"
        }

        raise a.Return(result)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("hosts", "Hosts")
            ], data["name"]),
            a.links("Debug", [
                a.link("debug_host", "Debug this host", icon="bug", host_id=self.context.get("host_id")),
            ]),
            a.form("Host '{0}' information".format(data["name"]), fields={
                "host_enabled": a.field("Enabled (can accept players)", "switch", "primary", order=0),
                "name": a.field("Host name", "text", "primary", "non-empty", order=1),
                "internal_location": a.field("Internal location (including scheme)", "text", "primary", "non-empty",
                                             order=2),
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
                a.link("hosts", "Go back")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    @coroutine
    def update(self, name, internal_location, host_default="false", host_enabled="false"):
        host_id = self.context.get("host_id")
        hosts = self.application.hosts

        yield hosts.update_host(
            host_id,
            name,
            internal_location,
            host_default == "true",
            host_enabled == "true")

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


class BansController(a.AdminController):
    def render(self, data):
        return [
            a.breadcrumbs([], "Bans"),
            a.links("Bans", [
                a.link("find_ban", "Find A Ban", icon="search"),
                a.link("new_ban", "Issue A Ban", icon="plus")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class FindBanController(a.AdminController):
    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("bans", "Bans")
            ], "Find A Ban"),
            a.split([
                a.form(title="Find by ID", fields={
                    "ban_id": a.field("Ban ID", "text", "primary", "number"),
                }, methods={
                    "search_id": a.method("Search", "primary")
                }, data=data),
                a.form(title="Find by ip", fields={
                    "ip": a.field("User IP", "text", "primary", "non-empty"),
                }, methods={
                    "search_ip": a.method("Search", "primary")
                }, data=data),
                a.form(title="Find by account number", fields={
                    "account": a.field("Account number", "text", "primary", "number")
                }, methods={
                    "search_account": a.method("Search", "primary")
                }, data=data)
            ]),
            a.links("Navigate", [
                a.link("index", "Go back")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    @coroutine
    def search_account(self, account):
        bans = self.application.bans

        try:
            ban = yield bans.get_ban_by_account(self.gamespace, account)
        except NoSuchBan:
            raise a.ActionError("No such ban")

        raise a.Redirect("ban", ban_id=ban.ban_id)

    @coroutine
    def search_ip(self, ip):
        bans = self.application.bans

        try:
            ban_id = yield bans.get_ban_by_ip(self.gamespace, ip)
        except NoSuchBan:
            raise a.ActionError("No such ban")

        raise a.Redirect("ban", ban_id=ban_id)

    @coroutine
    def search_id(self, ban_id):
        bans = self.application.bans

        try:
            yield bans.get_ban(self.gamespace, ban_id)
        except NoSuchBan:
            raise a.ActionError("No such ban")

        raise a.Redirect("ban", ban_id=ban_id)


class IssueBanController(a.AdminController):

    @coroutine
    def get(self):
        raise Return({
            "expires": str(datetime.datetime.now() + datetime.timedelta(days=7))
        })

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("bans", "Bans")
            ], "Issue a Ban"),

            a.form("New ban", fields={
                "account_id": a.field(
                    "Account ID",
                    "text", "primary", "number", order=0),
                "reason": a.field(
                    "Reason",
                    "text", "primary", "non-empty", order=1),
                "expires": a.field(
                    "Expires",
                    "date", "primary", "non-empty", order=2)
            }, methods={
                "create": a.method("Create", "primary", order=1)
            }, data=data),
            a.links("Navigate", [
                a.link("bans", "Go back")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    @coroutine
    def create(self, account_id, expires, reason):

        bans = self.application.bans

        try:
            ban_id = yield bans.new_ban(self.gamespace, account_id, expires, reason)
        except UserAlreadyBanned:
            raise a.ActionError("User already banned")
        except BanError as e:
            raise a.ActionError(e.message)

        raise a.Redirect(
            "ban",
            message="Ban has been issued",
            ban_id=ban_id)


class BanController(a.AdminController):

    @coroutine
    def get(self, ban_id):

        bans = self.application.bans

        try:
            ban = yield bans.get_ban(self.gamespace, ban_id)
        except NoSuchBan:
            raise a.ActionError("No such ban")
        except BanError as e:
            raise a.ActionError(e.message)

        raise Return({
            "account_id": ban.account,
            "expires": str(ban.expires),
            "ip": ban.ip,
            "reason": ban.reason
        })

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("bans", "Bans")
            ], self.context.get("ban_id")),

            a.form("Ban", fields={
                "account_id": a.field(
                    "Account ID",
                    "readonly", "primary", "number", order=0),
                "reason": a.field(
                    "Reason",
                    "text", "primary", "non-empty", order=1),
                "expires": a.field(
                    "Expires",
                    "date", "primary", "non-empty", order=2)
            }, methods={
                "update": a.method("Update", "primary", order=2),
                "delete": a.method("Delete", "danger", order=1)
            }, data=data),
            a.links("Navigate", [
                a.link("bans", "Go back")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    @coroutine
    def update(self, expires, reason, **ignored):

        bans = self.application.bans

        ban_id = self.context.get("ban_id")

        try:
            yield bans.update_ban(self.gamespace, ban_id, expires, reason)
        except BanError as e:
            raise a.ActionError(e.message)

        raise a.Redirect(
            "ban",
            message="Ban has been updated",
            ban_id=ban_id)

    @coroutine
    def delete(self, **ignored):

        bans = self.application.bans

        ban_id = self.context.get("ban_id")

        try:
            yield bans.delete_ban(self.gamespace, ban_id)
        except BanError as e:
            raise a.ActionError(e.message)

        raise a.Redirect(
            "bans",
            message="Ban has been deleted")
