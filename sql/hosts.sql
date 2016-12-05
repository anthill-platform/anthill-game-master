CREATE TABLE `hosts` (
  `host_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `host_name` varchar(128) NOT NULL,
  `host_region` int(11) NOT NULL,
  `internal_location` varchar(255) NOT NULL DEFAULT '',
  `geo_location` point NOT NULL,
  `host_default` tinyint(1) NOT NULL DEFAULT '0',
  `host_enabled` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`host_id`),
  KEY `host_region` (`host_region`),
  CONSTRAINT `hosts_ibfk_1` FOREIGN KEY (`host_region`) REFERENCES `regions` (`region_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;