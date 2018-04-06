import ujson

from tornado.gen import coroutine, Return
from tornado.web import HTTPError

from common.access import scoped, internal, AccessToken, remote_ip
from common.handler import AuthenticatedHandler, JsonRPCWSHandler
from common.validate import ValidationError
from common.internal import InternalError
from common.jsonrpc import JsonRPCError, JsonRPCTimeout

from model.host import RegionNotFound, HostNotFound, HostError, RegionError
from model.controller import ControllerError
from model.player import Player, PlayersGroup, RoomNotFound, PlayerError, RoomError, PlayerBanned
from model.gameserver import GameServerNotFound
from model.party import PartySession, PartyError, NoSuchParty, PartyFlags
from model.ban import UserAlreadyBanned, BanError, NoSuchBan

import logging

from geoip import geolite2


class InternalHandler(object):
    def __init__(self, application):
        self.application = application

    @coroutine
    def host_heartbeat(self, name, memory, cpu):
        logging.info("Host '{0}' load updated: {1} memory {2} cpu".format(name, memory, cpu))

        hosts = self.application.hosts

        try:
            host = yield hosts.find_host(name)
        except HostNotFound:
            raise InternalError(404, "Host not found")

        try:
            yield hosts.update_host_load(host.host_id, memory, cpu)
        except HostError as e:
            raise InternalError(500, str(e))

    @coroutine
    def controller_action(self, action, gamespace, room_id, args, kwargs):
        try:
            result = yield self.application.ctl_client.received(gamespace, room_id, action, args, kwargs) or {}
        except ControllerError as e:
            raise InternalError(e.code, e.message)

        raise Return(result)

    @coroutine
    def issue_ban(self, gamespace, account, reason, expires):
        bans = self.application.bans

        try:
            ban_id = yield bans.new_ban(gamespace, account, expires, reason)
        except ValidationError as e:
            raise InternalError(400, e.message)
        except BanError as e:
            raise InternalError(500, e.message)
        except UserAlreadyBanned:
            raise InternalError(406, "This user have already been banned")

        raise Return({
            "id": ban_id
        })

    @coroutine
    def get_ban(self, gamespace, ban_id):
        bans = self.application.bans

        try:
            ban = yield bans.get_ban(gamespace, ban_id)
        except ValidationError as e:
            raise InternalError(400, e.message)
        except BanError as e:
            raise InternalError(409, e.message)
        except NoSuchBan:
            raise InternalError(404, "No such ban")

        raise Return({
            "ban": ban.dump()
        })

    @coroutine
    def update_ban(self, gamespace, ban_id, reason, expires):
        bans = self.application.bans

        try:
            yield bans.update_ban(gamespace, ban_id, expires, reason)
        except ValidationError as e:
            raise InternalError(400, e.message)
        except BanError as e:
            raise InternalError(409, e.message)

        raise Return("OK")

    @coroutine
    def delete_ban(self, gamespace, ban_id):
        bans = self.application.bans

        try:
            yield bans.delete_ban(gamespace, ban_id)
        except ValidationError as e:
            raise InternalError(400, e.message)
        except BanError as e:
            raise InternalError(409, e.message)

        raise Return("OK")

    @coroutine
    def find_ban(self, gamespace, account_id):
        bans = self.application.bans

        try:
            ban = yield bans.get_active_ban_by_account(gamespace, account_id)
        except ValidationError as e:
            raise InternalError(400, e.message)
        except BanError as e:
            raise InternalError(409, e.message)
        except NoSuchBan:
            raise InternalError(404, "No such ban")

        raise Return({
            "ban": ban.dump()
        })


class JoinHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def post(self, game_name, game_server_name, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account = self.token.account

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
            create_settings = ujson.loads(self.get_argument("create_settings", "{}"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        player_info = {}

        player = Player(self.application, gamespace, game_name, game_version,
                        game_server_name, account, self.token.key, player_info, ip)

        lock_my_region = self.get_argument("my_region_only", "false") == "true"
        selected_region = self.get_argument("region", None)
        auto_create = self.get_argument("auto_create", "true") == "true"

        try:
            yield player.init()
        except PlayerBanned as e:
            ban = e.ban

            logging.info("Banned user trying to join a game: @{0} ban {1}".format(ban.account, ban.ban_id))

            self.set_header("X-Ban-Until", ban.expires)
            self.set_header("X-Ban-Id", ban.ban_id)
            self.set_header("X-Ban-Reason", ban.reason)
            self.set_status(423, "You have been banned until: " + str(ban.expires))
            return
        except PlayerError as e:
            raise HTTPError(e.code, e.message)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        try:
            result = yield player.join(
                settings,
                auto_create=auto_create,
                create_room_settings=create_settings,
                lock_my_region=lock_my_region,
                selected_region=selected_region)
        except RoomNotFound as e:
            raise HTTPError(404, "No such room found")
        except PlayerError as e:
            raise HTTPError(e.code, e.message)

        self.dumps(result)


class JoinMultiHandler(AuthenticatedHandler):
    @scoped(scopes=["game", "game_multi"])
    @coroutine
    def post(self, game_name, game_server_name, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
            create_settings = ujson.loads(self.get_argument("create_settings", "{}"))
            accounts = ujson.loads(self.get_argument("accounts"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        lock_my_region = self.get_argument("my_region_only", "false") == "true"
        auto_create = self.get_argument("auto_create", "true") == "true"

        if not isinstance(accounts, list):
            raise HTTPError(400, "Accounts should be a list")

        ip = self.get_argument("ip", None)

        if ip:
            if not isinstance(ip, (str, unicode)):
                raise HTTPError(400, "ip is not a string")
        else:
            ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        players = PlayersGroup(self.application, gamespace, game_name, game_version,
                               game_server_name, accounts, ip)

        try:
            yield players.init()
        except PlayerError as e:
            raise HTTPError(e.code, e.message)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        try:
            results = yield players.join(
                settings,
                auto_create=auto_create,
                create_room_settings=create_settings,
                lock_my_region=lock_my_region)
        except RoomNotFound as e:
            raise HTTPError(404, "No such room found")
        except PlayerError as e:
            raise HTTPError(e.code, e.message)

        self.dumps(results)


class JoinRoomHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def post(self, game_name, room_id):

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account = self.token.account

        ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        ban = yield self.application.bans.lookup_ban(gamespace, account, ip)

        if ban:
            logging.info("Banned user trying to join a game: @{0} ban {1}".format(ban.account, ban.ban_id))

            self.set_header("X-Ban-Until", ban.expires)
            self.set_header("X-Ban-Id", ban.ban_id)
            self.set_header("X-Ban-Reason", ban.reason)
            self.set_status(423, "You have been banned until: " + str(ban.expires))
            return

        try:
            record_id, key, room = yield self.application.rooms.join_room(
                gamespace, game_name, room_id, account, self.token.key, {})
        except RoomNotFound:
            raise HTTPError(404, "Room not found")
        except RoomError as e:
            raise HTTPError(400, e.message)

        result = room.dump()
        result.update({
            "key": key,
            "slot": record_id
        })

        self.dumps(result)


class CreateHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def post(self, game_name, game_server_name, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account = self.token.account

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        player_info = {}

        player = Player(self.application, gamespace, game_name, game_version,
                        game_server_name, account, self.token.key, player_info, ip)

        try:
            yield player.init()
        except PlayerBanned as e:
            ban = e.ban

            logging.info("Banned user trying to join a game: @{0} ban {1}".format(ban.account, ban.ban_id))

            self.set_header("X-Ban-Until", ban.expires)
            self.set_header("X-Ban-Id", ban.ban_id)
            self.set_header("X-Ban-Reason", ban.reason)
            self.set_status(423, "You have been banned until: " + str(ban.expires))
            return
        except PlayerError as e:
            raise HTTPError(e.code, e.message)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        try:
            result = yield player.create(settings)
        except PlayerError as e:
            raise HTTPError(e.code, e.message)

        self.dumps(result)


class CreateMultiHandler(AuthenticatedHandler):
    @scoped(scopes=["game", "game_multi"])
    @coroutine
    def post(self, game_name, game_server_name, game_version):

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
            accounts = ujson.loads(self.get_argument("accounts"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        ip = self.get_argument("ip", None)

        if ip:
            if not isinstance(ip, (str, unicode)):
                raise HTTPError(400, "ip is not a string")
        else:
            ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        if not isinstance(accounts, list):
            raise HTTPError(400, "Accounts should be a list")

        player = PlayersGroup(
            self.application, gamespace, game_name, game_version,
            game_server_name, accounts, ip)

        try:
            yield player.init()
        except PlayerError as e:
            raise HTTPError(e.code, e.message)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        try:
            result = yield player.create(settings)
        except PlayerError as e:
            raise HTTPError(e.code, e.message)

        self.dumps(result)


class RoomsHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def get(self, game_name, game_server_name, game_version):
        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            settings = ujson.loads(self.get_argument("settings", "{}"))
        except ValueError:
            raise HTTPError(400, "Corrupted JSON")

        try:
            gs = yield self.application.gameservers.find_game_server(
                gamespace, game_name, game_server_name)
        except GameServerNotFound:
            raise HTTPError(404, "No such game server")

        game_server_id = gs.game_server_id

        rooms_data = self.application.rooms
        hosts = self.application.hosts
        region_lock = None
        ordered_regions = None

        show_full = self.get_argument("show_full", "true") == "true"
        lock_my_region = self.get_argument("my_region_only", "false") == "true"
        selected_region = self.get_argument("region", None)

        if selected_region:
            try:
                region_lock = yield hosts.find_region(selected_region)
            except RegionNotFound:
                raise HTTPError(404, "No such region")
        else:
            ip = remote_ip(self.request)

            if ip is None:
                raise HTTPError(400, "Bad IP")

            geo = geolite2.lookup(ip)

            if geo:
                p_lat, p_long = geo.location

                if lock_my_region:
                    try:
                        region_lock = yield hosts.get_closest_region(p_long, p_lat)
                    except RegionNotFound:
                        pass

                if not region_lock:
                    closest_regions = yield hosts.list_closest_regions(p_long, p_lat)
                    ordered_regions = [region.region_id for region in closest_regions]
            else:
                ordered_regions = None

        try:
            rooms = yield rooms_data.list_rooms(
                gamespace, game_name, game_version,
                game_server_id, settings,
                regions_order=ordered_regions,
                show_full=show_full,
                region=region_lock)
        except RoomError as e:
            raise HTTPError(400, e.message)

        self.dumps({
            "rooms": [
                room.dump()
                for room in rooms
            ]
        })


class RegionsHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def get(self):
        hosts = self.application.hosts

        try:
            regions = yield hosts.list_regions()
        except RegionError as e:
            raise HTTPError(500, e.message)

        ip = remote_ip(self.request)

        if ip is None:
            raise HTTPError(400, "Bad IP")

        geo = geolite2.lookup(ip)

        my_region = None

        if geo:
            p_lat, p_long = geo.location

            try:
                my_region = yield hosts.get_closest_region(p_long, p_lat)
            except RegionNotFound:
                pass

        if not my_region:
            try:
                my_region = yield hosts.get_default_region()
            except RegionNotFound:
                raise HTTPError(500, "No default region is defined")

        self.dumps({
            "regions": {
                region.name : {
                    "settings": region.settings,
                    "location": {
                        "x": region.geo_location[0],
                        "y": region.geo_location[1],
                    }
                }

                for region in regions
            },
            "my_region": my_region.name
        })


class StatusHandler(AuthenticatedHandler):
    @coroutine
    def get(self):

        try:
            players_count = yield self.application.rooms.get_players_count()
        except RoomError as e:
            raise HTTPError(500, e.message)

        self.dumps({
            "players": players_count
        })


class PlayerRecordsHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def get(self, account_id):

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            players_records = yield self.application.rooms.list_player_records(
                gamespace, account_id)
        except RoomError as e:
            raise HTTPError(500, e.message)

        self.dumps({
            "records": [
                players_record.dump()
                for players_record in players_records
            ]
        })


class MultiplePlayersRecordsHandler(AuthenticatedHandler):
    @scoped(scopes=["game"])
    @coroutine
    def get(self):

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            account_ids = ujson.loads(self.get_argument("accounts"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted 'accounts' JSON")

        try:
            players_records = yield self.application.rooms.list_players_records(
                gamespace, account_ids)
        except ValueError as e:
            raise HTTPError(400, e.message)
        except RoomError as e:
            raise HTTPError(500, e.message)

        self.dumps({
            "records": {
                account_id: [
                    r.dump()
                    for r in player_records
                ]
                for account_id, player_records in players_records.iteritems()
            }
        })


class PartyHandler(JsonRPCWSHandler):
    def __init__(self, application, request, **kwargs):
        super(PartyHandler, self).__init__(application, request, **kwargs)
        self.session = None

    @coroutine
    def _on_message(self, message_type, payload):

        try:
            yield self.send_request(
                self,
                "message",
                message_type=message_type,
                payload=payload)
        except JsonRPCTimeout:
            logging.error("Timeout during _on_message request")
        except JsonRPCError:
            pass

    @coroutine
    def _on_close(self, code, reason):
        self.close(code, reason)

    @coroutine
    def _inited(self, session):
        self.session = session

        self.session.set_on_message(self._on_message)
        self.session.set_on_close(self._on_close)

        logging.info("Party session gs:{0} pt:{1} acc:{2} started.".format(
            self.session.gamespace_id,
            self.session.party.id,
            self.session.account_id
        ))

        yield self.send_rpc(
            self,
            "party",
            party_info=self.session.dump())

    @coroutine
    def send_message(self, payload):
        if not self.session:
            return

        try:
            result = yield self.session.send_message(PartySession.MESSAGE_TYPE_CUSTOM, payload)
        except PartyError as e:
            raise JsonRPCError(e.code, e.message)

        raise Return(result)

    @coroutine
    def close_party(self, message):
        if not self.session:
            return

        try:
            result = yield self.session.close_party(message)
        except NoSuchParty:
            raise JsonRPCError(404, "No such party to close.")
        except PartyError as e:
            raise JsonRPCError(e.code, e.message)

        raise Return(result)

    @coroutine
    def join_party(self, member_profile, check_members=None):
        if not self.session:
            return

        try:
            result = yield self.session.join_party(member_profile, check_members=check_members)
        except NoSuchParty:
            raise JsonRPCError(404, "No such party to start game.")
        except PartyError as e:
            raise JsonRPCError(e.code, e.message)

        raise Return(result)

    @coroutine
    def leave_party(self):
        if not self.session:
            return

        try:
            result = yield self.session.leave_party()
        except NoSuchParty:
            raise JsonRPCError(404, "No such party to start game.")
        except PartyError as e:
            raise JsonRPCError(e.code, e.message)

        raise Return(result)

    @coroutine
    def start_game(self, message):
        if not self.session:
            return

        try:
            result = yield self.session.start_game(message)
        except NoSuchParty:
            raise JsonRPCError(404, "No such party to start game.")
        except PartyError as e:
            raise JsonRPCError(e.code, e.message)

        raise Return(result)

    @coroutine
    def on_closed(self):
        if not self.session:
            return

        yield self.session.release()
        logging.info("Party session gs:{0} pt:{1} acc:{2} closed.".format(
            self.session.gamespace_id,
            self.session.party.id,
            self.session.account_id
        ))
        self.session = None


class CreatePartySimpleHandler(AuthenticatedHandler):
    @scoped(scopes=["party_create"])
    @coroutine
    def post(self, game_name, game_version, game_server_name):
        parties = self.application.parties
        hosts = self.application.hosts

        try:
            party_settings = ujson.loads(self.get_argument("party_settings", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted party settings")

        try:
            room_settings = ujson.loads(self.get_argument("room_settings", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted room settings")

        gamespace = self.token.get(AccessToken.GAMESPACE)

        close_callback = self.get_argument("close_callback", None)
        max_members = self.get_argument("max_members", 8)
        selected_region = self.get_argument("region", None)
        my_region = None

        party_flags = PartyFlags()

        if self.get_argument("auto_start", "true") == "true":
            party_flags.set(PartyFlags.AUTO_START)
        if self.get_argument("auto_close", "true") == "true":
            party_flags.set(PartyFlags.AUTO_CLOSE)

        if selected_region:
            try:
                my_region = yield hosts.find_region(selected_region)
            except RegionNotFound:
                raise HTTPError(404, "No such region")
        else:
            ip = remote_ip(self.request)

            if ip is None:
                raise HTTPError(400, "Bad IP")

            geo = geolite2.lookup(ip)

            if geo:
                p_lat, p_long = geo.location

                try:
                    my_region = yield hosts.get_closest_region(p_long, p_lat)
                except RegionNotFound:
                    pass

        if not my_region:
            try:
                my_region = yield hosts.get_default_region()
            except RegionNotFound:
                raise HTTPError(410, "No default region defined")

        try:
            session = yield parties.create_empty_party(
                gamespace, game_name, game_version, game_server_name,
                my_region.region_id, party_settings, room_settings, max_members,
                party_flags=party_flags, close_callback=close_callback)
        except ValidationError as e:
            raise HTTPError(400, e.message)
        except PartyError as e:
            raise HTTPError(e.code, e.message)
        except NoSuchParty:
            raise HTTPError(404, "No such party")

        self.dumps({
            "party": session.dump()
        })


class CreatePartySessionHandler(PartyHandler):

    def __init__(self, application, request, **kwargs):
        super(CreatePartySessionHandler, self).__init__(application, request, **kwargs)

    def required_scopes(self):
        return ["party_create"]

    def check_origin(self, origin):
        return True

    @coroutine
    def on_opened(self, game_name, game_version, game_server_name, *ignored, **ignored_kw):

        parties = self.application.parties
        hosts = self.application.hosts

        party_flags = PartyFlags()

        if self.get_argument("auto_start", "true") == "true":
            party_flags.set(PartyFlags.AUTO_START)
        if self.get_argument("auto_close", "true") == "true":
            party_flags.set(PartyFlags.AUTO_CLOSE)

        try:
            party_settings = ujson.loads(self.get_argument("party_settings", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted party settings")

        try:
            room_settings = ujson.loads(self.get_argument("room_settings", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted room settings")

        try:
            room_filters = ujson.loads(self.get_argument("room_filters", "null"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted room settings")

        auto_join = self.get_argument("auto_join", "true") == "true"

        if auto_join:
            try:
                member_profile = ujson.loads(self.get_argument("member_profile", "{}"))
            except (KeyError, ValueError):
                raise HTTPError(3400, "Corrupted member profile settings")
        else:
            member_profile = None

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account_id = self.token.account

        close_callback = self.get_argument("close_callback", None)
        max_members = self.get_argument("max_members", 8)
        selected_region = self.get_argument("region", None)
        my_region = None

        if selected_region:
            try:
                my_region = yield hosts.find_region(selected_region)
            except RegionNotFound:
                raise HTTPError(3404, "No such region")
        else:
            ip = remote_ip(self.request)

            if ip is None:
                raise HTTPError(3400, "Bad IP")

            geo = geolite2.lookup(ip)

            if geo:
                p_lat, p_long = geo.location

                try:
                    my_region = yield hosts.get_closest_region(p_long, p_lat)
                except RegionNotFound:
                    pass

        if not my_region:
            try:
                my_region = yield hosts.get_default_region()
            except RegionNotFound:
                raise HTTPError(3410, "No default region defined")

        try:
            yield parties.create_party(
                gamespace, game_name, game_version, game_server_name,
                my_region.region_id, party_settings, room_settings, max_members,
                account_id, member_profile, self.token.key,
                party_flags=party_flags, auto_join=auto_join, close_callback=close_callback,
                room_filters=room_filters, session_callback=self._inited)
        except ValidationError as e:
            raise HTTPError(3400, e.message)
        except PartyError as e:
            logging.exception("Failed to open party session")
            raise HTTPError(3000 + e.code, e.message)
        except NoSuchParty:
            raise HTTPError(3404, "No such party")


class PartiesSearchHandler(PartyHandler):

    def __init__(self, application, request, **kwargs):
        super(PartiesSearchHandler, self).__init__(application, request, **kwargs)

    def required_scopes(self):
        return ["party"]

    def check_origin(self, origin):
        return True

    @coroutine
    def on_opened(self, game_name, game_version, game_server_name, *ignored, **ignored_kw):

        parties = self.application.parties
        hosts = self.application.hosts

        try:
            party_filter = ujson.loads(self.get_argument("party_filter"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted party filter")

        try:
            party_settings = ujson.loads(self.get_argument("create_party_settings", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted party settings")

        try:
            room_settings = ujson.loads(self.get_argument("create_room_settings", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted room settings")

        try:
            room_filters = ujson.loads(self.get_argument("create_room_filters", "null"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted room filters")

        try:
            member_profile = ujson.loads(self.get_argument("member_profile", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted member profile settings")

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account_id = self.token.account

        create_party_flags = PartyFlags()

        if self.get_argument("create_auto_start", "true") == "true":
            create_party_flags.set(PartyFlags.AUTO_START)
        if self.get_argument("create_auto_close", "true") == "true":
            create_party_flags.set(PartyFlags.AUTO_CLOSE)

        close_callback = self.get_argument("create_close_callback", None)
        max_members = self.get_argument("max_members", 8)
        selected_region = self.get_argument("region", None)
        auto_create = self.get_argument("auto_create", "false") == "true"
        my_region = None

        if auto_create and (not self.token.has_scopes(["party_create"])):
            raise HTTPError(3403, "Scope 'party_create' is required if 'auto_create' is true.")

        if selected_region:
            try:
                my_region = yield hosts.find_region(selected_region)
            except RegionNotFound:
                raise HTTPError(3404, "No such region")
        else:
            ip = remote_ip(self.request)

            if ip is None:
                raise HTTPError(3400, "Bad IP")

            geo = geolite2.lookup(ip)

            if geo:
                p_lat, p_long = geo.location

                try:
                    my_region = yield hosts.get_closest_region(p_long, p_lat)
                except RegionNotFound:
                    pass

        if not my_region:
            try:
                my_region = yield hosts.get_default_region()
            except RegionNotFound:
                raise HTTPError(3410, "No default region defined")

        try:
            yield parties.join_party(
                gamespace, game_name, game_version, game_server_name,
                my_region.region_id, party_filter, account_id,
                member_profile, self.token.key,
                auto_create, party_settings, room_settings, max_members,
                create_flags=create_party_flags, create_close_callback=close_callback,
                create_room_filters=room_filters, session_callback=self._inited)
        except ValidationError as e:
            raise HTTPError(3400, e.message)
        except PartyError as e:
            logging.exception("Failed to open party session")
            raise HTTPError(3000 + e.code, e.message)
        except NoSuchParty:
            raise HTTPError(3404, "No such party")


    @coroutine
    def on_closed(self):
        if not self.session:
            return

        yield self.session.release()
        self.session = None


class PartySessionHandler(PartyHandler):

    def __init__(self, application, request, **kwargs):
        super(PartySessionHandler, self).__init__(application, request, **kwargs)

    def required_scopes(self):
        return ["party"]

    def check_origin(self, origin):
        return True

    @coroutine
    def on_opened(self, party_id, *ignored, **ignored_kw):

        parties = self.application.parties

        auto_join = self.get_argument("auto_join", "true") == "true"

        try:
            check_members = ujson.loads(self.get_argument("check_members", "null"))
        except (KeyError, ValueError):
            raise HTTPError(3400, "Corrupted member profile settings")

        if auto_join:
            try:
                member_profile = ujson.loads(self.get_argument("member_profile", "{}"))
            except (KeyError, ValueError):
                raise HTTPError(3400, "Corrupted member profile settings")
        else:
            member_profile = None

        gamespace = self.token.get(AccessToken.GAMESPACE)
        account_id = self.token.account

        try:
            yield parties.party_session(
                gamespace, party_id, account_id, self.token.key,
                member_profile=member_profile, check_members=check_members,
                auto_join=auto_join, session_callback=self._inited)
        except ValidationError as e:
            raise HTTPError(3400, e.message)
        except PartyError as e:
            logging.exception("Failed to open party session")
            raise HTTPError(3000 + e.code, e.message)
        except NoSuchParty:
            raise HTTPError(3404, "No such party")

    @coroutine
    def on_closed(self):
        if not self.session:
            return

        yield self.session.release()
        self.session = None


class SimplePartyHandler(AuthenticatedHandler):

    @scoped(scopes=["party"])
    @coroutine
    def get(self, party_id):
        parties = self.application.parties
        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            party = yield parties.get_party(gamespace, party_id)
        except NoSuchParty:
            raise HTTPError(404, "No such party")
        except PartyError as e:
            raise HTTPError(e.code, e.message)

        self.dumps({
            "party": party.dump()
        })

    @scoped(scopes=["party_close"])
    @coroutine
    def delete(self, party_id):
        parties = self.application.parties

        try:
            message = ujson.loads(self.get_argument("message", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted party settings")

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            result = yield parties.close_party(gamespace, party_id, message)
        except ValidationError as e:
            raise HTTPError(400, e.message)
        except PartyError as e:
            raise HTTPError(e.code, e.message)
        except NoSuchParty:
            raise HTTPError(404, "No such party")

        self.dumps(result or {})


class IssueBanHandler(AuthenticatedHandler):
    @scoped(scopes=["game_ban"])
    @coroutine
    def post(self):
        bans = self.application.bans

        account_id = self.get_argument("account")
        reason = self.get_argument("reason")
        expires = self.get_argument("expires")

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            ban_id = yield bans.new_ban(gamespace, account_id, expires, reason)
        except ValidationError as e:
            raise HTTPError(400, e.message)
        except BanError as e:
            raise HTTPError(500, e.message)
        except UserAlreadyBanned:
            raise HTTPError(406, "This user have already been banned")

        self.dumps({
            "id": ban_id
        })


class BanHandler(AuthenticatedHandler):
    @scoped(scopes=["game_ban"])
    @coroutine
    def get(self, ban_id):
        bans = self.application.bans

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            ban = yield bans.get_ban(gamespace, ban_id)
        except ValidationError as e:
            raise HTTPError(400, e.message)
        except BanError as e:
            raise HTTPError(409, e.message)
        except NoSuchBan:
            raise HTTPError(404, "No such ban")

        self.dumps({
            "ban": ban.dump()
        })

    @scoped(scopes=["game_ban"])
    @coroutine
    def post(self, ban_id):
        bans = self.application.bans

        gamespace = self.token.get(AccessToken.GAMESPACE)

        reason = self.get_argument("reason")
        expires = self.get_argument("expires")

        try:
            yield bans.update_ban(gamespace, ban_id, expires, reason)
        except ValidationError as e:
            raise HTTPError(400, e.message)
        except BanError as e:
            raise HTTPError(409, e.message)

    @scoped(scopes=["game_ban"])
    @coroutine
    def delete(self, ban_id):
        bans = self.application.bans

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            yield bans.delete_ban(gamespace, ban_id)
        except ValidationError as e:
            raise HTTPError(400, e.message)
        except BanError as e:
            raise HTTPError(409, e.message)


class FindBanHandler(AuthenticatedHandler):
    @scoped(scopes=["game_ban"])
    @coroutine
    def get(self, account_id):
        bans = self.application.bans

        gamespace = self.token.get(AccessToken.GAMESPACE)

        try:
            ban = yield bans.get_active_ban_by_account(gamespace, account_id)
        except ValidationError as e:
            raise HTTPError(400, e.message)
        except BanError as e:
            raise HTTPError(409, e.message)
        except NoSuchBan:
            raise HTTPError(404, "No such ban")

        self.dumps({
            "ban": ban.dump()
        })
