
from tornado.gen import coroutine, Return, sleep

import tornado.ioloop
from room import RoomNotFound, RoomError
from game import GameVersionNotFound
import logging
from common import random_string


class Player(object):
    AUTO_REMOVE_TIME = 10
    LOCK_ACTIONS_TIME = 15

    def __init__(self, app, gamespace, game_id, game_version, account_id, access_token):
        self.app = app
        self.rooms = app.rooms
        self.games = app.games
        self.gamespace = gamespace
        self.game_id = game_id
        self.game_version = game_version
        self.account_id = str(account_id)

        self.settings = None
        self.game_settings = None

        self.server_settings = {}
        self.players = []
        self.room = None
        self.room_id = None
        self.record_id = None
        self.access_token = access_token

    def generate_key(self):
        return str(self.account_id) + "_" + random_string(60)

    @coroutine
    def init(self):
        self.settings = yield self.games.get_game_settings(self.gamespace, self.game_id)
        self.game_settings = self.settings.game_settings

        try:
            self.server_settings = yield self.games.get_game_version_server_settings(
                self.gamespace, self.game_id, self.game_version)
        except GameVersionNotFound as e:
            logging.info("Applied default config for version '{0}'".format(self.game_version))
            self.server_settings = self.settings.server_settings

            if not self.server_settings:
                raise PlayerError("No default version configuration")

    @coroutine
    def create(self, room_settings, key=None):

        if not isinstance(room_settings, dict):
            raise PlayerError("Settings is not a dict")

        room_settings = {
            key: value
            for key, value in room_settings.iteritems()
            if isinstance(value, (str, unicode, int, float, bool))
        }

        lock_status = yield self.app.ratelimit.limit("create_room", self.account_id)

        if lock_status:
            logging.debug("Allowed for a player: " + self.account_id)
        else:
            raise PlayerError("Too many requests")

        if not key:
            key = self.generate_key()

        self.record_id, self.room_id = yield self.rooms.create_and_join_room(
            self.gamespace, self.game_id, self.game_version,
            self.settings, room_settings, self.account_id,
            key, self.access_token
        )

        logging.info("Created a room: '{0}'".format(self.room_id))

        try:
            combined_settings = {
                "game": self.game_settings,
                "server": self.server_settings,
                "room": room_settings
            }

            result = yield self.rooms.spawn_server(
                self.gamespace, self.game_id, self.game_version,
                self.room_id, self.settings.server_host, combined_settings
            )
        except RoomError as e:
            # failed to spawn a server, then leave
            # this will likely to cause the room to be deleted
            yield self.leave(True)
            logging.exception("Failed to spawn a server")
            raise e

        location = result["location"]

        # call a joined coroutine in parallel
        tornado.ioloop.IOLoop.current().spawn_callback(self.joined)

        raise Return({
            "id": self.room_id,
            "slot": self.record_id,
            "location": location,
            "key": key
        })

    @coroutine
    def join(self, search_settings, auto_create=False, create_room_settings=None):
        """
        Joins a player to the first available room. Waits until the room is
        :param search_settings: filters to search the rooms
        :param auto_create: if no such room, create one
        :param create_room_settings: in case room auto creation is triggered, will be use to fill the new room's
               settings
        """

        key = self.generate_key()

        try:
            self.record_id, self.room = yield self.rooms.find_and_join_room(
                self.gamespace, self.game_id, self.game_version,
                self.account_id, key, self.access_token, search_settings)
            self.room_id = self.room["room_id"]

            location = self.room["location"]

        except RoomNotFound as e:
            if auto_create:
                logging.info("No rooms found, creating one")

                result = yield self.create(create_room_settings or {}, key=key)
                raise Return(result)

            else:
                raise e

        # call a joined coroutine in parallel
        tornado.ioloop.IOLoop.current().spawn_callback(self.joined)

        raise Return({
            "id": self.room_id,
            "slot": self.record_id,
            "location": location,
            "key": key
        })

    @coroutine
    def joined(self):
        """
        Called asynchronously when user joined the room
        Waits a while, and then leaves the room, if the join reservation
            was not approved by game-controller.
        """

        # wait a while
        yield sleep(Player.AUTO_REMOVE_TIME)

        # and then try to remove a player reservation
        if (self.record_id is None) or (self.room_id is None):
            return

        result = yield self.rooms.leave_room_reservation(self.gamespace, self.room_id, self.account_id)

        if result:
            logging.warning("Removed player reservation: room '{0}' player '{1}' gamespace '{2}'".format(
                self.room_id, self.account_id, self.gamespace
            ))

    @coroutine
    def leave(self, remove_room=False):
        if (self.record_id is None) or (self.room_id is None):
            return

        yield self.rooms.leave_room(self.gamespace, self.room_id, self.account_id, remove_room=remove_room)

        self.record_id = None
        self.room = None


class PlayerError(Exception):
    def __init__(self, message):
        self.message = message


class Room(object):
    def __init__(self, room):
        self.room_id = room["room_id"]
        self.room = room
