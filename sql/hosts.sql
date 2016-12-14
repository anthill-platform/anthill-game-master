CREATE TABLE `hosts` (
  `host_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `host_name` varchar(128) NOT NULL,
  `host_region` int(11) NOT NULL,
  `internal_location` varchar(255) NOT NULL DEFAULT '',
  `host_enabled` tinyint(1) NOT NULL DEFAULT '0',
  `host_heartbeat` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `host_load` float NOT NULL DEFAULT '0.5',
  `host_memory` float NOT NULL DEFAULT '0',
  `host_cpu` float NOT NULL DEFAULT '0',
  `host_processing` tinyint(1) NOT NULL DEFAULT '0',
  `host_state` enum('ACTIVE','ERROR') NOT NULL DEFAULT 'ERROR',
  PRIMARY KEY (`host_id`),
  KEY `host_region` (`host_region`),
  CONSTRAINT `hosts_ibfk_1` FOREIGN KEY (`host_region`) REFERENCES `regions` (`region_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;