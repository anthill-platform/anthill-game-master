
from anthill.common.options import define
import os

# Main

define("host",
       default="http://localhost:9508",
       help="Public hostname of this service",
       type=str)

define("listen",
       default="port:9508",
       help="Socket to listen. Could be a port number (port:N), or a unix domain socket (unix:PATH)",
       type=str)

define("name",
       default="game",
       help="Service short name. User to discover by discovery service.",
       type=str)

# MySQL database

define("db_host",
       default="127.0.0.1",
       type=str,
       help="MySQL database location")

define("db_username",
       default="root",
       type=str,
       help="MySQL account username")

define("db_password",
       default="",
       type=str,
       help="MySQL account password")

define("db_name",
       default="dev_game",
       type=str,
       help="MySQL database name")

# Regular cache

define("cache_host",
       default="127.0.0.1",
       help="Location of a regular cache (redis).",
       group="cache",
       type=str)

define("cache_port",
       default=6379,
       help="Port of regular cache (redis).",
       group="cache",
       type=int)

define("cache_db",
       default=4,
       help="Database of regular cache (redis).",
       group="cache",
       type=int)

define("cache_max_connections",
       default=500,
       help="Maximum connections to the regular cache (connection pool).",
       group="cache",
       type=int)

# Rate limit cache

define("rate_cache_host",
       default="127.0.0.1",
       help="Location of a regular cache (redis).",
       group="cache",
       type=str)

define("rate_cache_port",
       default=6379,
       help="Port of regular cache (redis).",
       group="cache",
       type=int)

define("rate_cache_db",
       default=4,
       help="Database of regular cache (redis).",
       group="cache",
       type=int)

define("rate_cache_max_connections",
       default=500,
       help="Maximum connections to the regular cache (connection pool).",
       group="cache",
       type=int)

# Keys

define("rate_create_room",
       default="5,60",
       help="A limit for room creation for user tuple: (amount, time)",
       type=str)

# Deployments

if os.name == "nt":
    # Windows
    define("deployments_location",
           default="C:/Anthill/game-master-deployments",
           help="Path where all deployments are stored. If you have several instances of Game Master service, "
                "make sure this location is accessible from all instances (e.g. on a nfs).",
           type=str)
else:
    # Unix
    define("deployments_location",
           default="/usr/local/anthill/game-master-deployments",
           help="Path where all deployments are stored. If you have several instances of Game Master service, "
                "make sure this location is accessible from all instances (e.g. on a nfs).",
           type=str)

# Rabbitmq

define("party_broker",
       default="amqp://guest:guest@127.0.0.1:5672/",
       help="RabbitMQ broker location for party messaging (amqp).",
       group="message",
       type=str)
