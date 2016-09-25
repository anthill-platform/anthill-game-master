CREATE TABLE `game_versions` (
  `record_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(11) NOT NULL,
  `game_name` varchar(64) NOT NULL DEFAULT '',
  `game_version` varchar(64) NOT NULL DEFAULT '',
  `game_server_id` int(10) unsigned NOT NULL,
  `server_settings` json NOT NULL,
  PRIMARY KEY (`record_id`),
  UNIQUE KEY `game_name` (`game_name`,`game_version`,`game_server_id`,`gamespace_id`),
  KEY `game_server_id` (`game_server_id`),
  CONSTRAINT `game_versions_ibfk_1` FOREIGN KEY (`game_server_id`) REFERENCES `game_servers` (`game_server_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;