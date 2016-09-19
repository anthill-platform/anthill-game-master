CREATE TABLE `games` (
  `record_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `game_id` varchar(64) NOT NULL DEFAULT '',
  `gamespace_id` int(11) NOT NULL,
  `server_host` varchar(255) NOT NULL DEFAULT '',
  `schema` json NOT NULL,
  `max_players` int(11) NOT NULL,
  `game_settings` json NOT NULL,
  `server_settings` json NOT NULL,
  PRIMARY KEY (`record_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;