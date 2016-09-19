CREATE TABLE `game_versions` (
  `record_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `game_id` varchar(64) NOT NULL DEFAULT '',
  `game_version` varchar(64) NOT NULL DEFAULT '',
  `gamespace_id` int(11) NOT NULL,
  `server_settings` json NOT NULL,
  PRIMARY KEY (`record_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
