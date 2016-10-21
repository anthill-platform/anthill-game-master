
from tornado.gen import coroutine, Return

import logging
from common.internal import Internal, InternalError


class NotifyError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message


class Room(object):
    def __init__(self, rooms, gamespace, room_id, settings):
        self.rooms = rooms
        self.slots = {}
        self.gamespace = gamespace
        self.settings = settings
        self.room_id = room_id
        self.internal = Internal()

        logging.info("New room created: " + str(room_id))

    def game_settings(self):
        return self.settings["game"]

    def id(self):
        return self.room_id

    @coroutine
    def notify(self, method, *args, **kwargs):
        """
        Notify the master server about actions, happened in the room
        """
        try:
            result = yield self.internal.request(
                "game", "controller_action",
                room_id=self.id(),
                action=method,
                gamespace=self.gamespace,
                args=args,
                kwargs=kwargs)

        except InternalError as e:
            logging.error("Failed to notify an action: " + str(e.code) + ": " + e.body)

            raise NotifyError(e.code, e.message)
        else:
            # if there's a method with such action name, call it
            if (not method.startswith("_")) and hasattr(self, method):
                yield getattr(self, method)(result, *args, **kwargs)

            raise Return(result)

    @coroutine
    def update_settings(self, result, settings, *args, **kwargs):
        self.room_settings().update(settings)

    def room_settings(self):
        return self.settings["room"]

    @coroutine
    def stopped(self, result, *args, **kwargs):
        self.rooms.delete(self.room_id)

    def server_settings(self):
        return self.settings["server"]


class RoomSlot(object):
    pass


class RoomsData(object):
    def __init__(self, application):
        self.rooms = {}
        self.application = application

    def delete(self, room_id):
        if room_id in self.rooms:
            logging.info("Room deleted: " + str(room_id))
            del self.rooms[room_id]

    def new(self, gamespace, room_id, settings):
        logging.info("New room: " + str(room_id))

        room = Room(self, gamespace, room_id, settings)
        self.rooms[room_id] = room
        return room

