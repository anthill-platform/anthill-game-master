CREATE TABLE `game_deployments` (
  `gamespace_id` int(11) NOT NULL,
  `game_name` varchar(64) NOT NULL DEFAULT '',
  `game_version` varchar(64) NOT NULL DEFAULT '',
  `current_deployment` int(11) NOT NULL,
  `deployment_enabled` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`gamespace_id`,`game_name`,`game_version`),
  UNIQUE KEY `gamespace_id` (`gamespace_id`,`game_name`,`game_version`),
  KEY `gamespace_id_2` (`gamespace_id`,`game_name`,`game_version`),
  KEY `current_deployment` (`current_deployment`),
  CONSTRAINT `game_deployments_ibfk_1` FOREIGN KEY (`current_deployment`) REFERENCES `deployments` (`deployment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;