
from tornado.gen import coroutine, Return
import common.database
from common.model import Model
from common.validate import validate


class BanError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class UserAlreadyBanned(Exception):
    pass


class NoSuchBan(Exception):
    pass


class BanAdapter(object):
    def __init__(self, data):
        self.ban_id = str(data.get("ban_id"))
        self.account = data.get("ban_account")
        self.ip = data.get("ban_ip")
        self.expires = data.get("ban_expires")
        self.reason = data.get("ban_reason")

    def dump(self):
        return {
            "id": self.ban_id,
            "account": str(self.account),
            "ip": str(self.ip),
            "expires": str(self.expires),
            "reason": str(self.reason)
        }


class BansModel(Model):
    def __init__(self, db):
        self.db = db

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["bans"]

    @coroutine
    @validate(gamespace="int", account="int", expires="datetime", reason="str")
    def new_ban(self, gamespace, account, expires, reason):

        try:
            ban_id = yield self.db.insert(
                """
                INSERT INTO `bans`
                (`ban_gamespace`, `ban_account`, `ban_expires`, `ban_reason`)
                VALUES (%s, %s, %s, %s)
                """, gamespace, account, expires, reason
            )
        except common.database.DuplicateError:
            raise UserAlreadyBanned()
        except common.database.DatabaseError as e:
            raise BanError("Failed to ban user: " + e.args[1])
        else:
            raise Return(ban_id)

    @coroutine
    @validate(gamespace="int", ban_id="int", ban_expires="datetime", ban_reason="str")
    def update_ban(self, gamespace, ban_id, ban_expires, ban_reason):
        try:
            yield self.db.execute(
                """
                UPDATE `bans`
                SET `ban_expires`=%s, `ban_reason`=%s
                WHERE `ban_id`=%s AND `ban_gamespace`=%s;
                """, ban_expires, ban_reason, ban_id, gamespace
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to update ban: " + e.args[1])

    @coroutine
    def update_ban_ip(self, gamespace, ban_id, ban_ip):
        try:
            yield self.db.execute(
                """
                UPDATE `bans`
                SET `ban_ip`=%s
                WHERE `ban_id`=%s AND `ban_gamespace`=%s;
                """, ban_ip, ban_id, gamespace
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to update ban: " + e.args[1])

    @coroutine
    def get_ban(self, gamespace, ban_id):
        try:
            ban = yield self.db.get(
                """
                SELECT *
                FROM `bans`
                WHERE `ban_gamespace`=%s AND `ban_id`=%s
                LIMIT 1;
                """, gamespace, ban_id
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to get server: " + e.args[1])

        if ban is None:
            raise NoSuchBan()

        raise Return(BanAdapter(ban))

    @coroutine
    def get_active_ban_by_account(self, gamespace, account):
        try:
            ban = yield self.db.get(
                """
                SELECT *
                FROM `bans`
                WHERE `ban_gamespace`=%s AND `ban_account`=%s AND `ban_expires` > NOW()
                LIMIT 1;
                """, gamespace, account
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to get server: " + e.args[1])

        if ban is None:
            raise NoSuchBan()

        raise Return(BanAdapter(ban))

    @coroutine
    def get_ban_by_account(self, gamespace, account):
        try:
            ban = yield self.db.get(
                """
                SELECT *
                FROM `bans`
                WHERE `ban_gamespace`=%s AND `ban_account`=%s
                LIMIT 1;
                """, gamespace, account
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to get server: " + e.args[1])

        if ban is None:
            raise NoSuchBan()

        raise Return(BanAdapter(ban))

    @coroutine
    def get_ban_by_ip(self, gamespace, ip):
        try:
            ban = yield self.db.get(
                """
                SELECT *
                FROM `bans`
                WHERE `ban_gamespace`=%s AND `ban_ip`=%s
                LIMIT 1;
                """, gamespace, ip
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to get server: " + e.args[1])

        if ban is None:
            raise NoSuchBan()

        raise Return(BanAdapter(ban))

    @coroutine
    def lookup_ban(self, gamespace, account, account_ip):
        ban = yield self.find_active_ban(gamespace, account, account_ip)

        if ban and (not ban.ip):
            yield self.update_ban_ip(gamespace, ban.ban_id, account_ip)
            ban.ip = account_ip

        raise Return(ban)

    @coroutine
    def find_active_ban(self, gamespace, account, account_ip):
        try:
            ban = yield self.db.get(
                """
                SELECT *
                FROM `bans`
                WHERE `ban_gamespace`=%s
                    AND (`ban_account`=%s OR `ban_ip`=%s)
                    AND `ban_expires` > NOW()
                LIMIT 1;
                """, gamespace, account, account_ip
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to get server: " + e.args[1])

        if ban is None:
            raise Return(None)

        raise Return(BanAdapter(ban))

    @coroutine
    @validate(gamespace="int", accounts="json_list_of_ints", ips="json_list_of_strings")
    def find_bans(self, gamespace, accounts, ips):
        """
        Returns a list of accounts that are banned for a list of input accounts/ips
        :param gamespace: A gamespace to check in
        :param accounts: a list of account id's to check
        :param ips: a list of ip aderssed to check
        :return:
        """

        if not accounts or not ips:
            raise BanError("accounts or ips is empty")

        try:
            bans = yield self.db.query(
                """
                SELECT `ban_account`
                FROM `bans`
                WHERE `ban_gamespace`=%s
                    AND (`ban_account` IN %s OR `ban_ip` IN %s)
                    AND `ban_expires` > NOW()
                LIMIT 1;
                """, gamespace, accounts, ips
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to get server: " + e.args[1])

        result = [
            ban["ban_account"]
            for ban in bans
        ]

        raise Return(result)

    @coroutine
    @validate(gamespace="int", ban_id="int")
    def delete_ban(self, gamespace, ban_id):

        try:
            yield self.db.execute(
                """
                DELETE FROM `bans`
                WHERE `ban_gamespace`=%s AND `ban_id`=%s;
                """, gamespace, ban_id
            )
        except common.database.DatabaseError as e:
            raise BanError("Failed to delete a server: " + e.args[1])
