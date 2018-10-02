CREATE TABLE `game_servers` (
  `game_server_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(11) NOT NULL,
  `game_name` varchar(64) NOT NULL,
  `game_server_name` varchar(255) NOT NULL DEFAULT '',
  `schema` json NOT NULL,
  `max_players` int(11) NOT NULL,
  `game_settings` json NOT NULL,
  `server_settings` json NOT NULL,
  PRIMARY KEY (`game_server_id`),
  UNIQUE KEY `gamespace_id` (`gamespace_id`,`game_name`,`game_server_name`),
  KEY `game_server_name` (`game_server_name`),
  KEY `game_name` (`game_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;