
from psutil import virtual_memory, cpu_percent
from common.model import Model


class HeartbeatModel(Model):
    def __init__(self, app):
        self.app = app

    def __rooms_report__(self):
        rooms = self.app.rooms
        return [room_id for room_id, room in rooms.list()]

    def report(self):
        m = virtual_memory()
        memory_load = int((1.0 - float(m.free) / float(m.total)) * 100)
        cpu_load = int(cpu_percent())

        rooms = self.__rooms_report__()

        return {
            "load": {
                "memory": memory_load,
                "cpu": cpu_load
            },
            "rooms": rooms
        }
