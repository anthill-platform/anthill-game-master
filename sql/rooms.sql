CREATE TABLE `rooms` (
  `room_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(11) unsigned NOT NULL,
  `game_id` varchar(64) NOT NULL DEFAULT '',
  `game_version` varchar(64) NOT NULL,
  `players` int(11) unsigned NOT NULL DEFAULT '0',
  `max_players` int(11) unsigned NOT NULL DEFAULT '0',
  `settings` json NOT NULL,
  `location` json NOT NULL,
  `state` enum('NONE','SPAWNED') NOT NULL DEFAULT 'NONE',
  PRIMARY KEY (`room_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;