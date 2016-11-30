
from common.options import define

# Main

define("host",
       default="http://game-ctl-dev.anthill",
       help="Public hostname of this service",
       type=str)

define("gs_host",
       default="game-ctl-dev.anthill",
       help="Public hostname of this service for games (without protocol)",
       type=str)

define("listen",
       default="port:11600",
       help="Socket to listen. Could be a port number (port:N), or a unix domain socket (unix:PATH)",
       type=str)

define("name",
       default="game-ctl",
       help="Service short name. User to discover by discovery service.",
       type=str)

# Game servers

define("sock_path",
       default="/tmp",
       help="Location of the unix sockets game servers communicate with.",
       type=str)

define("binaries_path",
       default="/opt/local/gs",
       help="Location of game server binaries.",
       type=str)

define("ports_pool_from",
       default=38000,
       help="Port range start (for game servers)",
       type=int)

define("ports_pool_to",
       default=40000,
       help="Port range end (for game servers)",
       type=int)
