
from anthill.common.model import Model
from anthill.common.server import Server
from anthill.common.rabbitrpc import RabbitMQJsonRPC


class GameControllerJsonRPC(RabbitMQJsonRPC):
    def __init__(self, internal_name, broker, max_connections, channel_prefetch_count):
        super().__init__()
        self.internal_name = internal_name
        self.broker = broker
        self.max_connections = max_connections
        self.channel_prefetch_count = channel_prefetch_count

    async def listen(self, on_receive, timeout=300):
        return await self.listen_broker(self.broker, self.internal_name, on_receive, timeout)

    async def __get_context__(self, service):
        connection = await self.__get_connection__(
            self.broker,
            max_connections=self.max_connections,
            connection_name="request_{0}".format(service),
            channel_prefetch_count=self.channel_prefetch_count)

        context = await connection.__declare_queue__(service, on_return_callback=self.__on_return__)
        return context


class GameControllerRPC(Model, RabbitMQJsonRPC):
    def __init__(self, app, broker, max_connections, channel_prefetch_count):
        super().__init__()
        self.app = app
        self.broker = broker
        self.max_connections = max_connections
        self.channel_prefetch_count = channel_prefetch_count
        self.rpcs = {}

    def acquire_rpc(self, name):
        rpc = self.rpcs.get(name)
        if rpc is None:
            rpc = GameControllerJsonRPC(name, self.broker, self.max_connections, self.channel_prefetch_count)
            self.rpcs[name] = rpc
        return rpc
