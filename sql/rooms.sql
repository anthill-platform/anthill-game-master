CREATE TABLE `rooms` (
  `room_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(11) unsigned NOT NULL,
  `game_name` varchar(64) NOT NULL DEFAULT '',
  `game_version` varchar(64) NOT NULL,
  `game_server_id` int(11) unsigned NOT NULL,
  `players` int(11) unsigned NOT NULL DEFAULT '0',
  `max_players` int(11) unsigned NOT NULL DEFAULT '0',
  `settings` json NOT NULL,
  `location` json NOT NULL,
  `state` enum('NONE','SPAWNED') NOT NULL DEFAULT 'NONE',
  `host_id` int(11) unsigned NOT NULL,
  PRIMARY KEY (`room_id`),
  KEY `game_server_id` (`game_server_id`),
  KEY `host_id` (`host_id`),
  CONSTRAINT `rooms_ibfk_1` FOREIGN KEY (`host_id`) REFERENCES `hosts` (`host_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;