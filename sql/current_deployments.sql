CREATE TABLE `current_deployments` (
  `gamespace_id` int(11) NOT NULL,
  `game_name` varchar(64) NOT NULL DEFAULT '',
  `game_version` varchar(64) NOT NULL DEFAULT '',
  `current_deployment` int(11) NOT NULL,
  PRIMARY KEY (`gamespace_id`,`game_name`,`game_version`),
  UNIQUE KEY `gamespace_id` (`gamespace_id`,`game_name`,`game_version`),
  KEY `gamespace_id_2` (`gamespace_id`,`game_name`,`game_version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;