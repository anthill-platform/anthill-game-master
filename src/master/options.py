
from common.options import define

# Main

define("host",
       default="http://game-dev.anthill.local",
       help="Public hostname of this service",
       type=str)

define("listen",
       default="port:11500",
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
       default="anthill",
       type=str,
       help="MySQL account username")

define("db_password",
       default="",
       type=str,
       help="MySQL account password")

define("db_name",
       default="game",
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
       default=7,
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
       default=7,
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
       default=(5, 60),
       help="A limit for room creation for user tuple: (amount, time)",
       type=tuple)

# Deployments

define("deployments_location",
       default="/opt/local/deployments",
       help="A limit for room creation for user tuple: (amount, time)",
       type=str)
