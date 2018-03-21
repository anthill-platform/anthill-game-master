# Wha The Party Is

Party is an group of members (potentially players) that can be created without actually instantiating a <a href="https://github.com/anthill-platform/anthill-game-master#concepts">Game Server</a>. 

In certain cases, partying players together is even required before the actual Game Server being started up:
  
* You want to start the Game Server only when full room of people is matched;
* Players want to discuss the future map / game mode before starting the game;
* Players want to join a random game with their friends.

If you would like to see the REST API for the parties, see <a href="#party-rest-api">Party REST API</a> section below.

# Party Flow

Each party has several "states". The final goal of the party is to find or create a Game Server, then destroy itself.

1. Party is created either by some player or by a service;
2. Members open a <a href="#party-session">Party Session</a> upon it, using it the members can join into the party, send messages to eachother etc.
3. Either by autometic trigger, or manually by some member, the party can be "started", meaning an actuall Game Server is instantiated, with appropriate party settings;
4. The memberss are relocated into the Game Server and become players by receving a notification from the Party Session;
5. The party itself is destroyed.

# Party Properties

Each party has a set of properties:

| Name             | Description          |
|------------------|----------------------|
| `party_settings` | Abstract JSON object of party-related settings, completely defined by the game. Parties can be found using these settings (see `party_filter` argument on some requests below) |
| `room_setting`   | Abstract JSON object of the actual room settings that will be applied to the Game Server once the party starts (if the `room_filter` below is defined, and an existing room has been found, this field is ignored) |
| `room_filter`    | (Optional) If defined the party will search for appropriate room first upon party startup, instead of creating a new one. |

Additional properties:

| Name             | Description          |
|------------------|----------------------|
| `max_members`    | (Optional) Maximum number of party members, default is `8` |
| `auto_start`     | (Optional) If `true` (default), the party will automatically start once the party gets full (reaching `max_members` number of members). If `false`, nothing will happen.
| `auto_close`     | (Optional) If `true` (default), the party will be destroyed automatically once the last member leaves. If `false`, the empty party will remain.
| `region`         | (Optional) A <a href="https://github.com/anthill-platform/anthill-game-master#concepts">Region</a> to start the Game Server on, default is picked automatically upon party creator's IP. |
| `close_callback` | (Optional) If defined, a callback function of the <a href="https://github.com/anthill-platform/anthill-exec">Exec Service</a> with that name that will be called once the party is closed (see `Server Code`). Please note that this function should allow calling (`allow_call = true`)

# Member Properties

Besides the actual party, each member in it can have his unique properties:

| Name             | Description          |
|------------------|----------------------|
| `member_profile` | A small JSON Object to represent the member. For example, that might be a desired color, or avatar URL of caching. This object is passed to the Game Server, so it can be used by it.  |
| `member_role`    | A number, defining how much power the member has within the party`. This number is also passed to the Game Server. |

Roles are as follows:

* At least `500` role is required to start the party manually.
* At least `1000` role is required to close the party manually.
* The creator of the party gets `1000` role.
* The regular member of the party gets `0` role.
* As of currently, there is no way to change roles, so only the creator of the party can start is manually or force party closure.

# Party Session

Party Session is a Web Socket session that allows members to have real-time communication within a party.

The actual communication is made within <a href="http://www.jsonrpc.org/specification">JSON-RPC 2.0</a> protocol. In short, a JSON-RPC protocol allows two nodes to send each other requests, end receive responses 
(in form of JSON objects):

```
Current Party Member -> { request JSON object } -> Game Service
Current Party Member  <- { response JSON object } <- Game Service
```

### Party Session Joining

The member can either join the party, or not. In both cases the connection can still remain. `max_members` only applies to joined members, so there can be more connected sessions to a party than a maximum members capacity.

Party members can be "not joined" into the party and still send and receive messages. That make the whole `join` functionality to be more like `ready`.

### Session Methods

A party member can call these methods to communicate with a party.

<details>
<summary>Example of the JSON-RPC Request</summary><p>

Request Object:

```json
{
    "jsonrpc": "2.0", 
    "method": "send_message", 
    "params": {
        "payload": {
            "text": "hello"
        }
    }, 
    "id": 1
}
```

Response Object:

```
{
    "jsonrpc": "2.0", 
    "result": "OK", 
    "id": 1
}
```

</p></details>


* `send_message(payload)` – to send any message object (defined with argument `payload`) to all other members of the session. 
  
  This could be used for chat or in-game requests etc

* `close_party(message)` – to close the current party. 

  `message` argument defines any object that would be delivered to other party members upon closing the party. 
  
  Please note that party member needs to have at least `1000` role to close a party.

* `leave_party` – to leave the current party. 

  As the connection still open, the member will still receive any in-party members, but if the party starts, the members who left the party won't be transferred to a Game Server.

* `join_party(member_profile, check_members)` – to join the party back. 

  This can be done automatically upon session creation. 
  
  `member_profile` – see <a href="#member-properties">Member Properties</a>. 
  
  `check_members` – optional Profile Object that may be used to theck ALL of the members for certain condition, or the join will fail.
  
  <details>
  <summary>Example</summary>
  
  This complex function will ensure that no more 2 members in the party, that have field `clan-name` of their `member_profile` equal to `TEST_CLAN`, meaning there could be only two members total from clan `TEST_CLAN`.
  
  ```json
  	{
	    "members": {
	        "@func": "<",
	        "@cond": 2,
	        "@value": {
	            "@func": "num_child_where",
	            "@test": "==",
	            "@field": "clan-name",
	            "@value": "TEST_CLAN"
	        }
	    }
	}
  ```
  
  </details>
  
* `start_game(message)` – to manually start the game.

  `message` argument defines any object that would be delivered to other party members upon starting the game. 
  
  Please note that party member needs to have at least `500` role to start the game manually.

### Session Callbacks

The party session may call some reqests methods too, meaning a Game Service initiates conversation.

```
Game Service -> { request JSON object } -> Current Party Member
Game Service <- { response JSON object } <- Current Party Member
```

* `message(message_type, payload)` – some message has been received by a party member

  <details>
  <summary>Example of the JSON-RPC Request</summary><p>
	
	```json
	{
	    "jsonrpc": "2.0", 
	    "method": "message", 
	    "params": {
	        "message_type": "custom",
	        "payload": {
	            "text": "hello"
	        }
	    }, 
	    "id": 1
	}
	```

  </p></details>

  `message_type` is a type of message, the `payload` depends on the `message_type`
  
  | Message Type | Description | Payload |
  |--------------|-------------|---------|
  | `player_joined` | A new member has joined the party. | A JSON Object with fields: `account` – an account ID of the member, `profile` – a `member_profile` of the member |
  | `player_left` | A member has left the party. | A JSON Object with fields: `account` – an account ID of the member, `profile` – a `member_profile` of the member |
  | `game_starting` | The game is about to start as a Game Server is being instantiated | As described in `start_game` request  |
  | `game_start_failed` | Somehow the Game Server instantiation has failed | A JSON Object with fields: `reason`, `code` |
  | `game_started` | A game has successfully started, now the party is about to be closed. The client has now connect to the Game Server as described <a href="https://github.com/anthill-platform/anthill-game-master/blob/master/doc/API.md#2-connecting">here</a> | A JSON Object with fields: `id` – room ID, `slot` – current player's slot in this room, `key` – a room secret key, `location` – a location of the instantiated Game Server, `settings` – newly created room's settings |
  | `custom` | A custom message, being sent by `send_message` | As described in `send_message`  |
  | `party_closed` | The party is being closed, expect the WebSocket communication to be closed as well. | As described in `close_party ` |

Please see for REST API methods marked with <img width="16" src="https://user-images.githubusercontent.com/1666014/32136810-72ccb840-bc1d-11e7-9934-7bc7fbc59913.png"> to know how to open a Party Session.

# Identifying A Party

A Game Server can detect if it's being launched in a party context with environment variables.

* `party:id` is such environment variable exists, then the Game Server is started in party context, and the variable contains id of the party. Please note this can be used for references only as the actual party may be destroyed at that point.
* `party:settings` a `party_settings` from <a href="#party-properties">Party Properties</a>.
* `party:members` a JSON object with initial party members list in following format:

   ```
   {
      "<account-id>": {
         "profile": <member-profile>,
         "role": <member-role>
      }
   }
   ```
   
   Please note that this list is not exslusionary as players can connect from another parties later (see below)
   
### Late connection

In some cases, party members can join the Game Server way after creation of it. For example, if `room_filter` is defined inside the party, the existing Game Server will be searched before creating a new one. In that case the party members may connect to existing Game Server that was spawned by another party (or without any party at all).

To deal with this, a Game Server can identify a party member by parsing the `info` object of the `joined` controller request response. The `info` object may contain these fields: `party:id`, `party:profile`, `party:role`, their definitions are described above.

See <a href="API.md#2-connecting">Game Controller Connecting Flow</a> for the information about the `joined` request.

# Party REST API

This section describes various calls you can do to interact with the parties.

## Create Party

Creates a fresh new party and returns its information. Please note this request does not open <a href="#party-session">Party Session</a>.

#### ← Request

```rest
POST /party/create/<game_name>/<game_version>/<game_server_name>
```

| Argument         | Description                                                                                    |
|------------------|--------------------------------|
| `game_name` | Name of the game |
| `game_version ` | Version of the game |
| `game_server_name ` | Game-related preset of the server, must be defined as with usual Game Server instantiation  |
| `party_settings` | See <a href="#party-properties">Party Properties</a> |
| `room_settings`  | See <a href="#party-properties">Party Properties</a> |
| `max_members` | See <a href="#party-properties">Party Properties</a> |
| `region` | See <a href="#party-properties">Party Properties</a> |
| `auto_start` | See <a href="#party-properties">Party Properties</a> |
| `auto_close` | See <a href="#party-properties">Party Properties</a> |
| `close_callback` | See <a href="#party-properties">Party Properties</a> |

Access scope `party_create` is required for this request.

#### → Response

In case of success, a JSON object with party information is returned:

```
{
   "party": {
      "id": "<party-id>",
      "num_members": <number-of-members>,
      "max_memvers": <meximum-numver-of-members>,
      "settings": { ... }
   }
}
```

| Response         | Description                                          |
|------------------|------------------------------------------------------|
| `200 OK`         | Everything went OK, room information follows.        |
| `400 Bad Arguments` | Some arguments are missing or wrong.              |


## Get Party Information

Returns party information.

#### ← Request

```rest
GET /party/<party-id>
```

| Argument         | Description                                                                                    |
|------------------|--------------------------------|
| `party-id` | Id of the party in question |

Access scope `party` is required for this request.

#### → Response

In case of success, a JSON object with party information is returned:

```
{
   "party": {
      "id": "<party-id>",
      "num_members": <number-of-members>,
      "max_memvers": <meximum-numver-of-members>,
      "settings": { ... }
   }
}
```

| Response         | Description                                          |
|------------------|------------------------------------------------------|
| `200 OK`         | Everything went OK, room information follows.        |
| `400 Bad Arguments` | Some arguments are missing or wrong.              |


## Close Party

Closes existing party. The called does not have to be the creator of the party, but scope `party_close` is required.

#### ← Request

```rest
DELETE /party/<party-id>
```

| Argument         | Description                                                                                    |
|------------------|--------------------------------|
| `party-id` | Id of the party in question |

Access scope `party_close ` is required for this request.

#### → Response

If the party had `close_callback` defined, a result of execution of such callback will be returned. Otherwise, and empty `{}` is returned.


| Response         | Description                                          |
|------------------|------------------------------------------------------|
| `200 OK`         | Everything went OK, room information follows.        |
| `400 Bad Arguments` | Some arguments are missing or wrong.              |


## <img width="16" src="https://user-images.githubusercontent.com/1666014/32136810-72ccb840-bc1d-11e7-9934-7bc7fbc59913.png"> Create Party And Open Session

Creates a fresh new party and opens a <a href="#party-session">Party Session</a> on it.

#### ← Web Socket Request

Please note that this request is a Web Socket request, meaning that `HTTP` session will be upgraded to a Web Socket session.

```rest
WEB SOCKET /party/create/<game_name>/<game_version>/<game_server_name>/session
```

| Argument         | Description                                                                                    |
|------------------|--------------------------------|
| `game_name` | Name of the game |
| `game_version ` | Version of the game |
| `game_server_name ` | Game-related preset of the server, must be defined as with usual Game Server instantiation  |

Additional query artuments:

| Query Argument   | Description                    |
|------------------|--------------------------------|
| `party_settings` | See <a href="#party-properties">Party Properties</a> |
| `room_settings`  | See <a href="#party-properties">Party Properties</a> |
| `max_members` | See <a href="#party-properties">Party Properties</a> |
| `region` | See <a href="#party-properties">Party Properties</a> |
| `auto_start` | See <a href="#party-properties">Party Properties</a> |
| `auto_close` | See <a href="#party-properties">Party Properties</a> |
| `close_callback` | See <a href="#party-properties">Party Properties</a> |
| `auto_join ` | If `true` (default), the current memmber will be joined to a new session automatically. |
| `member_profile ` | If `auto_join` is `true`, this would be used to define member's profile. See <a href="#member-properties">Member Properties</a> |

Access scope `party_create` is required for this request.


## <img width="16" src="https://user-images.githubusercontent.com/1666014/32136810-72ccb840-bc1d-11e7-9934-7bc7fbc59913.png"> Connect To Existing Party

Connects to exisint party and opens a <a href="#party-session">Party Session</a> on it.

#### ← Web Socket Request

Please note that this request is a Web Socket request, meaning that `HTTP` session will be upgraded to a Web Socket session.

```rest
WEB SOCKET /party/<party_id>/session
```

| Argument         | Description                                                                                    |
|------------------|--------------------------------|
| `party_id ` | Id of the party in question |

Additional query artuments:

| Query Argument   | Description                    |
|------------------|--------------------------------|
| `auto_join ` | If `true` (default), the current memmber will be joined to a new session automatically. |
| `member_profile ` | If `auto_join` is `true`, this would be used to define member's profile. See <a href="#member-properties">Member Properties</a> |
| `check_members ` | If `auto_join` is `true`, this Profile Object may be used to theck ALL of the members for certain condition, or the automatic join will fail. |

Access scope `party` is required for this request.

## <img width="16" src="https://user-images.githubusercontent.com/1666014/32136810-72ccb840-bc1d-11e7-9934-7bc7fbc59913.png"> Find A Party And Open Session

Find a party (possibly creates a new one) and opens a <a href="#party-session">Party Session</a> on it.

#### ← Web Socket Request

Please note that this request is a Web Socket request, meaning that `HTTP` session will be upgraded to a Web Socket session.

```rest
WEB SOCKET /parties/<game_name>/<game_version>/<game_server_name>/session
```

| Argument         | Description                                                                                    |
|------------------|--------------------------------|
| `game_name` | Name of the game |
| `game_version ` | Version of the game |
| `game_server_name ` | Game-related preset of the server, must be defined as with usual Game Server instantiation  |

Additional query artuments:

| Query Argument   | Description                    |
|------------------|--------------------------------|
| `party_filter` | A filter to search the parties for. This argument is required. |
| `auto_create` | To automatically create a new party if there's no party that satisfies `party_filter`. Please note that if `auto_create` is `true`, access scope `party_create` is required. |
| `member_profile ` | Member's profile. See <a href="#member-properties">Member Properties</a> |

If `auto_create` is `true`, these arguments are expected:

| Query Argument   | Description                    |
|------------------|--------------------------------|
| `create_party_settings` | `party_settings` in  <a href="#party-properties">Party Properties</a> |
| `create_room_settings`  | `room_settings` in <a href="#party-properties">Party Properties</a> |
| `create_room_filters`  | `room_filters` in <a href="#party-properties">Party Properties</a> |
| `max_members` | See <a href="#party-properties">Party Properties</a> |
| `region` | See <a href="#party-properties">Party Properties</a> |
| `create_auto_start` | `auto_start` in <a href="#party-properties">Party Properties</a> |
| `create_auto_close` | `auto_close` in <a href="#party-properties">Party Properties</a> |
| `create_close_callback` | `close_callback` in <a href="#party-properties">Party Properties</a> |

The `auto_join` cannot be defined in this argumend as it will always do automatically join.

Access scope `party` is required for this request.
