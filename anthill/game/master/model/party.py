from tornado.gen import IOLoop, multi

from anthill.common.validate import validate
from anthill.common.model import Model
from anthill.common.database import DatabaseError, format_conditions_json, ConditionError
from anthill.common.rabbitconn import RabbitMQConnection
from anthill.common.options import options
from anthill.common.ratelimit import RateLimitExceeded
from anthill.common.access import AccessToken
from anthill.common.profile import PredefinedProfile, ProfileError
from anthill.common.internal import Internal, InternalError
from anthill.common import Enum, Flags

from .gameserver import GameServerNotFound, GameVersionNotFound
from .deploy import NoCurrentDeployment
from .host import HostNotFound
from .room import RoomError, RoomNotFound

import ujson
import logging


class PartyError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return "{0}: {1}".format(self.code, self.message)


class PartyStatus(Enum):
    CREATED = 'created'
    STARTING = 'starting'

    ALL = {CREATED, STARTING}


class PartyFlags(Flags):
    AUTO_START = 'auto_start'
    AUTO_CLOSE = 'auto_close'


class NoSuchParty(Exception):
    pass


class PartyAdapter(object):
    def __init__(self, data):
        self.id = data.get("party_id")
        self.party_num_members = data.get("party_num_members", 1)
        self.party_max_members = data.get("party_max_members", 0)
        self.game_name = data.get("game_name")
        self.game_version = data.get("game_version")
        self.game_server_id = data.get("game_server_id")
        self.region_id = data.get("region_id")
        self.settings = data.get("party_settings")
        self.room_settings = data.get("room_settings")
        self.room_filters = data.get("room_filters")
        self.close_callback = data.get("party_close_callback")
        self.flags = PartyFlags(data.get("party_flags", "").lower().split(","))
        self.status = PartyStatus(data.get("party_status", PartyStatus.CREATED))

    def dump(self):
        return {
            "id": self.id,
            "num_members": self.party_num_members,
            "max_members": self.party_max_members,
            "settings": self.settings
        }


class PartyMemberAdapter(object):
    def __init__(self, data):
        self.account = data.get("account_id")
        self.role = data.get("member_role", 0)
        self.profile = data.get("member_profile")
        self.token = data.get("member_token")

    def dump(self):
        return {
            "account": self.account,
            "role": self.role,
            "profile": self.profile
        }


class PartySession(object):
    MESSAGE_TYPE_PLAYER_JOINED = 'player_joined'
    MESSAGE_TYPE_PLAYER_LEFT = 'player_left'
    MESSAGE_TYPE_GAME_STARTING = 'game_starting'
    MESSAGE_TYPE_GAME_START_FAILED = 'game_start_failed'
    MESSAGE_TYPE_GAME_STARTED = 'game_started'
    MESSAGE_TYPE_CUSTOM = 'custom'
    MESSAGE_TYPE_PARTY_CLOSED = 'party_closed'

    FIELD_MESSAGE_TYPE = 'mt'
    FIELD_PAYLOAD = 'p'

    def __init__(self, gamespace_id, account_id, broker, party, db, parties, role, token):

        self.gamespace_id = gamespace_id
        self.account_id = str(account_id)
        self.member_profile = None
        self.role = role
        self.broker = broker
        self.channel = None
        self.party = party
        self.exchange = None
        self.queue = None
        self.consumer = None
        self.db = db
        self.parties = parties
        self.done = False
        self.released = False
        self.is_joined = False
        self.send_new_player = False
        self.need_auto_start = False
        self.members = []
        self.token = token

        self.message_callback = None
        self.close_callback = None

        self.handlers_before = {
            PartySession.MESSAGE_TYPE_GAME_STARTING: self.__has_to_be_joined__,
            PartySession.MESSAGE_TYPE_GAME_START_FAILED: self.__has_to_be_joined__,
        }
        self.handlers_after = {
            PartySession.MESSAGE_TYPE_GAME_STARTED: self.__game_started__,
            PartySession.MESSAGE_TYPE_PARTY_CLOSED: self.__party_closed__
        }

    def __exchange__name__(self):
        return "party." + str(self.gamespace_id) + "." + str(self.party.id)

    def __routing_key__(self, account_id):
        return "user." + str(self.gamespace_id) + "." + str(account_id)

    def set_on_message(self, callback):
        self.message_callback = callback

    def set_on_close(self, callback):
        self.close_callback = callback

    def joined(self, member_profile, send_new_player=False):
        self.member_profile = member_profile
        self.send_new_player = send_new_player
        self.is_joined = True

    def __check_auto_start__(self):
        if (PartyFlags.AUTO_START in self.party.flags) and \
                (self.party.party_max_members == self.party.party_num_members):
            self.need_auto_start = True

    def dump(self):
        return {
            "party": self.party.dump(),
            "members": [
                member.dump()
                for member in self.members
            ]
        }

    @validate(room_settings="json_dict")
    async def __join_server__(self, members, party):

        members = [
            (AccessToken(member.token), {
                "party_id": str(party.id),
                "party_profile": member.profile,
                "party_role": member.role
            })
            for member in members
        ]

        records, room = await self.parties.rooms.find_and_join_room_multi(
            self.gamespace_id, self.party.game_name, self.party.game_version,
            self.party.game_server_id, members, self.party.room_filters, region=self.party.region_id)

        self.room_id = room.room_id

        logging.info("Found a room: '{0}'".format(self.room_id))

        location = room.location
        settings = room.room_settings

        await multi([self.send_message(
            PartySession.MESSAGE_TYPE_GAME_STARTED, {
                "id": str(self.room_id),
                "slot": str(record_id),
                "key": str(key),
                "location": location,
                "settings": settings
            }, account_id=account)
            for account, (record_id, key) in records.items()
            if str(account) != self.account_id])

        my_record = records.get(str(self.account_id))

        if not my_record:
            raise PartyError(500, "No player's record found")

        my_record_id, my_key = my_record

        await self.__process_message__(PartySession.MESSAGE_TYPE_GAME_STARTED, {
            "id": str(self.room_id),
            "slot": str(my_record_id),
            "key": str(my_key),
            "location": location,
            "settings": settings
        })

    @validate(room_settings="json_dict")
    async def __spawn_server__(self, members, party):

        room_settings = {
            key: value
            for key, value in self.party.room_settings.items()
            if isinstance(value, (str, int, float, bool))
        }

        try:
            deployment = await self.parties.deployments.get_current_deployment(
                self.gamespace_id, self.party.game_name, self.party.game_version)
        except NoCurrentDeployment:
            raise PartyError(404, "No deployment defined for {0}/{1}".format(
                self.party.game_name, self.party.game_version))

        if not deployment.enabled:
            raise PartyError(410, "Deployment is disabled for {0}/{1}".format(
                self.party.game_name, self.party.game_version))

        deployment_id = deployment.deployment_id

        try:
            limit = await self.parties.ratelimit.limit("create_room", self.account_id)
        except RateLimitExceeded:
            raise PartyError(429, "Too many requests")

        try:
            host = await self.parties.hosts.get_best_host(self.party.region_id)
        except HostNotFound:
            raise PartyError(503, "Not enough hosts")

        try:
            gs = await self.parties.gameservers.get_game_server(
                self.gamespace_id, self.party.game_name, self.party.game_server_id)
        except GameServerNotFound:
            raise PartyError(404, "No such gameserver")

        try:
            server_settings = await self.parties.gameservers.get_version_game_server(
                self.gamespace_id, self.party.game_name, self.party.game_version, gs.game_server_id)
        except GameVersionNotFound:
            logging.info("Applied default config for version '{0}'".format(self.party.game_version))
            server_settings = gs.server_settings

            if server_settings is None:
                raise PartyError(500, "No default version configuration")

        create_members = [
            (AccessToken(member.token), {
                "party_id": str(party.id),
                "party_profile": member.profile,
                "party_role": member.role
            })
            for member in members
        ]

        records, self.room_id = await self.parties.rooms.create_and_join_room_multi(
            self.gamespace_id, self.party.game_name, self.party.game_version,
            gs, room_settings, create_members, host, deployment_id, trigger_remove=False)

        logging.info("Created a room: '{0}'".format(self.room_id))

        party_members = {
            member.account: {
                "profile": member.profile,
                "role": member.role
            }
            for member in members
        }

        other_settings = {
            "party_id": str(party.id),
            "party_settings": ujson.dumps(party.settings),
            "party_members": ujson.dumps(party_members)
        }

        try:
            result = await self.parties.rooms.spawn_server(
                self.gamespace_id, self.party.game_name, self.party.game_version, gs.name,
                deployment_id, self.room_id, host, gs.game_settings, server_settings,
                room_settings, other_settings=other_settings)
        except RoomError as e:
            await self.parties.rooms.remove_room(self.gamespace_id, self.room_id)
            logging.exception("Failed to spawn a server")
            await limit.rollback()
            raise PartyError(500, e.message)

        updated_room_settings = result.get("settings")

        if updated_room_settings:
            room_settings.update(updated_room_settings)
            await self.parties.rooms.update_room_settings(self.gamespace_id, self.room_id, room_settings)

        self.parties.rooms.trigger_remove_temp_reservation_multi(
            self.gamespace_id, self.room_id, [member.account for member in members])

        await multi([self.send_message(
            PartySession.MESSAGE_TYPE_GAME_STARTED, {
                "id": str(self.room_id),
                "slot": str(record_id),
                "key": str(key),
                "location": result.get("location"),
                "settings": room_settings
            }, account_id=account)
            for account, (record_id, key) in records.items()
            if str(account) != self.account_id])

        my_record = records.get(str(self.account_id))

        if not my_record:
            raise PartyError(500, "No player's record found")

        my_record_id, my_key = my_record

        await self.__process_message__(PartySession.MESSAGE_TYPE_GAME_STARTED, {
            "id": str(self.room_id),
            "slot": str(my_record_id),
            "key": str(my_key),
            "location": result.get("location"),
            "settings": room_settings
        })

    # noinspection PyUnusedLocal
    async def __party_closed__(self, message_type, payload):
        await self.close(3410, "Party closed")

    # noinspection PyUnusedLocal
    async def __game_started__(self, message_type, payload):
        await self.close(3411, "Game started")

    # noinspection PyUnusedLocal
    async def __has_to_be_joined__(self, message_type, payload):
        return self.is_joined

    @validate(message_payload="json_dict")
    async def close_party(self, message_payload):

        if self.released:
            raise PartyError(410, "Party is released")

        if self.role < PartyModel.PARTY_PERMISSION_CLOSE:
            raise PartyError(403, "Not enough permissions to close party")

        await self.parties.close_party(self.gamespace_id, self.party.id, message_payload)

    @validate()
    async def leave_party(self):
        if self.released:
            raise PartyError(410, "Party is released")

        if not self.is_joined:
            raise PartyError(410, "Not joined")

        await self.parties.__remove_party_member__(
            self.gamespace_id, self.party.id, self.account_id, add_member=True)

        await self.send_message(PartySession.MESSAGE_TYPE_PLAYER_LEFT, {
            "account": self.account_id,
            "profile": self.member_profile
        })

        self.is_joined = False
        self.member_profile = None

    @validate(member_profile="json_dict", check_members="json_dict")
    async def join_party(self, member_profile, check_members=None):

        if self.released:
            raise PartyError(410, "Party is released")

        if self.is_joined:
            raise PartyError(409, "Already joined")

        async with self.db.acquire(auto_commit=False) as db:
            try:
                party_data = await db.get(
                    """
                    SELECT *
                    FROM `parties`
                    WHERE `gamespace_id`=%s AND `party_id`=%s
                    LIMIT 1
                    FOR UPDATE;
                    """, self.gamespace_id, self.party.id)

                if party_data is None:
                    raise NoSuchParty()

                party = PartyAdapter(party_data)

                if party.status != PartyStatus.CREATED:
                    raise PartyError(409, "Party have already started a game")

                if party.party_num_members >= party.party_max_members:
                    raise PartyError(406, "Party is full")

                members = await self.parties.list_party_members(self.gamespace_id, self.party.id, db=db)

                if check_members:
                    merged_profiles = PredefinedProfile({
                        "members": [member.dump() for member in members]
                    })

                    try:
                        await merged_profiles.set_data(check_members, None, merge=True)
                    except ProfileError as e:
                        raise PartyError(409, "Member check failed: " + str(e.message))

                party.party_num_members += 1

                self.party.party_num_members = party.party_num_members
                self.party.party_max_members = party.party_max_members

                try:
                    await db.insert(
                        """
                        INSERT INTO `party_members`
                        (`account_id`, `gamespace_id`, `party_id`, `member_role`, 
                         `member_profile`, `member_token`) 
                        VALUES (%s, %s, %s, %s, %s, %s); 
                        """, self.account_id, self.gamespace_id, self.party.id,
                        PartyModel.PARTY_ROLE_USER, ujson.dumps(member_profile), self.token)
                except DatabaseError as e:
                    # well, we've tried
                    await db.rollback()
                    raise PartyError(500, "Failed to join a party: " + e.args[1])

                self.joined(member_profile, send_new_player=True)

                try:
                    await db.execute(
                        """
                        UPDATE `parties`
                        SET `party_num_members`=%s
                        WHERE `gamespace_id`=%s AND `party_id`=%s
                        LIMIT 1;
                        """, party.party_num_members, self.gamespace_id, self.party.id)
                except DatabaseError as e:
                    raise PartyError(500, "Failed to register a player into a party: " + e.args[1])

                self.is_joined = True

            finally:
                await db.commit()

            await self.send_message(PartySession.MESSAGE_TYPE_PLAYER_JOINED, {
                "account": self.account_id,
                "profile": self.member_profile})

            self.__check_auto_start__()
            IOLoop.current().add_callback(self.start_game_if_needed)

    @validate(message_payload="json_dict")
    async def start_game(self, message_payload):

        await self.__start_game__(message_payload)

    @validate(message_payload="json_dict")
    async def __start_game__(self, message_payload, check_permissions=True):

        if self.released:
            raise PartyError(410, "Party is released")

        if check_permissions:
            if self.role < PartyModel.PARTY_PERMISSION_START:
                raise PartyError(403, "Not enough permissions to start a game")

        async with self.db.acquire(auto_commit=False) as db:
            try:
                party_data = await db.get(
                    """
                    SELECT *
                    FROM `parties`
                    WHERE `gamespace_id`=%s AND `party_id`=%s
                    LIMIT 1
                    FOR UPDATE;
                    """, self.gamespace_id, self.party.id)

                if party_data is None:
                    raise NoSuchParty()

                party = PartyAdapter(party_data)

                if party.status != PartyStatus.CREATED:
                    raise PartyError(409, "Party have already started a game")

                await db.execute(
                    """
                    UPDATE `parties`
                    SET `party_status`=%s
                    WHERE `gamespace_id`=%s AND `party_id`=%s
                    LIMIT 1;
                    """, PartyStatus.STARTING, self.gamespace_id, self.party.id)

                members = await self.parties.list_party_members(self.gamespace_id, self.party.id, db=db)
                await db.commit()
                await self.send_message(PartySession.MESSAGE_TYPE_GAME_STARTING, message_payload)

                # hold a reference for a moment as self.parties will become None as soon as 'game_started' is received
                parties = self.parties

                try:
                    if party.room_filters is None:
                        logging.info("Party spawning new server: {0}".format(party.id))
                        await self.__spawn_server__(members, party)
                    else:
                        # if filters is defined, that means we need to find a matching server first
                        try:
                            logging.info("Party joining to a server: {0}".format(party.id))
                            await self.__join_server__(members, party)
                        except RoomNotFound:
                            logging.info("No rooms found, spawning new server: {0}".format(party.id))
                            # and if there's no matching rooms, create one
                            await self.__spawn_server__(members, party)
                except PartyError as e:

                    # rollback
                    await db.execute(
                        """
                        UPDATE `parties`
                        SET `party_status`=%s
                        WHERE `gamespace_id`=%s AND `party_id`=%s
                        LIMIT 1;
                        """, PartyStatus.CREATED, self.gamespace_id, self.party.id)
                    await db.commit()

                    await self.send_message(PartySession.MESSAGE_TYPE_GAME_START_FAILED, {
                        "code": e.code,
                        "reason": e.message
                    })

                    raise e

                try:
                    await parties.delete_party(self.gamespace_id, self.party.id, db=db)
                except PartyError:
                    logging.exception("Failed to delete a party")
                else:

                    if party.close_callback:
                        try:
                            result = await parties.__party_close_callback__(
                                self.gamespace_id, party.close_callback,
                                party=party.dump(), message=message_payload, reason="game_started")
                        except PartyError:
                            logging.exception("Failed to call close_callback: {0}".format(party.close_callback))
                        else:
                            return result

                self.done = True

                await self.close(1000, "Party is closed due to started game")

                return "OK"

            except DatabaseError as e:
                raise PartyError(500, e.args[1])
            finally:
                await db.commit()

    async def init(self, members=None):
        self.channel = await self.broker.channel()
        self.exchange = await self.channel.exchange(
            exchange=self.__exchange__name__(),
            exchange_type='topic',
            auto_delete=True)

        self.queue = await self.channel.queue(exclusive=True, arguments={"x-message-ttl": 1000})

        await self.queue.bind(exchange=self.exchange, routing_key=self.__routing_key__(self.account_id))
        await self.queue.bind(exchange=self.exchange, routing_key="all." + str(self.gamespace_id))

        if self.send_new_player:
            await self.send_message(PartySession.MESSAGE_TYPE_PLAYER_JOINED, {
                "account": self.account_id,
                "profile": self.member_profile
            })

        self.consumer = await self.queue.consume(self.__on_message__, no_ack=True)

        if members:
            self.members = members
        else:
            self.members = await self.parties.list_party_members(self.gamespace_id, self.party.id)

        if self.is_joined:
            self.__check_auto_start__()
        else:
            for member in members:
                if str(member.account) == str(self.account_id):
                    self.role = member.role
                    self.joined(member.profile)
                    break

    async def start_game_if_needed(self):
        if self.need_auto_start:
            await self.__start_game__({
                "auto_start": True
            }, check_permissions=False)

    @staticmethod
    async def drop_message(gamespace_id, party_id, broker, message_type, payload, routing_key):

        exchange_name = "party." + str(gamespace_id) + "." + str(party_id)

        channel = await broker.channel()

        body = ujson.dumps({
            PartySession.FIELD_MESSAGE_TYPE: message_type,
            PartySession.FIELD_PAYLOAD: payload
        })

        await channel.basic_publish(
            exchange=exchange_name,
            routing_key=routing_key,
            body=body,
            immediate=True)

        # noinspection PyBroadException
        try:
            channel.close()
        except Exception:
            logging.exception("Failed to close the channel")

    async def send_message(self, message_type, payload, account_id=None):

        if self.released:
            raise PartyError(410, "Party is released")

        body = ujson.dumps({
            PartySession.FIELD_MESSAGE_TYPE: message_type,
            PartySession.FIELD_PAYLOAD: payload
        })

        self.channel.basic_publish(
            exchange=self.__exchange__name__(),
            routing_key=self.__routing_key__(account_id) if account_id else "all." + str(self.gamespace_id),
            body=body,
            immediate=True)

    async def close(self, code, reason):
        await self.release(remove_member=False)

        if self.close_callback:
            await self.close_callback(code, reason)

    # noinspection PyBroadException
    async def release(self, remove_member=True, add_member_slot=True):

        if self.released:
            return

        if (not self.done) and remove_member and self.is_joined:
            await self.send_message(PartySession.MESSAGE_TYPE_PLAYER_LEFT, {
                "account": self.account_id,
                "profile": self.member_profile
            })

        if self.queue:
            try:
                await self.queue.delete()
            except Exception:
                logging.exception("Failed to delete the queue")

        if self.channel:
            try:
                self.channel.close()
            except Exception:
                logging.exception("Failed to close the channel")

        if (not self.done) and remove_member and self.is_joined:
            await self.parties.__remove_party_member__(
                self.gamespace_id, self.party.id, self.account_id, add_member=add_member_slot)

        # hello gc
        self.parties = None
        self.released = True

    async def __process_message__(self, message_type, payload):

        handler_before = self.handlers_before.get(message_type, None)

        if handler_before:
            cont = await handler_before(message_type, payload)
            if not cont:
                return

        if self.message_callback:
            await self.message_callback(message_type, payload)

        handler_after = self.handlers_after.get(message_type, None)

        if handler_after:
            await handler_after(message_type, payload)

    # noinspection PyUnusedLocal
    def __on_message__(self, channel, method, properties, body):

        if self.released:
            return

        try:
            msg = ujson.loads(body)
        except (KeyError, ValueError):
            return

        message_type = msg.get(PartySession.FIELD_MESSAGE_TYPE, "unknown")
        payload = msg.get(PartySession.FIELD_PAYLOAD, {})

        IOLoop.current().spawn_callback(self.__process_message__, message_type, payload)


class PartyQuery(object):
    def __init__(self, gamespace_id, game_name, game_version, game_server_id):
        self.gamespace_id = gamespace_id
        self.game_name = game_name
        self.game_version = game_version
        self.game_server_id = game_server_id

        self.region_id = None
        self.free_slots = 1
        self.limit = 0
        self.offset = 0
        self.other_conditions = []
        self.for_update = False

    def add_conditions(self, conditions):

        if not isinstance(conditions, list):
            raise RuntimeError("conditions expected to be a list")

        self.other_conditions.extend(conditions)

    def __values__(self):
        conditions = [
            "`parties`.`gamespace_id`=%s",
            "`parties`.`game_name`=%s",
            "`parties`.`game_version`=%s",
            "`parties`.`game_server_id`=%s"
        ]

        data = [
            str(self.gamespace_id),
            self.game_name,
            self.game_version,
            self.game_server_id
        ]

        if self.free_slots:
            conditions.append("`parties`.`party_num_members` + %s <= `rooms`.`party_max_members`")
            data.append(self.free_slots)

        if self.region_id:
            conditions.append("`parties`.`region_id`=%s")
            data.append(str(self.region_id))

        for condition, values in self.other_conditions:
            conditions.append(condition)
            data.extend(values)

        return conditions, data

    async def query(self, db, one=False, count=False):
        conditions, data = self.__values__()

        query = """
            SELECT {0} * FROM `parties`
        """.format(
            "SQL_CALC_FOUND_ROWS" if count else "")

        query += """
            WHERE {0}
        """.format(" AND ".join(conditions))

        if self.limit:
            query += """
                LIMIT %s,%s
            """
            data.append(int(self.offset))
            data.append(int(self.limit))

        if self.for_update:
            query += """
                FOR UPDATE
            """

        query += ";"

        if one:
            result = await db.get(query, *data)

            if not result:
                return None

            return PartyAdapter(result)
        else:
            result = await db.query(query, *data)

            count_result = 0

            if count:
                count_result = await db.get(
                    """
                        SELECT FOUND_ROWS() AS count;
                    """)
                count_result = count_result["count"]

            items = map(PartyAdapter, result)

            if count:
                return (items, count_result)

            return items


class PartyModel(Model):
    PARTY_ROLE_ADMIN = 1000
    PARTY_ROLE_USER = 0

    PARTY_PERMISSION_START = 500
    PARTY_PERMISSION_CLOSE = 1000

    def __init__(self, db, gameservers, deployments, ratelimit, hosts, rooms):
        self.db = db
        self.gameservers = gameservers
        self.deployments = deployments
        self.ratelimit = ratelimit
        self.hosts = hosts
        self.rooms = rooms
        self.internal = Internal()

        party_broker = options.party_broker

        self.party_broker = RabbitMQConnection(party_broker, "game.party-queues")

    async def started(self, application):
        await super(PartyModel, self).started(application)
        await self.party_broker.wait_connect()

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["parties", "party_members"]

    @validate(gamespace_id="int", game_name="str_name", game_version="str_name", game_server_name="str_name",
              region_id="int", party_settings="json_dict", room_settings="json_dict", room_filters="json_dict",
              max_members="int", account_id="int", member_role="json_dict", member_token="str", auto_join="bool",
              party_flags=PartyFlags, close_callback="str_name")
    async def create_party(self, gamespace_id, game_name, game_version, game_server_name, region_id, party_settings,
                           room_settings, max_members, account_id, member_profile, member_token, party_flags,
                           room_filters=None, auto_join=True, close_callback=None, session_callback=None):

        if max_members < 2:
            raise PartyError(400, "'max_members' cannot be lass than 2")

        try:
            gs = await self.gameservers.find_game_server(gamespace_id, game_name, game_server_name)
        except GameServerNotFound:
            raise PartyError(400, "No such game server")

        members_count = 1 if auto_join else 0

        async with self.db.acquire(auto_commit=True) as db:
            try:
                party_id = await db.insert(
                    """
                    INSERT INTO `parties` 
                    (`gamespace_id`, `party_num_members`, `party_max_members`, `game_name`, `game_version`, 
                     `game_server_id`, `region_id`, `party_settings`, `room_settings`, `room_filters`, `party_status`, 
                     `party_flags`, `party_close_callback`) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """, gamespace_id, members_count, max_members, game_name, game_version,
                    gs.game_server_id, region_id, ujson.dumps(party_settings), ujson.dumps(room_settings),
                    ujson.dumps(room_filters) if room_filters is not None else None, PartyStatus.CREATED,
                    party_flags.dump(),
                    close_callback)
            except DatabaseError as e:
                raise PartyError(500, "Failed to create new party: " + e.args[1])

            role = PartyModel.PARTY_ROLE_ADMIN

            party = PartyAdapter({
                "party_id": party_id,
                "party_num_members": members_count,
                "party_max_members": max_members,
                "party_flags": party_flags.dump(),
                "party_close_callback": close_callback,
                "game_name": game_name,
                "game_version": game_version,
                "game_server_id": gs.game_server_id,
                "party_settings": party_settings,
                "room_settings": room_settings,
                "room_filters": room_filters,
                "region_id": region_id
            })

            session = PartySession(
                gamespace_id, account_id,
                self.party_broker, party,
                self.db, self, role, member_token)

            members = []

            if auto_join:
                try:
                    await db.insert(
                        """
                        INSERT INTO `party_members`
                        (`account_id`, `gamespace_id`, `party_id`, `member_role`, `member_profile`, `member_token`) 
                        VALUES (%s, %s, %s, %s, %s, %s);
                        """, account_id, gamespace_id, party_id, role, ujson.dumps(member_profile), member_token)
                except DatabaseError as e:
                    # well, we've tried

                    # noinspection PyBroadException
                    try:
                        await db.execute(
                            """
                            DELETE FROM `parties`
                            WHERE `gamespace_id`=%s AND `party_id`=%s
                            LIMIT 1;
                            """, gamespace_id, party_id
                        )
                    except:
                        pass

                    raise PartyError(500, "Failed to create new party: " + e.args[1])

                session.joined(member_profile, send_new_player=True)

                members.append(PartyMemberAdapter({
                    "account_id": account_id,
                    "member_role": role,
                    "member_profile": member_profile
                }))

            await session.init(members=members)

            if session_callback:
                await session_callback(session)

            await session.start_game_if_needed()

            return session

    @validate(gamespace_id="int", game_name="str_name", game_version="str_name", game_server_name="str_name",
              region_id="int", party_settings="json_dict", room_settings="json_dict", max_members="int",
              party_flags=PartyFlags, auto_close="bool", close_callback="str_name")
    async def create_empty_party(self, gamespace_id, game_name, game_version, game_server_name, region_id,
                                 party_settings, room_settings, max_members,
                                 party_flags, close_callback=None):

        if max_members < 2:
            raise PartyError(400, "'max_members' cannot be lass than 2")

        try:
            gs = await self.gameservers.find_game_server(gamespace_id, game_name, game_server_name)
        except GameServerNotFound:
            raise PartyError(400, "No such game server")

        async with self.db.acquire(auto_commit=True) as db:
            try:
                party_id = await db.insert(
                    """
                    INSERT INTO `parties` 
                    (`gamespace_id`, `party_num_members`, `party_max_members`, `game_name`, `game_version`, 
                     `game_server_id`, `region_id`, `party_settings`, `room_settings`, `party_status`, 
                     `party_flags`, `party_close_callback`) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """, gamespace_id, 0, max_members, game_name, game_version,
                    gs.game_server_id, region_id, ujson.dumps(party_settings), ujson.dumps(room_settings),
                    PartyStatus.CREATED, party_flags.dump(), close_callback)
            except DatabaseError as e:
                raise PartyError(500, "Failed to create new party: " + e.args[1])

            party = PartyAdapter({
                "party_id": party_id,
                "party_num_members": 1,
                "party_max_members": max_members,
                "party_flags": party_flags.dump(),
                "game_name": game_name,
                "game_version": game_version,
                "game_server_id": gs.game_server_id,
                "party_settings": party_settings,
                "room_settings": room_settings,
                "party_close_callback": close_callback,
                "region_id": region_id
            })

            return party

    async def list_party_members(self, gamespace_id, party_id, db=None):
        try:
            hosts = await (db or self.db).query(
                """
                SELECT *
                FROM `party_members`
                WHERE `gamespace_id`=%s AND `party_id`=%s;
                """, gamespace_id, party_id)
        except DatabaseError as e:
            raise PartyError(500, "Failed to list party members: " + e.args[1])

        return list(map(PartyMemberAdapter, hosts))

    async def close_party(self, gamespace_id, party_id, message, reason="close"):

        async with self.db.acquire(auto_commit=False) as db:
            try:

                party_data = await db.get(
                    """
                    SELECT *
                    FROM `parties`
                    WHERE `gamespace_id`=%s AND `party_id`=%s
                    LIMIT 1
                    FOR UPDATE;
                    """, gamespace_id, party_id)

                if party_data is None:
                    raise NoSuchParty()

                party = PartyAdapter(party_data)

                if party.party_num_members:
                    await PartySession.drop_message(
                        gamespace_id, party_id, self.party_broker,
                        PartySession.MESSAGE_TYPE_PARTY_CLOSED, message,
                        routing_key='all.' + str(gamespace_id))

                await self.delete_party(gamespace_id, party_id, db=db)

                if party.close_callback:
                    try:
                        result = await self.__party_close_callback__(
                            gamespace_id, party.close_callback,
                            party=party.dump(), message=message, reason=reason)
                    except PartyError:
                        logging.exception("Failed to call close_callback: {0}".format(party.close_callback))
                    else:
                        return result

            finally:
                await db.commit()

    async def delete_party(self, gamespace_id, party_id, db=None):
        try:
            await (db or self.db).execute(
                """
                DELETE FROM `party_members`
                WHERE `gamespace_id`=%s AND `party_id`=%s;
                """, gamespace_id, party_id)
            await (db or self.db).execute(
                """
                DELETE FROM `parties`
                WHERE `gamespace_id`=%s AND `party_id`=%s
                LIMIT 1;
                """, gamespace_id, party_id)
        except DatabaseError as e:
            raise PartyError(500, "Failed to delete a party: " + e.args[1])

    async def __remove_party_member__(self, gamespace_id, party_id, account_id, add_member=True):

        async with self.db.acquire(auto_commit=False) as db:
            try:
                try:
                    deleted = await db.execute(
                        """
                        DELETE FROM `party_members`
                        WHERE `gamespace_id`=%s AND `account_id`=%s AND `party_id`=%s;
                        """, gamespace_id, account_id, party_id)
                except DatabaseError as e:
                    raise PartyError(500, "Failed to leave a party: " + e.args[1])

                if not deleted:
                    raise PartyError(409, "No such player to leave a party")

                party_data = await db.get(
                    """
                    SELECT *
                    FROM `parties`
                    WHERE `gamespace_id`=%s AND `party_id`=%s
                    LIMIT 1
                    FOR UPDATE;
                    """, gamespace_id, party_id)

                if party_data is None:
                    raise NoSuchParty()

                party = PartyAdapter(party_data)

                if party.party_num_members <= 0:
                    raise PartyError(406, "Party is empty already")

                if add_member:
                    party.party_num_members -= 1

                    if (PartyFlags.AUTO_CLOSE in party.flags) and (party.party_num_members == 0):
                        await self.delete_party(gamespace_id, party_id, db=db)

                        if party.close_callback:
                            try:
                                result = await self.__party_close_callback__(
                                    gamespace_id, party.close_callback,
                                    party=party.dump(), reason="leave")
                            except PartyError:
                                logging.exception("Failed to call close_callback: {0}".format(party.close_callback))
                            else:
                                return result
                    else:
                        try:
                            await db.execute(
                                """
                                UPDATE `parties`
                                SET `party_num_members`=%s
                                WHERE `gamespace_id`=%s AND `party_id`=%s
                                LIMIT 1;
                                """, party.party_num_members, gamespace_id, party_id)
                        except DatabaseError as e:
                            raise PartyError(500, "Failed to register a player into a party: " + e.args[1])

            finally:
                await db.commit()

    @validate(gamespace_id="int", game_name="str_name", game_version="str_name", game_server_name="str_name",
              region_id="int", party_filter="json_dict", account_id="int",
              member_profile="json_dict", member_token="str",
              auto_create="bool", create_party_settings="json_dict", create_room_settings="json_dict",
              create_room_filters="json_dict", create_max_members="int", create_flags=PartyFlags,
              create_close_callback="str_name")
    async def join_party(self, gamespace_id, game_name, game_version, game_server_name, region_id,
                         party_filter, account_id, member_profile, member_token,
                         auto_create, create_party_settings, create_room_settings,
                         create_max_members, create_flags, create_room_filters=None,
                         create_close_callback=None, session_callback=None):

        if create_max_members < 2:
            raise PartyError(400, "'max_members' cannot be lass than 2")

        try:
            game_server_id = await self.gameservers.find_game_server(gamespace_id, game_name, game_server_name)
        except GameServerNotFound:
            raise PartyError(400, "No such game server")

        try:
            conditions = format_conditions_json('party_settings', party_filter)
        except ConditionError as e:
            raise RoomError(str(e))

        async with self.db.acquire(auto_commit=False) as db:
            try:
                query = PartyQuery(gamespace_id, game_name, game_version, game_server_id)

                query.add_conditions(conditions)
                query.for_update = True
                query.show_full = False
                query.region_id = region_id

                party = await query.query(db, one=True)

                if party is None:
                    await db.commit()

                    if auto_create:
                        result = await self.create_party(
                            gamespace_id, game_name, game_version, game_server_name, region_id,
                            create_party_settings, create_room_settings, create_max_members, account_id,
                            member_profile, member_token, party_flags=create_flags, auto_join=True,
                            close_callback=create_close_callback, room_filters=create_room_filters)
                        return result
                    else:
                        raise NoSuchParty()

                if party.status != PartyStatus.CREATED:
                    raise PartyError(409, "Party have already started a game")

                if party.party_num_members >= party.party_max_members:
                    raise PartyError(406, "Party is full")

                party.party_num_members += 1

                try:
                    await db.insert(
                        """
                        INSERT INTO `party_members`
                        (`account_id`, `gamespace_id`, `party_id`, `member_role`, `member_profile`, `member_token`) 
                        VALUES (%s, %s, %s, %s, %s, %s);
                        """, account_id, gamespace_id, party.id, PartyModel.PARTY_ROLE_USER,
                        ujson.dumps(member_profile), member_token)
                except DatabaseError as e:
                    # well, we've tried
                    await db.rollback()
                    raise PartyError(500, "Failed to join a party: " + e.args[1])

                role = PartyModel.PARTY_ROLE_USER

                if (PartyFlags.AUTO_START in party.flags) and (party.party_max_members == party.party_num_members):
                    session = PartySession(
                        gamespace_id, account_id, member_profile, role, self.party_broker, party, self.db, self)
                    await session.init()
                    await session.__start_game__({
                        "room_settings": party.room_settings
                    }, check_permissions=False)
                    return session

                try:
                    await db.execute(
                        """
                        UPDATE `parties`
                        SET `party_num_members`=%s
                        WHERE `gamespace_id`=%s AND `party_id`=%s
                        LIMIT 1;
                        """, party.party_num_members, gamespace_id, party.id)
                except DatabaseError as e:
                    raise PartyError(500, "Failed to register a player into a party: " + e.args[1])

                session = PartySession(
                    gamespace_id, account_id,
                    self.party_broker, party,
                    self.db, self, role, member_token)
                session.joined(member_profile, send_new_player=True)

                await session.init()

                if session_callback:
                    await session_callback(session)

                await session.start_game_if_needed()
                return session

            finally:
                await db.commit()

    @validate(gamespace_id="int", party_id="int", account_id="int", member_profile="json_dict", member_token="str",
              check_members="json_dict", auto_join="bool")
    async def party_session(self, gamespace_id, party_id, account_id, member_token,
                            member_profile=None, check_members=None, auto_join=True,
                            session_callback=None):

        async with self.db.acquire(auto_commit=False) as db:
            try:
                party_data = await db.get(
                    """
                    SELECT *
                    FROM `parties`
                    WHERE `gamespace_id`=%s AND `party_id`=%s
                    LIMIT 1
                    FOR UPDATE;
                    """, gamespace_id, party_id)

                if party_data is None:
                    raise NoSuchParty()

                party = PartyAdapter(party_data)

                if party.party_num_members >= party.party_max_members:
                    raise PartyError(406, "Party is full")

                members = await self.list_party_members(gamespace_id, party_id, db=db)

                if check_members:
                    merged_profiles = PredefinedProfile({
                        "members": [member.dump() for member in members]
                    })

                    try:
                        await merged_profiles.set_data(check_members, None, merge=True)
                    except ProfileError as e:
                        raise PartyError(409, "Member check failed: " + str(e.message))

                session = PartySession(
                    gamespace_id, account_id,
                    self.party_broker, party,
                    self.db, self, PartyModel.PARTY_ROLE_USER, member_token)

                if auto_join:
                    party.party_num_members += 1

                    try:
                        await db.insert(
                            """
                            INSERT INTO `party_members`
                            (`account_id`, `gamespace_id`, `party_id`, `member_role`, 
                             `member_profile`, `member_token`) 
                            VALUES (%s, %s, %s, %s, %s, %s); 
                            """, account_id, gamespace_id, party_id, PartyModel.PARTY_ROLE_USER,
                            ujson.dumps(member_profile), member_token)
                    except DatabaseError as e:
                        # well, we've tried
                        await db.rollback()
                        raise PartyError(500, "Failed to join a party: " + e.args[1])

                    session.joined(member_profile, send_new_player=True)

                    members.append(PartyMemberAdapter({
                        "account_id": account_id,
                        "member_role": PartyModel.PARTY_ROLE_USER,
                        "member_profile": member_profile,
                        "member_token": member_token
                    }))

                    try:
                        await db.execute(
                            """
                            UPDATE `parties`
                            SET `party_num_members`=%s
                            WHERE `gamespace_id`=%s AND `party_id`=%s
                            LIMIT 1;
                            """, party.party_num_members, gamespace_id, party_id)
                    except DatabaseError as e:
                        raise PartyError(500, "Failed to register a player into a party: " + e.args[1])

                await db.commit()

                await session.init(members=members)

                if session_callback:
                    await session_callback(session)

                await session.start_game_if_needed()
                return session

            finally:
                await db.commit()

    async def __party_close_callback__(self, gamespace_id, close_callback, **args):
        try:
            result = await self.internal.request(
                "exec", "call_server_function",
                gamespace=gamespace_id, method_name=close_callback, args=args, env={})
        except InternalError as e:
            raise PartyError(e.code, str(e))

        return result

    @validate(gamespace_id="int", party_id="int")
    async def get_party(self, gamespace_id, party_id):
        try:
            party = await self.db.get(
                """
                SELECT *
                FROM `parties`
                WHERE `gamespace_id`=%s AND `party_id`=%s
                LIMIT 1;
                """, gamespace_id, party_id
            )
        except DatabaseError as e:
            raise PartyError(500, "Failed to get party: " + e.args[1])

        if party is None:
            raise NoSuchParty()

        return PartyAdapter(party)
