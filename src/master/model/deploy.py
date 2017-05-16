
from tornado.gen import coroutine, Return
import common.database


from common import clamp
from common.model import Model
from common.options import options
from common.validate import validate
from common.environment import AppNotFound


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
    def __init__(self, db):
        self.db = db
        self.deployments_location = options.deployments_location

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["deployments", "game_deployments", "deployment_deliveries"]

    @coroutine
    @validate(gamespace_id="int", game_name="str", game_version="str")
    def get_current_deployment(self, gamespace_id, game_name, game_version):
        try:
            current_deployment = yield self.db.get(
                """
                SELECT *
                FROM `game_deployments`
                WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s
                LIMIT 1;
                """, gamespace_id, game_name, game_version
            )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to get deployment: " + e.args[1])

        if current_deployment is None:
            raise NoCurrentDeployment()

        raise Return(CurrentDeploymentAdapter(current_deployment))

    @coroutine
    @validate(gamespace_id="int", game_name="str", current_deployment="int", enabled="bool")
    def update_game_version_deployment(self, gamespace_id, game_name, game_version, current_deployment, enabled):

        try:
            yield self.db.execute(
                """
                INSERT INTO `game_deployments`
                (`gamespace_id`, `game_name`, `game_version`, `current_deployment`, `deployment_enabled`)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE `current_deployment`=%s, `deployment_enabled`=%s;
                """, gamespace_id, game_name, game_version,
                current_deployment, int(enabled), current_deployment, int(enabled)
            )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to switch deployment: " + e.args[1])

    @coroutine
    @validate(gamespace_id="int", game_name="str", current_deployment="int", deployment_hash="str")
    def new_deployment(self, gamespace_id, game_name, game_version, deployment_hash):

        try:
            deployment_id = yield self.db.insert(
                """
                INSERT INTO `deployments`
                (`gamespace_id`, `game_name`, `game_version`, `deployment_hash`)
                VALUES (%s, %s, %s, %s);
                """, gamespace_id, game_name, game_version, deployment_hash)
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to create a deployment: " + e.args[1])
        else:
            raise Return(str(deployment_id))

    @coroutine
    @validate(gamespace_id="int", deployment_id="int", status="str_name")
    def update_deployment_status(self, gamespace_id, deployment_id, status):
        try:
            yield self.db.execute(
                """
                UPDATE `deployments`
                SET `deployment_status`=%s
                WHERE `gamespace_id`=%s AND `deployment_id`=%s;
                """, status, gamespace_id, deployment_id
            )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to update deployment: " + e.args[1])

    @coroutine
    @validate(gamespace_id="int", deployment_id="int", deployment_hash="str")
    def update_deployment_hash(self, gamespace_id, deployment_id, deployment_hash):
        try:
            yield self.db.execute(
                """
                UPDATE `deployments`
                SET `deployment_hash`=%s
                WHERE `gamespace_id`=%s AND `deployment_id`=%s;
                """, deployment_hash, gamespace_id, deployment_id
            )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to update deployment: " + e.args[1])

    @coroutine
    @validate(gamespace_id="int", deployment_id="int")
    def get_deployment(self, gamespace_id, deployment_id):
        try:
            deployment = yield self.db.get(
                """
                SELECT *
                FROM `deployments`
                WHERE `gamespace_id`=%s AND `deployment_id`=%s
                LIMIT 1;
                """, gamespace_id, deployment_id
            )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to get deployment: " + e.args[1])

        if deployment is None:
            raise DeploymentNotFound()

        raise Return(DeploymentAdapter(deployment))

    @coroutine
    @validate(gamespace_id="int", game_name="str", game_version="str", items_in_page="int", page="int")
    def list_paged_deployments(self, gamespace_id, game_name, game_version, items_in_page, page=1):
        try:
            with (yield self.db.acquire()) as db:
                pages_count = yield db.get(
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

                deployments = yield db.query(
                    """
                    SELECT *
                    FROM `deployments`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s
                    ORDER BY `deployment_id` DESC
                    LIMIT %s, %s;
                    """, gamespace_id, game_name, game_version, limit_a, limit_b
                )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to get deployment: " + e.args[1])

        raise Return((map(DeploymentAdapter, deployments), pages))

    @coroutine
    @validate(gamespace_id="int", game_name="str", game_version="str_or_none")
    def list_deployments(self, gamespace_id, game_name, game_version=None):
        try:
            if game_version:
                deployments = yield self.db.query(
                    """
                    SELECT *
                    FROM `deployments`
                    WHERE `gamespace_id`=%s AND `game_name`=%s AND `game_version`=%s
                    ORDER BY `deployment_id` DESC;
                    """, gamespace_id, game_name, game_version
                )
            else:
                deployments = yield self.db.query(
                    """
                    SELECT *
                    FROM `deployments`
                    WHERE `gamespace_id`=%s AND `game_name`=%s
                    ORDER BY `deployment_id` DESC;
                    """, gamespace_id, game_name
                )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to get deployment: " + e.args[1])

        raise Return(map(DeploymentAdapter, deployments))

    @coroutine
    @validate(gamespace_id="int", deployment_id="int")
    def delete_deployment(self, gamespace_id, deployment_id):

        try:
            yield self.db.execute(
                """
                DELETE FROM `deployments`
                WHERE `gamespace_id`=%s AND `deployment_id`=%s
                """, gamespace_id, deployment_id
            )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to delete a deployment: " + e.args[1])

    @coroutine
    @validate(gamespace_id="int", deployment_id="int", host_id="int")
    def new_deployment_delivery(self, gamespace_id, deployment_id, host_id):
        try:
            deployment_delivery_id = yield self.db.insert(
                """
                INSERT INTO `deployment_deliveries`
                (`gamespace_id`, `deployment_id`, `host_id`)
                VALUES (%s, %s, %s);
                """, gamespace_id, deployment_id, host_id)
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to create a deployment delivery: " + e.args[1])
        else:
            raise Return(str(deployment_delivery_id))

    @coroutine
    @validate(gamespace_id="int", delivery_id="int", status="str_name", error_reason="str")
    def update_deployment_delivery_status(self, gamespace_id, delivery_id, status, error_reason=""):
        try:
            yield self.db.execute(
                """
                UPDATE `deployment_deliveries`
                SET `delivery_status`=%s, `error_reason`=%s
                WHERE `gamespace_id`=%s AND `delivery_id`=%s;
                """, status, error_reason, gamespace_id, delivery_id
            )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to update deployment delivery status: " + e.args[1])

    @coroutine
    @validate(gamespace_id="int", delivery_ids="json_list_of_ints", status="str_name")
    def update_deployment_deliveries_status(self, gamespace_id, delivery_ids, status):
        try:
            yield self.db.execute(
                """
                UPDATE `deployment_deliveries`
                SET `delivery_status`=%s
                WHERE `gamespace_id`=%s AND `delivery_id` IN %s;
                """, status, gamespace_id, delivery_ids
            )
        except common.database.DatabaseError as e:
            raise DeploymentError("Failed to update deployment delivery status: " + e.args[1])

    @coroutine
    @validate(gamespace_id="int", delivery_id="int")
    def update_deployment_delivery(self, gamespace_id, delivery_id):
        try:
            delivery = yield self.db.get(
                """
                SELECT *
                FROM `deployment_deliveries`
                WHERE `gamespace_id`=%s AND `delivery_id`=%s
                LIMIT 1;
                """, gamespace_id, delivery_id
            )
        except common.database.DatabaseError as e:
            raise DeploymentDeliveryError("Failed to get deployment: " + e.args[1])

        if delivery is None:
            raise DeploymentDeliveryNotFound()

        raise Return(DeploymentDeliveryAdapter(delivery))

    @coroutine
    @validate(gamespace_id="int", deployment_id="int")
    def list_deployment_deliveries(self, gamespace_id, deployment_id):
        try:
            deliveries = yield self.db.query(
                """
                SELECT *
                FROM `deployment_deliveries`
                WHERE `gamespace_id`=%s AND `deployment_id`=%s
                ORDER BY `delivery_id` DESC;
                """, gamespace_id, deployment_id
            )
        except common.database.DatabaseError as e:
            raise DeploymentDeliveryError("Failed to get deployment deliveries: " + e.args[1])

        raise Return(map(DeploymentDeliveryAdapter, deliveries))

    @coroutine
    @validate(gamespace_id="int", delivery_id="int")
    def delete_deployment_delivery(self, gamespace_id, delivery_id):
        try:
            yield self.db.execute(
                """
                DELETE FROM `deployment_deliveries`
                WHERE `gamespace_id`=%s AND `delivery_id`=%s
                """, gamespace_id, delivery_id
            )
        except common.database.DatabaseError as e:
            raise DeploymentDeliveryError("Failed to delete a deployment delivery: " + e.args[1])
