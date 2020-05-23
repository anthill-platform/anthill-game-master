
from tornado.concurrent import run_on_executor
from tornado.ioloop import IOLoop
from concurrent.futures import ThreadPoolExecutor

from anthill.common import database, clamp
from anthill.common.model import Model
from anthill.common.options import options
from anthill.common.validate import validate

import os
import threading


class DeploymentError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class DeploymentDeliveryError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class DeploymentNotFound(Exception):
    pass


class DeploymentDeliveryNotFound(Exception):
    pass


class NoCurrentDeployment(Exception):
    pass


class DeploymentDeliveryAdapter(object):

    STATUS_DELIVERING = "delivering"
    STATUS_DELIVERED = "delivered"
    STATUS_DELETING = "deleting"
    STATUS_DELETED = "deleted"
    STATUS_ERROR = "error"

    def __init__(self, data):
        self.delivery_id = str(data.get("delivery_id"))
        self.host_id = str(data.get("host_id"))
        self.status = data.get("delivery_status")
        self.error_reason = data.get("error_reason")


class DeploymentAdapter(object):

    STATUS_UPLOADING = "uploading"
    STATUS_UPLOADED = "uploaded"
    STATUS_DELIVERING = "delivering"
    STATUS_DELIVERED = "delivered"
    STATUS_DELETING = "deleting"
    STATUS_DELETED = "deleted"
    STATUS_ERROR = "error"

    def __init__(self, data):
        self.deployment_id = str(data.get("deployment_id"))
        self.game_name = data.get("game_name")
        self.game_version = data.get("game_version")
        self.date = data.get("deployment_date")
        self.status = data.get("deployment_status")
        self.hash = data.get("deployment_hash")


class CurrentDeploymentAdapter(object):
    def __init__(self, data):
        self.deployment_id = str(data.get("current_deployment"))
        self.game_name = data.get("game_name")
        self.game_version = data.get("game_version")
        self.enabled = data.get("deployment_enabled") == 1


class DeploymentModel(Model):
    executor = ThreadPoolExecutor(max_workers=4)

    def __init__(self, db):
        self.db = db
        self.deployments_location = options.deployments_location

        if not os.path.isdir(self.deployments_location):
            os.mkdir(self.deployments_location)

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["deployments", "game_deployments", "deployment_deliveries"]

    @validate(gamespace_id="int", game_name="str", game_version="str")
    async def get_current_deployment(self, gamespace_id, game_name, game_version):
        try:
            current_deployment = await self.db.get(
                """
                SELECT *
                FROM `game_deployments`
                WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s
                LIMIT 1;
                """, gamespace_id, game_name, game_version
            )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to get deployment: " + e.args[1])

        if current_deployment is None:
            raise NoCurrentDeployment()

        return CurrentDeploymentAdapter(current_deployment)

    @validate(gamespace_id="int", game_name="str", current_deployment="int", enabled="bool")
    async def update_game_version_deployment(self, gamespace_id, game_name, game_version, current_deployment, enabled):

        try:
            await self.db.execute(
                """
                INSERT INTO `game_deployments`
                (`gamespace_id`, `game_name`, `game_version`, `current_deployment`, `deployment_enabled`)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE `current_deployment`=%s, `deployment_enabled`=%s;
                """, gamespace_id, game_name, game_version,
                current_deployment, int(enabled), current_deployment, int(enabled)
            )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to switch deployment: " + e.args[1])

    @validate(gamespace_id="int", game_name="str", current_deployment="int", deployment_hash="str")
    async def new_deployment(self, gamespace_id, game_name, game_version, deployment_hash):

        try:
            deployment_id = await self.db.insert(
                """
                INSERT INTO `deployments`
                (`gamespace_id`, `game_name`, `game_version`, `deployment_hash`)
                VALUES (%s, %s, %s, %s);
                """, gamespace_id, game_name, game_version, deployment_hash)
        except database.DatabaseError as e:
            raise DeploymentError("Failed to create a deployment: " + e.args[1])
        else:
            return str(deployment_id)

    @validate(gamespace_id="int", deployment_id="int", status="str_name")
    async def update_deployment_status(self, gamespace_id, deployment_id, status):
        try:
            await self.db.execute(
                """
                UPDATE `deployments`
                SET `deployment_status`=%s
                WHERE `gamespace_id`=%s AND `deployment_id`=%s;
                """, status, gamespace_id, deployment_id
            )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to update deployment: " + e.args[1])

    @validate(gamespace_id="int", deployment_id="int", deployment_hash="str")
    async def update_deployment_hash(self, gamespace_id, deployment_id, deployment_hash):
        try:
            await self.db.execute(
                """
                UPDATE `deployments`
                SET `deployment_hash`=%s
                WHERE `gamespace_id`=%s AND `deployment_id`=%s;
                """, deployment_hash, gamespace_id, deployment_id
            )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to update deployment: " + e.args[1])

    @validate(gamespace_id="int", deployment_id="int")
    async def get_deployment(self, gamespace_id, deployment_id):
        try:
            deployment = await self.db.get(
                """
                SELECT *
                FROM `deployments`
                WHERE `gamespace_id`=%s AND `deployment_id`=%s
                LIMIT 1;
                """, gamespace_id, deployment_id
            )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to get deployment: " + e.args[1])

        if deployment is None:
            raise DeploymentNotFound()

        return DeploymentAdapter(deployment)

    @validate(gamespace_id="int", game_name="str", game_version="str", items_in_page="int", page="int")
    async def list_paged_deployments(self, gamespace_id, game_name, game_version, items_in_page, page=1):
        try:
            async with self.db.acquire() as db:
                pages_count = await db.get(
                    """
                        SELECT COUNT(*) as `count`
                        FROM `deployments`
                        WHERE gamespace_id=%s AND `game_name`=%s AND `game_version`=%s;
                    """, gamespace_id, game_name, game_version)

                import math
                pages = int(math.ceil(float(pages_count["count"]) / float(items_in_page)))

                page = clamp(page, 1, pages)

                limit_a = (page - 1) * items_in_page
                limit_b = page * items_in_page

                deployments = await db.query(
                    """
                    SELECT *
                    FROM `deployments`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s
                    ORDER BY `deployment_id` DESC
                    LIMIT %s, %s;
                    """, gamespace_id, game_name, game_version, limit_a, limit_b
                )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to get deployment: " + e.args[1])

        return list(map(DeploymentAdapter, deployments)), pages

    @validate(gamespace_id="int", game_name="str", game_version="str")
    async def list_deployments(self, gamespace_id, game_name, game_version=None):
        try:
            if game_version:
                deployments = await self.db.query(
                    """
                    SELECT *
                    FROM `deployments`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s
                    ORDER BY `deployment_id` DESC;
                    """, gamespace_id, game_name, game_version
                )
            else:
                deployments = await self.db.query(
                    """
                    SELECT *
                    FROM `deployments`
                    WHERE `gamespace_id`=%s AND `game_name`=%s
                    ORDER BY `deployment_id` DESC;
                    """, gamespace_id, game_name
                )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to get deployment: " + e.args[1])

        return list(map(DeploymentAdapter, deployments))

    @run_on_executor
    def __download_deployment_file__(self, ioloop, filename, write_callback):
        lock = threading.Lock()

        def write_chunk(chunk):
            write_callback(chunk, lock.release)

        with open(filename, 'rb') as f:
            while 1:
                data = f.read(16384)
                if data:
                    lock.acquire()
                    ioloop.add_callback(write_chunk, data)
                else:
                    return

    @validate(deployment=DeploymentAdapter)
    async def download_deployment_file(self, deployment, write_callback):
        """
        Starts the process of downloading deployment file for deployment
        :param deployment: a DeploymentAdapter instance for file in question
        :param write_callback: a write function of signatuere write_callback(chunk, flushed) which should write the
                               chunk data to the socket and call flushed when the chunk has been flushed out
        :return: yields until all contents of the file has been flushed out
        """

        deployment_path = os.path.join(
            self.deployments_location,
            deployment.game_name,
            deployment.game_version,
            str(deployment.deployment_id) + ".zip")

        await self.__download_deployment_file__(IOLoop.current(), deployment_path, write_callback)

    @run_on_executor
    def __remove_deployment_file__(self, filename):
        os.remove(filename)

    @validate(gamespace_id="int", deployment=DeploymentAdapter)
    async def delete_deployment_file(self, gamespace_id, deployment):

        deployment_path = os.path.join(
            self.deployments_location,
            deployment.game_name,
            deployment.game_version,
            str(deployment.deployment_id) + ".zip")

        await self.__remove_deployment_file__(deployment_path)

    @validate(gamespace_id="int", deployment=DeploymentAdapter)
    async def delete_deployment(self, gamespace_id, deployment):
        try:
            await self.db.execute(
                """
                DELETE FROM `deployments`
                WHERE `gamespace_id`=%s AND `deployment_id`=%s
                """, gamespace_id, deployment.deployment_id
            )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to delete a deployment: " + e.args[1])

    @validate(gamespace_id="int", deployment_id="int", host_id="int")
    async def new_deployment_delivery(self, gamespace_id, deployment_id, host_id):
        try:
            deployment_delivery_id = await self.db.insert(
                """
                INSERT INTO `deployment_deliveries`
                (`gamespace_id`, `deployment_id`, `host_id`)
                VALUES (%s, %s, %s);
                """, gamespace_id, deployment_id, host_id)
        except database.DatabaseError as e:
            raise DeploymentError("Failed to create a deployment delivery: " + e.args[1])
        else:
            return str(deployment_delivery_id)

    @validate(gamespace_id="int", delivery_id="int", status="str_name", error_reason="str")
    async def update_deployment_delivery_status(self, gamespace_id, delivery_id, status, error_reason=""):
        try:
            await self.db.execute(
                """
                UPDATE `deployment_deliveries`
                SET `delivery_status`=%s, `error_reason`=%s
                WHERE `gamespace_id`=%s AND `delivery_id`=%s;
                """, status, error_reason, gamespace_id, delivery_id
            )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to update deployment delivery status: " + e.args[1])

    @validate(gamespace_id="int", delivery_ids="json_list_of_ints", status="str_name")
    async def update_deployment_deliveries_status(self, gamespace_id, delivery_ids, status):
        try:
            await self.db.execute(
                """
                UPDATE `deployment_deliveries`
                SET `delivery_status`=%s
                WHERE `gamespace_id`=%s AND `delivery_id` IN %s;
                """, status, gamespace_id, delivery_ids
            )
        except database.DatabaseError as e:
            raise DeploymentError("Failed to update deployment delivery status: " + e.args[1])

    @validate(gamespace_id="int", delivery_id="int")
    async def update_deployment_delivery(self, gamespace_id, delivery_id):
        try:
            delivery = await self.db.get(
                """
                SELECT *
                FROM `deployment_deliveries`
                WHERE `gamespace_id`=%s AND `delivery_id`=%s
                LIMIT 1;
                """, gamespace_id, delivery_id
            )
        except database.DatabaseError as e:
            raise DeploymentDeliveryError("Failed to get deployment: " + e.args[1])

        if delivery is None:
            raise DeploymentDeliveryNotFound()

        return DeploymentDeliveryAdapter(delivery)

    @validate(gamespace_id="int", deployment_id="int")
    async def list_deployment_deliveries(self, gamespace_id, deployment_id):
        try:
            deliveries = await self.db.query(
                """
                SELECT *
                FROM `deployment_deliveries`
                WHERE `gamespace_id`=%s AND `deployment_id`=%s
                ORDER BY `delivery_id` DESC;
                """, gamespace_id, deployment_id
            )
        except database.DatabaseError as e:
            raise DeploymentDeliveryError("Failed to get deployment deliveries: " + e.args[1])

        return list(map(DeploymentDeliveryAdapter, deliveries))

    @validate(gamespace_id="int", delivery_id="int")
    async def delete_deployment_delivery(self, gamespace_id, delivery_id):
        try:
            await self.db.execute(
                """
                DELETE FROM `deployment_deliveries`
                WHERE `gamespace_id`=%s AND `delivery_id`=%s
                """, gamespace_id, delivery_id
            )
        except database.DatabaseError as e:
            raise DeploymentDeliveryError("Failed to delete a deployment delivery: " + e.args[1])
