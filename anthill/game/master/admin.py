from tornado.gen import multi
from tornado.ioloop import IOLoop
import tornado.httpclient

import anthill.common.admin as a
from anthill.common import run_on_executor
from anthill.common.server import Server
from anthill.common.environment import EnvironmentClient, AppNotFound
from anthill.common.database import format_conditions_json, ConditionError
from anthill.common.validate import validate, ValidationError
from anthill.common.jsonrpc import JsonRPCError, JSONRPC_TIMEOUT

from .model.gameserver import GameError, GameServerNotFound, GameVersionNotFound, GameServersModel, GameServerExists
from .model.host import HostNotFound, HostError, RegionNotFound, RegionError
from .model.deploy import DeploymentError, DeploymentNotFound, NoCurrentDeployment, DeploymentAdapter
from .model.deploy import DeploymentDeliveryError, DeploymentDeliveryAdapter
from .model.ban import NoSuchBan, BanError, UserAlreadyBanned
from .model.room import RoomQuery, RoomNotFound, RoomError

from concurrent.futures import ThreadPoolExecutor

from geoip import geolite2
from urllib import parse
import socket
import logging
import os
import zipfile
import hashlib
import datetime
import math
import re
import ujson


class ApplicationController(a.AdminController):
    async def get(self, record_id):

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers

        try:
            app = await environment_client.get_app_info(record_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            servers = await gameservers.list_game_servers(self.gamespace, record_id)
        except GameError as e:
            raise a.ActionError("Failed to list game servers: " + str(e))

        app_versions = sorted(list(app.versions.keys()))

        result = {
            "app_id": record_id,
            "app_record_id": app.id,
            "app_name": app.title,
            "versions": app_versions,
            "game_servers": servers
        }

        return result

    def render(self, data):

        game_name = self.context.get("record_id")

        return [
            a.breadcrumbs([], data["app_name"]),
            a.links("Application '{0}' versions".format(data["app_name"]), links=[
                a.link("app_version", v_name, icon="tags", app_id=game_name,
                       version_id=v_name) for v_name in data["versions"]
            ]),
            a.links("Game Server Configurations", links=[
                a.link("game_server", gs.name, icon="rocket", game_server_id=gs.game_server_id, game_name=game_name)
                for gs in data["game_servers"]
            ]),
            a.links("Navigate", [
                a.link("index", "Go back", icon="chevron-left"),
                a.link("rooms", "See game rooms", icon="th-large", game_name=game_name),
                a.link("new_game_server", "Create Game Server",
                       icon="plus", game_name=game_name),
                a.link("/environment/app", "Manage app '{0}' at 'Environment' service.".format(data["app_name"]),
                       icon="link text-danger", record_id=data["app_record_id"]),
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class GameServerController(a.AdminController):
    async def get(self, game_server_id, game_name):

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            gs = await gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        result = {
            "app_name": app.title,
            "max_players": gs.max_players,
            "game_settings": gs.game_settings,
            "server_settings": gs.server_settings,
            "game_server_name": gs.name,
            "schema": gs.schema
        }

        return result

    def render(self, data):

        return [
            a.breadcrumbs([
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
            ], data["game_server_name"]),

            a.form("Game Server Configuration", fields={
                "game_server_name": a.field(
                    "Game Server Configuration Name",
                    "text", "primary", "non-empty", order=0),
                "game_settings": a.field(
                    "Configuration", "dorn",
                    "primary", "non-empty", schema=GameServersModel.GAME_SETTINGS_SCHEME, order=1),
                "server_settings": a.field(
                    "Custom Server Configuration Settings (set as "
                    "<span class=\"label label-default\">server_settings</span> environment variable)",
                    "dorn", "primary", "non-empty", schema=data["schema"], order=2),
                "max_players": a.field("Max players per room", "text", "primary", "number", order=4),
                "schema": a.field(
                    "Game Server Configuration Settings Schema", "json", "primary", "non-empty", order=5)
            }, methods={
                "update": a.method("Update", "primary", order=1),
                "delete": a.method("Delete", "danger", order=2)
            }, data=data),
            a.links("Navigate", [
                a.link("app", "Go back", icon="chevron-left", record_id=self.context.get("game_name")),
                a.link("new_game_server", "Clone Game Server", icon="clone",
                       game_name=self.context.get("game_name"),
                       game_server_id=self.context.get("game_server_id")),
                a.link("https://spacetelescope.github.io/understanding-json-schema/index.html", "See docs", icon="book")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    async def delete(self, **ignored):

        game_server_id = self.context.get("game_server_id")
        game_name = self.context.get("game_name")

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers

        try:
            await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            await gameservers.delete_game_server(self.gamespace, game_name, game_server_id)
        except GameError as e:
            raise a.ActionError("Failed to delete game server: " + str(e))

        raise a.Redirect(
            "app",
            message="Game server has been deleted",
            record_id=game_name)

    async def update(self, game_server_name, schema, max_players, game_settings, server_settings, **ignored):

        game_server_id = self.context.get("game_server_id")
        game_name = self.context.get("game_name")

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers

        try:
            game_settings = ujson.loads(game_settings)
            server_settings = ujson.loads(server_settings)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            schema = ujson.loads(schema)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            await gameservers.update_game_server(
                self.gamespace, game_name, game_server_id, game_server_name,
                schema, max_players, game_settings, server_settings)
        except GameError as e:
            raise a.ActionError("Failed: " + str(e))

        raise a.Redirect(
            "game_server",
            message="Settings have been updated",
            game_name=game_name,
            game_server_id=game_server_id)


class NewGameServerController(a.AdminController):
    async def get(self, game_name, game_server_id=None):

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        result = {
            "app_name": app.title,
            "schema": GameServersModel.DEFAULT_SERVER_SCHEME,
            "max_players": "8"
        }

        if game_server_id:
            try:
                gs = await gameservers.get_game_server(self.gamespace, game_name, game_server_id)
            except GameServerNotFound:
                raise a.ActionError("No such game server to clone from")

            result.update({
                "max_players": gs.max_players,
                "game_settings": gs.game_settings,
                "server_settings": gs.server_settings,
                "game_server_name": gs.name,
                "schema": gs.schema
            })

        return result

    def render(self, data):

        return [
            a.breadcrumbs([
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
            ], "New game server"),

            a.form("Game Server Configuration", fields={
                "game_server_name": a.field(
                    "Game Server Configuration Name",
                    "text", "primary", "non-empty", order=0),
                "game_settings": a.field(
                    "Configuration", "dorn",
                    "primary", "non-empty", schema=GameServersModel.GAME_SETTINGS_SCHEME, order=1),
                "max_players": a.field("Max players per room", "text", "primary", "number", order=4),
                "schema": a.field(
                    "Custom Game Server Configuration Schema", "json", "primary", "non-empty", order=5)
            }, methods={
                "create": a.method("Create", "primary", order=1)
            }, data=data),
            a.links("Navigate", [
                a.link("app", "Go back", icon="chevron-left", record_id=self.context.get("game_name")),
                a.link("https://spacetelescope.github.io/understanding-json-schema/index.html", "See docs", icon="book")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    async def create(self, game_server_name, schema, max_players, game_settings, **ignored):

        game_name = self.context.get("game_name")

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers

        try:
            game_settings = ujson.loads(game_settings)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            schema = ujson.loads(schema)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            game_server_id = await gameservers.create_game_server(
                self.gamespace, game_name, game_server_name,
                schema, max_players, game_settings, {})
        except GameError as e:
            raise a.ActionError("Failed: " + str(e))
        except GameServerExists:
            raise a.ActionError("Such Game Server already exists")

        raise a.Redirect(
            "game_server",
            message="Settings have been updated",
            game_name=game_name,
            game_server_id=game_server_id)


class GameServerVersionController(a.AdminController):
    async def delete(self, **ignored):

        gameservers = self.application.gameservers

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        game_server_id = self.context.get("game_server_id")

        try:
            await gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        try:
            await gameservers.delete_game_version(self.gamespace, game_name, game_version, game_server_id)
        except GameError as e:
            raise a.ActionError("Failed to delete version config: " + str(e))

        raise a.Redirect(
            "app_version",
            message="Version config has been deleted",
            app_id=game_name,
            version_id=game_version)

    async def get(self, game_name, game_version, game_server_id):

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            gs = await gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        try:
            version_settings = await gameservers.get_version_game_server(
                self.gamespace, game_name, game_version, game_server_id)

        except GameVersionNotFound:
            version_settings = {}

        result = {
            "app_name": app.title,
            "version_settings": version_settings,
            "game_server_name": gs.name,
            "schema": gs.schema
        }

        return result

    def render(self, data):
        config = []

        if not data["version_settings"]:
            config.append(a.notice(
                "Default Configuration",
                "This version ({0}) has no configuration, so default Game Server Configuration ({1}) applied. "
                "Edit the configuration below to overwrite it.".format(
                    self.context.get("game_version"), data["game_server_name"]
                )))

        config.extend([
            a.breadcrumbs([
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
                a.link("app_version", self.context.get("game_version"),
                       app_id=self.context.get("game_name"), version_id=self.context.get("game_version")),
                a.link("game_server", data["game_server_name"], game_server_id=self.context.get("game_server_id"),
                       game_name=self.context.get("game_name")),

            ], "Custom Server Configuration Settings"),

            a.form(title="Custom Server Configuration Settings for {0}/{1}".format(
                data["game_server_name"], self.context.get("game_version")), fields={
                "server_settings": a.field(
                    "Custom Server Configuration Settings (set as "
                    "<span class=\"label label-default\">server:settings</span> environment variable)",
                    "dorn", "primary", "non-empty", schema=data["schema"])
            }, methods={
                "update": a.method("Update", "primary"),
                "delete": a.method("Delete", "danger")
            }, data=data),

            a.links("Navigate", [
                a.link("app_version", "Go back", icon="chevron-left",
                       app_id=self.context.get("game_name"), version_id=self.context.get("game_version"))
            ])
        ])

        return config

    def access_scopes(self):
        return ["game_admin"]

    async def update(self, server_settings, **ignored):

        gameservers = self.application.gameservers

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        game_server_id = self.context.get("game_server_id")

        try:
            await gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        try:
            server_settings = ujson.loads(server_settings)
        except ValueError:
            raise a.ActionError("Corrupted JSON")

        try:
            await gameservers.set_version_game_server(
                self.gamespace, game_name, game_version, game_server_id, server_settings)

        except GameError as e:
            raise a.ActionError("Failed to update version config: " + str(e))

        raise a.Redirect(
            "game_server_version",
            message="Version config has been updated",
            game_name=game_name,
            game_version=game_version,
            game_server_id=game_server_id)


class ApplicationVersionController(a.AdminController):
    DEPLOYMENTS_PER_PAGE = 10

    async def switch_deployment(self, **ignored):
        deployments = self.application.deployments

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        deployment_id = self.context.get("deployment_id")

        try:
            deployment = await deployments.get_deployment(self.gamespace, deployment_id)
        except DeploymentError as e:
            raise a.ActionError("Failed to get game deployment: " + str(e))
        except DeploymentNotFound as e:
            raise a.ActionError("No such deployment")

        if deployment.status != "delivered":
            raise a.ActionError("Deployment is not delivered yet, cannot switch")

        try:
            await deployments.update_game_version_deployment(
                self.gamespace, game_name, game_version, deployment_id, True)
        except DeploymentError as e:
            raise a.ActionError("Failed to set game deployment: " + str(e))

        raise a.Redirect("app_version",
                         message="Deployment has been switched",
                         app_id=game_name,
                         version_id=game_version)

    async def delete_deployment(self, **ignored):
        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        deployment_id = self.context.get("deployment_id")

        deployments = self.application.deployments
        hosts = self.application.hosts

        try:
            deployment = await deployments.get_deployment(self.gamespace, deployment_id)
        except DeploymentNotFound:
            raise a.ActionError("No such deployment")
        else:
            if (deployment.game_name != game_name) or (deployment.game_version != game_version):
                raise a.ActionError("Wrong deployment")

        if deployment.status in [DeploymentAdapter.STATUS_DELETED, DeploymentAdapter.STATUS_DELETING,
                                 DeploymentAdapter.STATUS_DELIVERING, DeploymentAdapter.STATUS_UPLOADING]:
            raise a.ActionError("Cannot delete already deleted deployment or one in progress")

        try:
            hosts_list = await hosts.list_enabled_hosts()
        except HostError as e:
            raise a.ActionError("Failed to list hosts: " + str(e))

        deliver_list = []
        try:
            deliveries = await deployments.list_deployment_deliveries(self.gamespace, deployment_id)
        except DeploymentDeliveryError as e:
            raise a.ActionError("Failed to list deliveries: " + str(e))
        else:
            host_ids = {item.host_id: item for item in hosts_list}
            for delivery in deliveries:
                deliver_list.append((delivery.delivery_id, host_ids[delivery.host_id]))

        delivery = Delivery(self.application, self.gamespace)
        IOLoop.current().add_callback(delivery.__clean__, deployment, deliver_list=deliver_list)

        raise a.Redirect(
            "app_version",
            message="Deployment deleting process been started",
            app_id=game_name,
            version_id=game_version
        )

    async def version_disable(self, **ignored):
        deployments = self.application.deployments

        game_name = self.context.get("app_id")
        game_version = self.context.get("version_id")

        try:
            current_deployment = await deployments.get_current_deployment(self.gamespace, game_name, game_version)
        except NoCurrentDeployment as e:
            raise a.ActionError("No current deployment")

        try:
            await deployments.update_game_version_deployment(
                self.gamespace, game_name, game_version, current_deployment.deployment_id, False)
        except DeploymentError as e:
            raise a.ActionError("Failed to set game deployment: " + str(e))

        raise a.Redirect("app_version",
                         message="Game version has been turned off",
                         app_id=game_name,
                         version_id=game_version)

    async def version_enable(self, **ignored):
        deployments = self.application.deployments

        game_name = self.context.get("app_id")
        game_version = self.context.get("version_id")

        try:
            current_deployment = await deployments.get_current_deployment(self.gamespace, game_name, game_version)
        except NoCurrentDeployment as e:
            raise a.ActionError("No current deployment")

        try:
            await deployments.update_game_version_deployment(
                self.gamespace, game_name, game_version, current_deployment.deployment_id, True)
        except DeploymentError as e:
            raise a.ActionError("Failed to set game deployment: " + str(e))

        raise a.Redirect("app_version",
                         message="Game version has been turned on",
                         app_id=game_name,
                         version_id=game_version)

    async def get(self, app_id, version_id, page=1):

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers
        deployments = self.application.deployments

        try:
            app = await environment_client.get_app_info(app_id)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            servers = await gameservers.list_game_servers(self.gamespace, app_id)
        except GameError as e:
            raise a.ActionError("Failed to list game servers: " + str(e))

        try:
            game_deployments, pages = await deployments.list_paged_deployments(
                self.gamespace, app_id, version_id, ApplicationVersionController.DEPLOYMENTS_PER_PAGE, page)
        except DeploymentError as e:
            raise a.ActionError("Failed to list game deployments: " + str(e))

        try:
            current_deployment = await deployments.get_current_deployment(self.gamespace, app_id, version_id)
        except NoCurrentDeployment:
            current_deployment = None
            deployment_enabled = False
        except DeploymentError as e:
            raise a.ActionError("Failed to get current deployment: " + str(e))
        else:
            deployment_enabled = current_deployment.enabled
            current_deployment = current_deployment.deployment_id

        result = {
            "app_id": app_id,
            "app_name": app.title,
            "servers": servers,
            "deployments": game_deployments,
            "pages": pages,
            "current_deployment": current_deployment,
            "deployment_enabled": deployment_enabled,
            "deployment_enabled_title": "Enabled" if deployment_enabled else "Disabled"
        }

        return result

    def render(self, data):

        current_deployment = data["current_deployment"]
        deployment_enabled = data["deployment_enabled"]

        r = [
            a.breadcrumbs([
                a.link("app", data["app_name"], record_id=self.context.get("app_id"))
            ], self.context.get("version_id"))
        ]

        if current_deployment:
            r.append(a.form("Game Version " + str(self.context.get("version_id")) + " Status", fields={
                "deployment_enabled_title":
                    a.field("Status", "status",
                            "success" if deployment_enabled else "danger")
            }, methods={
                "version_disable" if deployment_enabled else "version_enable": a.method(
                    "Turn OFF" if deployment_enabled else "Turn ON",
                    "danger" if deployment_enabled else "success",
                    danger="Turning this game version OFF will make impossible for "
                           "players to create new rooms of this version"
                    if deployment_enabled else None)
            }, data=data))
        else:
            r.append(a.notice(
                "Warning",
                "There is no current deployment set for version <b>{0}</b>. "
                "Therefore, server spawning is not possible. "
                "Please deploy and switch to required deployment.".format(
                    self.context.get("version_id")
                )
            ))

        r.append(
            a.links("Upload New Deployment", [
                a.link("deploy", "Deploy New Game Server", icon="upload",
                       game_name=self.context.get("app_id"),
                       game_version=self.context.get("version_id"))
            ]))

        def get_buttons(item):
            if item.status not in (DeploymentAdapter.STATUS_DELETING, DeploymentAdapter.STATUS_DELETED):
                return [a.button("app_version", "Set Current", "primary", _method="switch_deployment",
                                 game_name=self.context.get("app_id"),
                                 game_version=self.context.get("version_id"),
                                 deployment_id=item.deployment_id),
                        a.button("app_version", "Delete", "danger", _method="delete_deployment",
                                 game_name=self.context.get("app_id"),
                                 game_version=self.context.get("version_id"),
                                 deployment_id=item.deployment_id)] \
                    if (current_deployment != item.deployment_id) else "Current deployment"
            return []

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
                            DeploymentAdapter.STATUS_ERROR: a.status("Error", "danger", "exclamation-triangle"),
                            DeploymentAdapter.STATUS_DELETING: a.status("Deleting", "info", "refresh fa-spin"),
                            DeploymentAdapter.STATUS_DELETED: a.status("Deleted", "default", "times"),
                        }.get(item.status, a.status(item.status, "default", "refresh"))
                    ],

                    "actions": get_buttons(item)
                }
                for item in data["deployments"]
            ], style="primary", empty="There is no deployments"),
        ])

        if data["pages"] > 1:
            r.append(a.pages(data["pages"]))

        r.extend([
            a.links("Game Servers Configurations for game version {0}".format(self.context.get("version_id")), links=[
                a.link(
                    "game_server_version", gs.name, icon="rocket",
                    game_name=self.context.get("app_id"),
                    game_version=self.context.get("version_id"),
                    game_server_id=gs.game_server_id)
                for gs in data["servers"]
            ]),

            a.links("Navigate", [
                a.link("app", "Go back", icon="chevron-left", record_id=self.context.get("app_id"))
            ])
        ])

        return r

    def access_scopes(self):
        return ["game_admin"]


class Delivery(object):
    def __init__(self, application, gamespace):
        self.application = application
        self.gamespace = gamespace

    async def __deliver_host__(self, game_name, game_version, deployment_id, delivery_id, host, deployment_hash):
        deployments = self.application.deployments
        rpc = self.application.rpc.acquire_rpc("admin")

        try:
            result = await rpc.send_mq_request(
                "game_host_{0}".format(host.host_id),
                "deploy_delivery", 600, game_name, game_version, deployment_id, deployment_hash)
        except Exception as e:
            await deployments.update_deployment_delivery_status(
                self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_ERROR, "Cannot deliver")
            logging.exception("Cannot deliver")
            raise DeploymentDeliveryError(str(e))

        if result:
            await deployments.update_deployment_delivery_status(
                self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_DELIVERED)
        else:
            await deployments.update_deployment_delivery_status(
                self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_ERROR, "Cannot deliver")

            raise DeploymentDeliveryError("Cannot deliver")

    async def __deliver_upload__(self, game_name, game_version, deployment_id, deliver_list, deployment_hash):
        deployments = self.application.deployments

        tasks = [
            self.__deliver_host__(game_name, game_version, deployment_id, delivery_id, host, deployment_hash)
            for delivery_id, host in deliver_list
        ]

        try:
            await multi(tasks)
        except Exception as e:
            logging.error("Error deliver deployment {0}: {1}".format(
                deployment_id, str(e)
            ))
            await deployments.update_deployment_status(
                self.gamespace, deployment_id, DeploymentAdapter.STATUS_ERROR)
            return False
        else:
            await deployments.update_deployment_status(
                self.gamespace, deployment_id, DeploymentAdapter.STATUS_DELIVERED)
            return True

    async def __deliver_clean_host__(self, game_name, game_version, deployment_id, delivery_id, host):
        deployments = self.application.deployments

        await deployments.update_deployment_delivery_status(
            self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_DELETING)

        deployments = self.application.deployments
        rpc = self.application.rpc.acquire_rpc("admin")

        try:
            result = await rpc.send_mq_request(
                "game_host_{0}".format(host.host_id),
                "delete_delivery", JSONRPC_TIMEOUT, game_name, game_version, deployment_id)
        except Exception as e:
            await deployments.update_deployment_delivery_status(
                self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_ERROR, str(e))
            raise DeploymentDeliveryError(str(e))

        if result:
            await deployments.update_deployment_delivery_status(
                self.gamespace, delivery_id, DeploymentDeliveryAdapter.STATUS_DELETED)
        else:
            await deployments.update_deployment_delivery_status(
                self.gamespace, deployment_id, DeploymentDeliveryAdapter.STATUS_ERROR)

    async def __deliver__(self, game_name, game_version, deployment_id, deployment_hash, wait_for_deliver=False):
        hosts = self.application.hosts
        deployments = self.application.deployments

        try:
            hosts_list = list(await hosts.list_enabled_hosts())
        except HostError as e:
            raise a.ActionError("Failed to list hosts: " + str(e))

        try:
            deliveries = list(await deployments.list_deployment_deliveries(self.gamespace, deployment_id))
        except DeploymentDeliveryError as e:
            raise a.ActionError("Failed to list deliveries: " + str(e))

        deliver_list = []
        delivery_ids = {item.host_id: item for item in deliveries}
        host_ids = {item.host_id: item for item in hosts_list}

        for host in hosts_list:
            if host.host_id not in delivery_ids:
                new_delivery_id = await deployments.new_deployment_delivery(
                    self.gamespace, deployment_id, host.host_id)
                deliver_list.append((new_delivery_id, host))

        for delivery in deliveries:
            if delivery.status == DeploymentDeliveryAdapter.STATUS_ERROR:
                deliver_list.append((delivery.delivery_id, host_ids[delivery.host_id]))

        if not deliver_list:
            raise a.ActionError("Nothing to deliver")

        try:
            await deployments.update_deployment_status(
                self.gamespace, deployment_id, DeploymentAdapter.STATUS_DELIVERING)
        except DeploymentError as e:
            raise a.ActionError("Failed to update deployment status: " + str(e))

        try:
            await deployments.update_deployment_deliveries_status(
                self.gamespace, [
                    delivery_id
                    for delivery_id, host in deliver_list
                ], DeploymentDeliveryAdapter.STATUS_DELIVERING)
        except DeploymentDeliveryError as e:
            await deployments.update_deployment_status(
                self.gamespace, deployment_id, DeploymentAdapter.STATUS_ERROR)
            raise a.ActionError("Failed to update deployment deliveries status: " + str(e))

        if wait_for_deliver:
            result = await self.__deliver_upload__(
                game_name, game_version, deployment_id, deliver_list, deployment_hash)
            return result
        else:
            IOLoop.current().spawn_callback(
                self.__deliver_upload__, game_name, game_version, deployment_id, deliver_list, deployment_hash)

    async def __clean__(self, deployment, deliver_list=None):
        deployments = self.application.deployments

        try:
            await deployments.update_deployment_status(
                self.gamespace, deployment.deployment_id, DeploymentAdapter.STATUS_DELETING)
        except DeploymentError as e:
            raise a.ActionError("Failed to update deployment status: " + str(e))

        tasks = [
            self.__deliver_clean_host__(deployment.game_name, deployment.game_version,
                                        deployment.deployment_id, delivery_id, host)
            for delivery_id, host in deliver_list
        ]

        try:
            await multi(tasks)
        except Exception as e:
            logging.exception("Failed to delete deployment {0}".format(deployment.deployment_id))
            await deployments.update_deployment_status(
                self.gamespace, deployment.deployment_id, DeploymentAdapter.STATUS_ERROR)
            raise a.ActionError("Failed to delete deployment: " + str(e))

        try:
            await deployments.delete_deployment_file(self.gamespace, deployment)
        except DeploymentError as e:
            raise a.ActionError("Failed to remove deployment: " + str(e))
        except DeploymentNotFound:
            raise a.ActionError("No such deployment")

        try:
            await deployments.update_deployment_status(
                self.gamespace, deployment.deployment_id, DeploymentAdapter.STATUS_DELETED)
        except DeploymentError as e:
            raise a.ActionError("Failed to update deployment status: " + str(e))


class ApplicationDeploymentController(a.AdminController):
    async def get(self, game_name, game_version, deployment_id):

        environment_client = EnvironmentClient(self.application.cache)
        deployments = self.application.deployments
        hosts = self.application.hosts

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            deployment = await deployments.get_deployment(self.gamespace, deployment_id)
        except DeploymentNotFound:
            raise a.ActionError("No such deployment")
        else:
            if (deployment.game_name != game_name) or (deployment.game_version != game_version):
                raise a.ActionError("Wrong deployment")

        try:
            deliveries = await deployments.list_deployment_deliveries(self.gamespace, deployment_id)
        except DeploymentDeliveryError as e:
            raise a.ActionError("Failed to fetch deliveries: " + str(e))

        try:
            hosts_list = await hosts.list_hosts()
        except HostError as e:
            raise a.ActionError("Failed to list hosts: " + str(e))

        result = {
            "app_name": app.title,
            "deployment_status_value": deployment.status,
            "deployment_status": deployment.status.title(),
            "deliveries": deliveries,
            "hosts": {
                item.host_id: item
                for item in hosts_list
            }
        }

        return result

    def render(self, data):
        return [
            a.breadcrumbs([
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
                    DeploymentAdapter.STATUS_DELETING: "info",
                    DeploymentAdapter.STATUS_DELETED: "default",
                }.get(data["deployment_status_value"], "info"), icon={
                    DeploymentAdapter.STATUS_UPLOADING: "refresh fa-spin",
                    DeploymentAdapter.STATUS_DELIVERING: "refresh fa-spin",
                    DeploymentAdapter.STATUS_UPLOADED: "check",
                    DeploymentAdapter.STATUS_DELIVERED: "check",
                    DeploymentAdapter.STATUS_ERROR: "exclamation-triangle",
                    DeploymentAdapter.STATUS_DELETING: "refresh fa-spin",
                    DeploymentAdapter.STATUS_DELETED: "times",
                }.get(data["deployment_status_value"], "refresh fa-spin"))
            }, methods={
                "deliver": a.method("Deliver again", "primary")
            } if data["deployment_status"] not in [
                DeploymentAdapter.STATUS_DELIVERING,
                DeploymentAdapter.STATUS_UPLOADING
            ] else {}, data=data, icon="cloud-upload"),

            a.content("Host delivery status", [
                {
                    "id": "host_address",
                    "title": "Host Address"
                },
                {
                    "id": "delivery_status",
                    "title": "Delivery status"
                },
            ], [
                  {
                      "host_address": data["hosts"][item.host_id].address if item.host_id in data[
                          "hosts"] else "Unknown",
                      "delivery_status": [
                          {
                              DeploymentDeliveryAdapter.STATUS_DELIVERING:
                                  a.status("Delivering", "info", "refresh fa-spin"),
                              DeploymentDeliveryAdapter.STATUS_DELIVERED:
                                  a.status("Delivered", "success", "check"),
                              DeploymentDeliveryAdapter.STATUS_DELETING:
                                  a.status("Deleting", "info", "refresh fa-spin"),
                              DeploymentDeliveryAdapter.STATUS_DELETED:
                                  a.status("Deleted", "default", "times"),
                              DeploymentDeliveryAdapter.STATUS_ERROR:
                                  a.status("Error: " + item.error_reason, "danger", "exclamation-triangle")
                          }.get(item.status, a.status(item.status, "default", "refresh")),
                      ]
                  }
                  for item in data["deliveries"]
              ], "primary"),

            a.links("Navigate", [
                a.link("app_version", "Go back", icon="chevron-left",
                       app_id=self.context.get("game_name"),
                       version_id=self.context.get("game_version"))
            ])
        ]

    def access_scopes(self):
        return ["game_deploy_admin"]

    async def deliver(self, **ignored):

        environment_client = EnvironmentClient(self.application.cache)
        deployments = self.application.deployments
        hosts = self.application.hosts

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")
        deployment_id = self.context.get("deployment_id")

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        try:
            deployment = await deployments.get_deployment(self.gamespace, deployment_id)
        except DeploymentNotFound:
            raise a.ActionError("No such deployment")
        else:
            if (deployment.game_name != game_name) or (deployment.game_version != game_version):
                raise a.ActionError("Wrong deployment")

        deployment_hash = deployment.hash

        delivery = Delivery(self.application, self.gamespace)

        await delivery.__deliver__(game_name, game_version, deployment_id, deployment_hash)

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
        self.auto_switch = False

    async def get(self, game_name, game_version):

        environment_client = EnvironmentClient(self.application.cache)

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        result = {
            "app_name": app.title,
            "switch_to_new": "true"
        }

        return result

    async def receive_started(self, filename, args):

        if not filename.endswith(".zip"):
            raise a.ActionError("The file passed is not a zip file.")

        self.auto_switch = args.get("switch_to_new", "false") == "true"

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")

        deployments = self.application.deployments
        location = deployments.deployments_location

        environment_client = EnvironmentClient(self.application.cache)

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")
        else:
            versions = app.versions
            if not game_version in versions:
                raise a.ActionError("No such app version")

        if not os.path.isdir(location):
            raise a.ActionError("Bad deployment location (server error)")

        try:
            self.deployment = await deployments.new_deployment(
                self.gamespace, game_name, game_version, "")
        except DeploymentError as e:
            raise a.ActionError(str(e))

        app_location = os.path.join(location, game_name)

        if not os.path.isdir(app_location):
            os.mkdir(app_location)

        version_location = os.path.join(location, game_name, game_version)

        if not os.path.isdir(version_location):
            os.mkdir(version_location)

        self.deployment_path = os.path.join(location, game_name, game_version, str(self.deployment) + ".zip")
        self.deployment_file = open(self.deployment_path, "wb")
        self.sha256 = hashlib.sha256()

    @run_on_executor
    def test_zip(self, the_zip_file):
        return the_zip_file.testzip()

    async def receive_completed(self):

        deployments = self.application.deployments

        self.deployment_file.close()

        the_zip_file = zipfile.ZipFile(self.deployment_path)

        try:
            ret = await self.test_zip(the_zip_file)
        except Exception as e:
            try:
                await deployments.update_deployment_status(self.gamespace, self.deployment, "corrupt")
            except DeploymentError as e:
                raise a.ActionError("Corrupted deployment, failed to update: " + str(e))
            raise a.ActionError("Corrupted deployment: " + str(e))
        else:
            if ret:
                try:
                    await deployments.update_deployment_status(self.gamespace, self.deployment, "corrupt")
                except DeploymentError as e:
                    raise a.ActionError("Corrupted deployment file, failed to update: " + str(e))

                raise a.ActionError("Corrupted deployment file: " + str(ret))

        deployment_hash = self.sha256.hexdigest()

        try:
            await deployments.update_deployment_hash(self.gamespace, self.deployment, deployment_hash)
        except DeploymentError as e:
            raise a.ActionError("Failed to update hash: " + str(e))

        try:
            await deployments.update_deployment_status(self.gamespace, self.deployment, "uploaded")
        except DeploymentError as e:
            raise a.ActionError("Failed to update deployment status: " + str(e))

        game_name = self.context.get("game_name")
        game_version = self.context.get("game_version")

        delivery = Delivery(self.application, self.gamespace)

        if self.auto_switch:
            result = await delivery.__deliver__(
                game_name, game_version, self.deployment, deployment_hash,
                wait_for_deliver=True)

            if not result:
                raise a.Redirect(
                    "app_version",
                    message="Failed to deliver deployment, cannot switch automatically",
                    app_id=game_name,
                    version_id=game_version)

            await deployments.update_game_version_deployment(
                self.gamespace, game_name, game_version, self.deployment, True)
        else:
            await delivery.__deliver__(game_name, game_version, self.deployment, deployment_hash)

        raise a.Redirect(
            "app_version",
            message="Game server has been deployed and switched"
            if self.auto_switch else "Game server has been deployed",
            app_id=game_name,
            version_id=game_version)

    @run_on_executor
    def receive_data(self, chunk):
        self.deployment_file.write(chunk)
        self.sha256.update(chunk)

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("app", data["app_name"], record_id=self.context.get("game_name")),
                a.link("app_version", self.context.get("game_version"),
                       app_id=self.context.get("game_name"), version_id=self.context.get("game_version"))
            ], "New Deployment"),

            a.file_upload("Deploy <b>{0}</b> / version <b>{1}</b>".format(
                data["app_name"], self.context.get("game_version")
            ), fields={
                "switch_to_new": a.field("Switch to it once delivered to hosts", "switch", "primary")
            }, data=data),

            a.links("Navigate", [
                a.link("app_version", "Go back", icon="chevron-left",
                       app_id=self.context.get("game_name"),
                       version_id=self.context.get("game_version"))
            ])
        ]

    def access_scopes(self):
        return ["game_deploy_admin"]


class DebugControllerAction(a.StreamAdminController):
    def __init__(self, app, token, handler):
        super().__init__(app, token, handler)

        self.host_id = None
        self.host_address = None
        self.rpc = self.application.rpc.acquire_rpc("admin")
        self.session_id = None
        self.sub = None

    async def prepared(self, server, **ignored):
        hosts = self.application.hosts

        try:
            host = await hosts.get_host(server)
        except HostNotFound as e:
            raise a.ActionError("Server not found: " + str(server))

        self.host_id = host.host_id
        self.host_address = host.address

    async def on_opened(self, *args, **kwargs):
        self.sub = await Server.acquire_custom_subscriber("game_host_debug_{0}".format(self.host_id), round_robin=False)
        await self.sub.handle("host_global_debug_action", self._on_receive_debug_action, routing_key=str(self.host_id))

        self.session_id = await self.rpc.send_mq_request(
            "game_host_{0}".format(self.host_id), "debug_open", JSONRPC_TIMEOUT)

        await self.sub.handle("host_session_debug_action", self._on_receive_debug_action, routing_key=self.session_id)

        servers = await self.rpc.send_mq_request(
            "game_host_{0}".format(self.host_id), "debug_command",
            JSONRPC_TIMEOUT, session_id=self.session_id, debug_command="get_servers")

        if servers is not None:
            await self.send_rpc(self, "servers", servers)

    async def _on_receive_debug_action(self, payload):
        """
        Host controller sent an action to the websocket client
        host controller -> host handler -> [websocket client]
        """
        kind = payload.pop("kind")
        if kind == "debug_action":
            logging.info("Received debug action: {0}".format(ujson.dumps(payload)))
            debug_action = payload.pop("debug_action")
            kwargs = payload.pop("kwargs")
            await self.send_rpc(self, debug_action, **kwargs)

    async def on_closed(self):
        if self.sub:
            await self.sub.release()
            self.sub = None
        if self.rpc:
            await self.rpc.send_mq_rpc(
                "game_host_{0}".format(self.host_id), "debug_close", self.session_id)

    async def command_received(self, context, method, **kwargs):
        """
        A websocket command received from a client that debugs this host
        [websocket client] -> host handler -> host controller
        """
        try:
            response = await self.rpc.send_mq_request(
                "game_host_{0}".format(self.host_id), "debug_command", JSONRPC_TIMEOUT,
                session_id=self.session_id, debug_command=method, **kwargs)

        except TypeError as e:
            raise JsonRPCError(400, "Bad arguments: " + e.args[0])
        except a.StreamCommandError as e:
            raise JsonRPCError(e.code, e.message)
        except ValidationError as e:
            raise JsonRPCError(400, e.message)
        except Exception as e:
            raise JsonRPCError(500, "Error: " + str(e))

        return response


class DebugHostController(a.AdminController):
    async def get(self, host_id, **ignore):

        hosts = self.application.hosts

        try:
            host = await hosts.get_host(host_id)
        except HostNotFound:
            raise a.ActionError("Server not found")

        try:
            region = await hosts.get_region(host.region)
        except RegionNotFound:
            raise a.ActionError("Region not found")
        except RegionError as e:
            raise a.ActionError(str(e))

        return {
            "host": host,
            "region": region
        }

    def render(self, data):
        return [
            a.breadcrumbs([
                a.link("region", data["region"].name, region_id=data["region"].region_id),
                a.link("host", data["host"].address,
                       host_id=self.context.get("host_id"))
            ], "Debug"),
            a.script(self.application.module_path("static/admin/debug_controller.js"),
                     server=self.context.get("host_id"),
                     room=self.context.get("room_id")),
            a.links("Navigate", [
                a.link("server", "Go back", icon="chevron-left", host_id=self.context.get("host_id"))
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class RootAdminController(a.AdminController):
    async def get(self):

        environment_client = EnvironmentClient(self.application.cache)
        apps = await environment_client.list_apps()

        hosts = self.application.hosts

        try:
            regions_list = await hosts.list_regions()
        except RegionError as e:
            raise a.ActionError("Failed to fetch regions: " + str(e))

        result = {
            "apps": apps,
            "regions": regions_list
        }

        return result

    def render(self, data):
        return [
            a.links("Applications", links=[
                a.link("app", app_name, icon="mobile", record_id=app_id)
                for app_id, app_name in data["apps"].items()
            ]),
            a.links("Regions", links=[
                a.link("region", region.name, icon="globe", region_id=region.region_id)
                for region in data["regions"]
            ]),
            a.links("Bans", [
                a.link("find_active_ban", "Find A Ban", icon="search"),
                a.link("new_ban", "Issue A Ban", icon="plus"),
                a.link("mass_ban", "Issue Multiple Bans", icon="plus-square"),
            ]),
            a.links("Navigate", [
                a.link("/environment/apps", "Manage apps", icon="link text-danger"),
                a.link("new_region", "New region", "plus"),
                a.link("hosts", "See Full Hosts List", "server")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class HostController(a.AdminController):
    async def delete(self, *args, **kwargs):
        host_id = self.context.get("host_id")
        hosts = self.application.hosts

        try:
            host = await hosts.get_host(host_id)
        except HostNotFound:
            raise a.ActionError("No such host")

        await hosts.delete_host(host_id)

        await self.application.rpc.acquire_rpc("admin").send_mq_rpc("game_host_{0}".format(host_id), "shutdown")

        raise a.Redirect(
            "region",
            message="Host has been deleted",
            region_id=host.region)

    async def get(self, host_id):
        hosts = self.application.hosts

        try:
            host = await hosts.get_host(host_id)
        except HostNotFound:
            raise a.ActionError("No such host")

        try:
            region = await hosts.get_region(host.region)
        except RegionNotFound:
            raise a.ActionError("Region not found")
        except RegionError as e:
            raise a.ActionError(str(e))

        result = {
            "address": host.address,
            "host": host,
            "region": region,
            "host_enabled": "true" if host.enabled else "false"
        }

        return result

    def render(self, data):

        host = data["host"]

        return [
            a.breadcrumbs([
                a.link("region", data["region"].name, region_id=data["region"].region_id),
            ], data["address"]),
            a.links("Debug", [
                a.link("debug_host", "Debug this host", icon="bug", host_id=self.context.get("host_id")),
            ]),
            a.notice("Status", """
                STATUS: <b>{0}</b><br>
                CPU: <b>{1}%</b><br>
                MEMORY: <b>{2}%</b><br>
                Last heartbeat check: <b>{3}</b>
            """.format(host.state, host.cpu, host.memory, str(host.heartbeat))),
            a.form("Host '{0}' information".format(data["address"]), fields={
                "host_enabled": a.field("Enabled (can accept players)", "switch", "primary", order=0)
            }, methods={
                "update": a.method("Update", "primary", order=1),
                "delete": a.method("Delete", "danger", order=2,
                                   danger="Deleting this host will result in permanent shutdown on remote host, "
                                          "that can be fixed only by manually restarting it.")
            }, data=data)
        ]

    def access_scopes(self):
        return ["game_admin"]

    async def update(self, host_enabled="false"):
        host_id = self.context.get("host_id")
        hosts = self.application.hosts

        try:
            await hosts.update_host(
                host_id,
                host_enabled == "true")
        except HostError as e:
            raise a.ActionError("Failed to update host: " + str(e))

        raise a.Redirect("host",
                         message="Host has been updated",
                         host_id=host_id)


class FindBanController(a.AdminController):
    def render(self, data):
        return [
            a.breadcrumbs([], "Find A Ban"),
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
                a.link("index", "Go back", icon="chevron-left")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    async def search_account(self, account):
        bans = self.application.bans

        try:
            ban = await bans.get_ban_by_account(self.gamespace, account)
        except NoSuchBan:
            raise a.ActionError("No such ban")

        raise a.Redirect("ban", ban_id=ban.ban_id)

    async def search_ip(self, ip):
        bans = self.application.bans

        try:
            ban_id = await bans.get_ban_by_ip(self.gamespace, ip)
        except NoSuchBan:
            raise a.ActionError("No such ban")

        raise a.Redirect("ban", ban_id=ban_id)

    async def search_id(self, ban_id):
        bans = self.application.bans

        try:
            await bans.get_ban(self.gamespace, ban_id)
        except NoSuchBan:
            raise a.ActionError("No such ban")

        raise a.Redirect("ban", ban_id=ban_id)


class IssueBanController(a.AdminController):
    async def get(self):
        return {
            "expires": str(datetime.datetime.now() + datetime.timedelta(days=7))
        }

    def render(self, data):
        return [
            a.breadcrumbs([], "Issue a Ban"),

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
                a.link("index", "Go back", icon="chevron-left")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    async def create(self, account_id, expires, reason):

        bans = self.application.bans

        try:
            ban_id = await bans.new_ban(self.gamespace, account_id, expires, reason)
        except UserAlreadyBanned:
            raise a.ActionError("User already banned")
        except BanError as e:
            raise a.ActionError(str(e))

        raise a.Redirect(
            "ban",
            message="Ban has been issued",
            ban_id=ban_id)


class IssueMultipleBansController(a.AdminController):
    async def get(self):
        return {
            "expires": str(datetime.datetime.now() + datetime.timedelta(days=7))
        }

    def render(self, data):
        return [
            a.breadcrumbs([], "Issue Multiple Bans"),

            a.form("New bans", fields={
                "account_ids": a.field(
                    "Account IDs (sepatated with spaces, commas, or with newlines)",
                    "text", "primary", "number", order=0, multiline=10),
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
                a.link("index", "Go back", icon="chevron-left")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    async def create(self, account_ids, expires, reason):

        bans = self.application.bans

        accounts = re.findall('\d+', account_ids, re.MULTILINE)

        try:
            for account in accounts:
                await bans.new_ban(self.gamespace, account, expires, reason)
        except UserAlreadyBanned:
            raise a.ActionError("User already banned")
        except BanError as e:
            raise a.ActionError(str(e))

        raise a.Redirect(
            "mass_ban",
            message="Bans have been issued")


class BanController(a.AdminController):
    async def get(self, ban_id):

        bans = self.application.bans

        try:
            ban = await bans.get_ban(self.gamespace, ban_id)
        except NoSuchBan:
            raise a.ActionError("No such ban")
        except BanError as e:
            raise a.ActionError(str(e))

        return {
            "account_id": ban.account,
            "expires": str(ban.expires),
            "ip": ban.ip,
            "reason": ban.reason
        }

    def render(self, data):
        return [
            a.breadcrumbs([], self.context.get("ban_id")),

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
                a.link("index", "Go back", icon="chevron-left")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    async def update(self, expires, reason, **ignored):

        bans = self.application.bans

        ban_id = self.context.get("ban_id")

        try:
            await bans.update_ban(self.gamespace, ban_id, expires, reason)
        except BanError as e:
            raise a.ActionError(str(e))

        raise a.Redirect(
            "ban",
            message="Ban has been updated",
            ban_id=ban_id)

    async def delete(self, **ignored):

        bans = self.application.bans

        ban_id = self.context.get("ban_id")

        try:
            await bans.delete_ban(self.gamespace, ban_id)
        except BanError as e:
            raise a.ActionError(str(e))

        raise a.Redirect(
            "index",
            message="Ban has been deleted")


class RegionController(a.AdminController):
    async def delete(self, *args, **kwargs):
        region_id = self.context.get("region_id")
        hosts = self.application.hosts

        try:
            await hosts.delete_region(region_id)
        except RegionError as e:
            raise a.ActionError("Failed to delete region: " + str(e))

        raise a.Redirect(
            "index",
            message="Region has been deleted")

    async def get(self, region_id):
        hosts = self.application.hosts

        try:
            region = await hosts.get_region(region_id)
        except RegionNotFound:
            raise a.ActionError("Region not found")
        except RegionError as e:
            raise a.ActionError(str(e))

        hosts_list = await hosts.list_hosts(region.region_id)

        result = {
            "name": region.name,
            "hosts": hosts_list,
            "geo_location": str(region.geo_location),
            "region_default": "true" if region.default else "false",
            "settings": region.settings
        }

        return result

    def render(self, data):
        return [
            a.breadcrumbs([], data["name"]),

            a.content("Hosts", [
                {
                    "id": "address",
                    "title": "Host Address"
                },
                {
                    "id": "status",
                    "title": "Status"
                },
                {
                    "id": "enabled",
                    "title": "Enabled"
                },
                {
                    "id": "cpu",
                    "title": "CPU Load"
                },
                {
                    "id": "memory",
                    "title": "Memory Load"
                },
                {
                    "id": "storage",
                    "title": "Storage Usage"
                },
                {
                    "id": "heartbeat",
                    "title": "Last Check"
                },
                {
                    "id": "debug",
                    "title": "Debug"
                }
            ], [
                  {
                      "address": [
                          a.link("host", host.address,
                                 icon="thermometer-{0}".format(min(int(host.load / 20), 4)),
                                 host_id=host.host_id)
                      ],
                      "enabled": [
                          a.status(
                              "Yes" if host.enabled else "No",
                              "success" if host.enabled else "danger")],
                      "cpu": "{0} %".format(host.cpu) if host.active else "-",
                      "memory": "{0} %".format(host.memory) if host.active else "-",
                      "storage": "{0} %".format(host.storage) if host.active else "-",
                      "status": [
                          a.status(host.state, "success") if host.active else a.status(host.state, "danger")],
                      "heartbeat": str(host.heartbeat),
                      "debug": [a.link("debug_host", "", icon="bug", host_id=host.host_id)]
                  }
                  for host in data["hosts"]
              ], "primary", empty="No hosts to display"),

            a.form("Region '{0}' information".format(data["name"]), fields={
                "name": a.field("Region name", "text", "primary", "non-empty", order=1),
                "geo_location": a.field("Geo location", "readonly", "primary", order=2),
                "region_default": a.field(
                    "Default region (to connect to in case user cannot be located)", "switch", "primary", order=3),
                "settings": a.field("Settings", "json", "primary", order=4)
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
                a.link("hosts", "Go back", icon="chevron-left")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]

    async def update_geo(self, external_location):

        region_id = self.context.get("region_id")

        try:
            external_ip = socket.gethostbyname(external_location)
        except socket.gaierror:
            raise a.ActionError("Failed to lookup hostname")

        geo = geolite2.lookup(external_ip)

        if geo is None:
            raise a.ActionError("Failed to lookup IP address ({0})".format(external_ip))

        p_lat, p_long = geo.location

        hosts = self.application.hosts

        try:
            await hosts.update_region_geo_location(region_id, p_long, p_lat)
        except HostError as e:
            raise a.ActionError(str(e))

        raise a.Redirect("region",
                         message="Geo location updated",
                         region_id=region_id)

    @validate(name="str_name", region_default="bool", settings="load_json")
    async def update(self, name, region_default="false", settings="{}"):
        region_id = self.context.get("region_id")
        hosts = self.application.hosts

        try:
            await hosts.update_region(
                region_id,
                name,
                region_default,
                settings)
        except RegionError as e:
            raise a.ActionError("Failed to update region: " + str(e))

        raise a.Redirect("region",
                         message="Region has been updated",
                         region_id=region_id)


class NewRegionController(a.AdminController):
    @validate(name="str_name", region_default="bool", settings="load_json")
    async def create(self, name, region_default="false", settings="{}"):
        hosts = self.application.hosts

        try:
            region_id = await hosts.new_region(name, region_default, settings)
        except RegionError as e:
            raise a.ActionError("Failed to create new region: " + str(e))

        raise a.Redirect(
            "region",
            message="New region has been created",
            region_id=region_id)

    async def get(self):
        return {
            "settings": {}
        }

    def render(self, data):
        return [
            a.breadcrumbs([], "New region"),
            a.form("New region", fields={
                "name": a.field("Region name", "text", "primary", "non-empty", order=1),
                "region_default": a.field(
                    "Default region (to connect to in "
                    "case user cannot be located)", "switch", "primary", "non-empty", order=2),

                "settings": a.field("Region settings", "json", "primary", "non-empty", order=3),
            }, methods={
                "create": a.method("Create", "primary")
            }, data=data),
            a.links("Navigate", [
                a.link("@back", "Go back", icon="chevron-left")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]


class SpawnRoomController(a.AdminController):
    async def get(self, game_name):

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers
        hosts = self.application.hosts

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        game_versions = {
            v_name: v_name
            for v_name, v_id in app.versions.items()
        }

        try:
            game_servers = await gameservers.list_game_servers(self.gamespace, game_name)
        except GameError as e:
            raise a.ActionError(str(e))

        game_servers = {
            game_server.game_server_id: game_server.name
            for game_server in game_servers
        }

        try:
            game_regions = await hosts.list_regions()
        except RegionError as e:
            raise a.ActionError(str(e))

        game_regions = {
            region.region_id: region.name
            for region in game_regions
        }

        return {
            "game_name": game_name,
            "game_title": app.title,
            "game_versions": game_versions,
            "game_servers": game_servers,
            "game_regions": game_regions,
            "room_settings": {},
            "custom_settings": {},
            "max_players": 0
        }

    @validate(game_version="str", game_server_id="int", region_id="int", room_settings="load_json_dict",
              max_players="int", custom_settings="load_json_dict")
    async def spawn(self, game_version, game_server_id, region_id, room_settings, custom_settings, max_players=0):

        environment_client = EnvironmentClient(self.application.cache)
        hosts = self.application.hosts
        rooms = self.application.rooms
        gameservers = self.application.gameservers

        game_name = self.context.get("game_name")

        try:
            await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        room_settings = {
            key: value
            for key, value in room_settings.items()
            if isinstance(value, (str, int, float, bool))
        }

        try:
            deployment = await self.application.deployments.get_current_deployment(
                self.gamespace, game_name, game_version)
        except NoCurrentDeployment:
            raise a.ActionError("No deployment defined for {0}/{1}".format(
                game_name, game_version
            ))

        if not deployment.enabled:
            raise a.ActionError("Deployment is disabled for {0}/{1}".format(
                game_name, game_version
            ))

        try:
            gs = await gameservers.get_game_server(self.gamespace, game_name, game_server_id)
        except GameServerNotFound:
            raise a.ActionError("No such game server")

        game_settings = gs.game_settings

        try:
            server_settings = await gameservers.get_version_game_server(
                self.gamespace, game_name, game_version, gs.game_server_id)
        except GameVersionNotFound:
            server_settings = gs.server_settings

            if server_settings is None:
                raise a.ActionError("No default version configuration")

        deployment_id = deployment.deployment_id

        try:
            region = await hosts.get_region(region_id)
        except RegionNotFound:
            raise a.ActionError("Host not found")

        try:
            host = await hosts.get_best_host(region.region_id)
        except HostNotFound:
            raise a.ActionError("Not enough hosts")

        room_id = await rooms.create_room(
            self.gamespace, game_name, game_version,
            gs, room_settings, host, deployment_id, max_players=max_players)

        logging.info("Created a room: '{0}'".format(room_id))

        try:
            result = await rooms.spawn_server(
                self.gamespace, game_name, game_version, gs.name,
                deployment_id, room_id, host, game_settings, server_settings,
                room_settings, other_settings=custom_settings)
        except RoomError as e:
            raise a.ActionError(str(e))

        updated_room_settings = result.get("settings")

        if updated_room_settings:
            room_settings.update(updated_room_settings)
            await rooms.update_room_settings(self.gamespace, room_id, room_settings)

        raise a.Redirect("room", message="Successfully spawned a server", room_id=room_id)

    def render(self, data):
        return [
            a.breadcrumbs(items=[
                a.link("app", data["game_title"], record_id=data["game_name"]),
                a.link("rooms", "Rooms", game_name=data["game_name"]),
            ], title="Spawn a new game server"),
            a.form("Spawn a new game server", fields={
                "game_version": a.field("Game Version", "select", "primary", values=data["game_versions"], order=1),
                "game_server_id": a.field("Game Server", "select", "primary", values=data["game_servers"], order=2),
                "region_id": a.field("Game Region",
                                     "select", "primary", values=data["game_regions"], order=3),
                "room_settings": a.field("Room Settings",
                                         "json", "primary", order=4, height=120),
                "max_players": a.field("Max Players", "text", "primary", order=5,
                                       description="Leave 0 for default value depending on the "
                                                   "Game Server Configuration"),
                "custom_settings": a.field("Custom Settings",
                                           "json", "primary", order=6, height=120,
                                           description="Custom Environment variables to pass to the game server"),
            }, methods={
                "spawn": a.method("Spawn", "primary")
            }, data=data, icon="flash")
        ]


class RoomController(a.AdminController):
    async def get(self, room_id):

        rooms = self.application.rooms
        environment_client = EnvironmentClient(self.application.cache)

        try:
            room = await rooms.get_room(self.gamespace, room_id)
        except RoomNotFound:
            raise a.ActionError("No such room")
        except RoomError as e:
            raise a.ActionError(str(e))

        try:
            app = await environment_client.get_app_info(room.game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        return {
            "game_name": room.game_name,
            "game_title": app.title
        }

    async def delete(self, **ignored):
        rooms = self.application.rooms

        room_id = self.context.get("room_id")

        try:
            room = await rooms.get_room(self.gamespace, room_id)
        except RoomNotFound:
            raise a.ActionError("No such room")
        except RoomError as e:
            raise a.ActionError(str(e))

        try:
            await rooms.terminate_room(self.gamespace, room_id, room=room)
        except RoomError as e:
            raise a.ActionError(str(e))

        raise a.Redirect(
            "rooms",
            message="Game has been shot down",
            game_name=room.game_name)

    async def execute_command(self, command, **ignored):
        rooms = self.application.rooms

        room_id = self.context.get("room_id")

        try:
            room = await rooms.get_room(self.gamespace, room_id)
        except RoomNotFound:
            raise a.ActionError("No such room")
        except RoomError as e:
            raise a.ActionError(str(e))

        try:
            await rooms.execute_stdin_command(self.gamespace, room_id, command, room=room)
        except RoomError as e:
            raise a.ActionError(str(e))

        raise a.Redirect(
            "room",
            message="Command has been executed",
            room_id=room_id)

    async def debug(self, **ignored):
        rooms = self.application.rooms

        room_id = self.context.get("room_id")

        try:
            room = await rooms.get_room(self.gamespace, room_id)
        except RoomNotFound:
            raise a.ActionError("No such room")
        except RoomError as e:
            raise a.ActionError(str(e))

        raise a.Redirect(
            "debug_host",
            service="game",
            host_id=room.host_id,
            room_id=room_id)

    def render(self, data):
        game_name = data["game_name"]

        return [
            a.breadcrumbs([
                a.link("app", data["game_title"], record_id=game_name),
                a.link("rooms", "Rooms", game_name=game_name)
            ], self.context.get("room_id")),
            a.split([
                a.form("Execute console command on a room", fields={
                    "command": a.field("Console command", "text", "primary",
                                       description="A console command will be delivered to the running room "
                                                   "(game server) trough standard input")
                }, methods={
                    "execute_command": a.method("Execute command", "primary")
                }, data=data, icon="code"),
                a.form("Other actions", fields={}, methods={
                    "delete": a.method("Delete room", "danger", order=1),
                    "debug": a.method("Debug game server", "primary", order=2)
                }, data=data, icon="bars")
            ]),
            a.links("Navigate", [
                a.link("rooms", "Go back", icon="chevron-left", game_name=game_name)
            ])
        ]


class RoomsController(a.AdminController):
    ROOMS_PER_PAGE = 20

    async def get(self, game_name, page=1,
                  game_version=None,
                  game_server=None,
                  game_deployment=None,
                  game_settings=None,
                  game_region=None,
                  game_host=None):

        environment_client = EnvironmentClient(self.application.cache)
        gameservers = self.application.gameservers
        deployments = self.application.deployments
        hosts = self.application.hosts

        try:
            app = await environment_client.get_app_info(game_name)
        except AppNotFound as e:
            raise a.ActionError("App was not found.")

        game_versions = {
            v_name: v_name
            for v_name, v_id in app.versions.items()
        }

        game_versions[""] = "Any"

        try:
            game_servers = await gameservers.list_game_servers(self.gamespace, game_name)
        except GameError as e:
            raise a.ActionError(str(e))

        game_servers = {
            game_server.game_server_id: game_server.name
            for game_server in game_servers
        }

        game_servers[""] = "Any"

        try:
            game_deployments = await deployments.list_deployments(self.gamespace, game_name)
        except DeploymentError as e:
            raise a.ActionError(str(e))

        game_deployments = {
            dep.deployment_id: dep.game_version + " / @" + dep.deployment_id
            for dep in game_deployments
        }

        game_deployments[""] = "Any"

        try:
            game_regions = await hosts.list_regions()
        except RegionError as e:
            raise a.ActionError(str(e))

        game_regions = {
            region.region_id: region.name
            for region in game_regions
        }

        game_regions[""] = "Any"

        try:
            game_hosts = await hosts.list_hosts(game_region)
        except RegionError as e:
            raise a.ActionError(str(e))

        game_hosts = {
            host.host_id: host.address
            for host in game_hosts
        }

        game_hosts[""] = "Any"

        query = RoomQuery(self.gamespace, game_name)

        query.select_game_servers = True
        query.select_hosts = True
        query.select_regions = True

        query.offset = (int(page) - 1) * RoomsController.ROOMS_PER_PAGE
        query.limit = RoomsController.ROOMS_PER_PAGE

        if game_version:
            query.game_version = game_version

        if game_server:
            query.game_server_id = game_server

        if game_deployment:
            query.deployment_id = game_deployment

        if game_settings:
            try:
                game_settings = ujson.loads(game_settings)
            except (KeyError, ValueError):
                raise a.ActionError("Corrupted settings")

            try:
                cond = format_conditions_json('settings', game_settings)
            except ConditionError as e:
                raise a.ActionError(str(e))

            query.add_conditions(cond)
        else:
            game_settings = {}

        if game_region:
            query.region_id = game_region

        if game_host:
            query.host_id = game_host

        rooms, count = await query.query(self.application.db, one=False, count=True)

        pages = int(math.ceil(float(count) / float(RoomsController.ROOMS_PER_PAGE)))

        result = {
            "game_name": game_name,
            "game_title": app.title,

            "game_versions": game_versions,
            "game_version": game_version,

            "game_servers": game_servers,
            "game_server": game_server,

            "game_deployments": game_deployments,
            "game_deployment": game_deployment,

            "game_settings": game_settings,

            "game_regions": game_regions,
            "game_region": game_region,

            "game_hosts": game_hosts,
            "game_host": game_host,

            "page": page,
            "rooms": rooms,
            "pages_count": pages,
            "total_count": count
        }

        return result

    async def filter(self, **args):

        game_name = self.context.get("game_name")
        page = self.context.get("page", 1)

        filters = {
            "game_name": game_name,
            "page": page
        }

        filters.update(args)

        raise a.Redirect("rooms", **filters)

    async def delete_results(self, **args):

        rooms = self.application.rooms

        game_name = self.context.get("game_name")
        page = self.context.get("page", 1)

        filters = {
            "game_name": game_name,
            "page": page
        }

        filters.update(args)

        filter_results = await self.get(**filters)

        failed_count = 0
        deleted_count = 0

        for room, game_server, region, host in filter_results["rooms"]:

            try:
                await rooms.terminate_room(self.gamespace, room.room_id, room=room, host=host)
            except RoomError:
                failed_count += 1
                logging.exception("Failed to delete room {0}".format(room.room_id))
            else:
                deleted_count += 1

        if failed_count:
            raise a.ActionError("Failed to delete {0} rooms".format(failed_count))

        raise a.Redirect("rooms", message="Successfully deleted {0} rooms".format(deleted_count), **filters)

    async def execute_command_on_results(self, command, **args):

        rooms = self.application.rooms

        game_name = self.context.get("game_name")
        page = self.context.get("page", 1)

        filters = {
            "game_name": game_name,
            "page": page
        }

        filters.update(args)

        filter_results = await self.get(**filters)

        failed_count = 0
        deleted_count = 0

        for room, game_server, region, host in filter_results["rooms"]:

            try:
                await rooms.execute_stdin_command(self.gamespace, room.room_id, command, room=room, host=host)
            except RoomError:
                failed_count += 1
                logging.exception("Failed to execute on a room {0}".format(room.room_id))
            else:
                deleted_count += 1

        if failed_count:
            raise a.ActionError("Failed to execute a command on {0} rooms".format(failed_count))

        raise a.Redirect("rooms",
                         message="Successfully executed command on a {0} rooms".format(deleted_count),
                         **filters)

    def render(self, data):

        game_name = self.context.get("game_name")

        rooms = [
            {
                "edit": [a.link("room", room.room_id, icon="th-large", room_id=room.room_id)],
                "game_name": [a.link("app", room.game_name, icon="mobile", record_id=room.game_name)],
                "game_version": [a.link("app_version", room.game_version, icon="tags",
                                        app_id=room.game_name, version_id=room.game_version)],
                "game_server": [a.link("game_server", game_server.name, icon="rocket",
                                       game_server_id=game_server.game_server_id, game_name=room.game_name)],
                "deployment": [a.link("deployment", room.deployment_id, icon="upload",
                                      deployment_id=room.deployment_id, game_name=room.game_name,
                                      game_version=room.game_version)],
                "players": str(room.players) + " / " + str(room.max_players),
                "region": [a.link("region", region.name, icon="globe", region_id=region.region_id)],
                "host": [a.link("host", host.address, icon="server", host_id=host.host_id)],
                "debug": [a.link("debug_host", "", icon="bug", host_id=host.host_id, room_id=room.room_id)],
                "settings": [a.json_view(room.room_settings)],
            }
            for room, game_server, region, host in data["rooms"]
        ]

        result = [
            a.breadcrumbs([
                a.link("app", data["game_title"], record_id=game_name)
            ], "Rooms"),
            a.content("Rooms: {0} total".format(data["total_count"]), [
                {
                    "id": "edit",
                    "title": "Edit"
                }, {
                    "id": "game_name",
                    "title": "Game"
                }, {
                    "id": "game_version",
                    "title": "Game Version"
                }, {
                    "id": "game_server",
                    "title": "Server"
                }, {
                    "id": "deployment",
                    "title": "Deployment"
                }, {
                    "id": "players",
                    "title": "Players"
                }, {
                    "id": "settings",
                    "title": "Settings"
                }, {
                    "id": "region",
                    "title": "Region"
                }, {
                    "id": "host",
                    "title": "Host"
                }, {
                    "id": "debug",
                    "title": "Debug"
                }], rooms, "default", empty="No rooms to display"),
            a.pages(data["pages_count"]),
            a.form("Filters", fields={
                "game_version": a.field("Game Version", "select", "primary", values=data["game_versions"], order=1),
                "game_server": a.field("Game Server", "select", "primary", values=data["game_servers"], order=2),
                "game_deployment": a.field("Game Deployment",
                                           "select", "primary", values=data["game_deployments"], order=3),
                "game_region": a.field("Game Region",
                                       "select", "primary", values=data["game_regions"], order=4),
                "game_host": a.field("Game Host",
                                     "select", "primary", values=data["game_hosts"], order=5),
                "game_settings": a.field("Game Custom Settings",
                                         "json", "primary", order=6, height=120,
                                         description="""
                                            Please read <a href="https://docs.anthillplatform.org/en/latest/other/dbquery.html" target="_blank">this document</a>
                                            for the query format.
                                         """),
            }, methods={
                "filter": a.method("Filter rooms", "primary")
            }, data=data, icon="filter")
        ]

        if len(data["rooms"]):
            result.append(a.split([
                a.form("Execute console command to a matched rooms", fields={
                    "command": a.field("Console command", "text", "primary",
                                       description="A console command will be delivered to the running rooms "
                                                   "(game servers) trough standard input")
                }, methods={
                    "execute_command_on_results": a.method("Execute command", "primary")
                }, data=data, icon="code"),
                a.form("Other actions", fields={}, methods={
                    "delete_results": a.method("Delete matched rooms", "danger")
                }, data=data, icon="bars")
            ]))

        result.append(a.links("Navigate", [
            a.link("app", "Go back", icon="chevron-left", record_id=game_name),
            a.link("spawn_room", "Spawn a new server", icon="flash", game_name=game_name),
        ]))

        return result

    def access_scopes(self):
        return ["game_admin"]


class HostsController(a.AdminController):
    async def get(self):
        hosts = self.application.hosts

        regions_list = await hosts.list_regions()
        hosts_list = await hosts.list_hosts()

        result = {
            "hosts": hosts_list,
            "regions": {
                region.region_id: region
                for region in regions_list
            }
        }

        return result

    def render(self, data):
        regions = data["regions"]

        return [
            a.breadcrumbs([], "Full Host List"),

            a.content(
                "Hosts",
                [
                    {
                        "id": "region",
                        "title": "Region"
                    },
                    {
                        "id": "address",
                        "title": "Host Address"
                    },
                    {
                        "id": "status",
                        "title": "Status"
                    },
                    {
                        "id": "enabled",
                        "title": "Enabled"
                    },
                    {
                        "id": "cpu",
                        "title": "CPU Load"
                    },
                    {
                        "id": "memory",
                        "title": "Memory Load"
                    },
                    {
                        "id": "storage",
                        "title": "Storage Usage"
                    },
                    {
                        "id": "heartbeat",
                        "title": "Last Check"
                    },
                    {
                        "id": "debug",
                        "title": "Debug Host"
                    }
                ],
                [
                    {
                        "region": [
                            a.link("region",
                                   regions[host.region_id].name if host.region_id in regions else "Unknown",
                                   icon="globe", region_id=host.region_id)
                        ],
                        "address": host.address,
                        "enabled": [
                            a.status(
                                "Yes" if host.enabled else "No",
                                "success" if host.enabled else "danger")],
                        "cpu": "{0} %".format(host.cpu) if host.active else "-",
                        "memory": "{0} %".format(host.memory) if host.active else "-",
                        "storage": "{0} %".format(host.storage) if host.active else "-",
                        "status": [
                            a.status(host.state, "success") if host.active else a.status(host.state, "danger")],
                        "heartbeat": str(host.heartbeat),
                        "debug": [a.link("debug_host", "", icon="bug", host_id=host.host_id)],
                    }
                    for host in data["hosts"]
                ], "primary", empty="No hosts to display"),

            a.links("Navigate", [
                a.link("index", "Go back", icon="chevron-left")
            ])
        ]

    def access_scopes(self):
        return ["game_admin"]
