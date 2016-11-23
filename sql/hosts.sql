CREATE TABLE `hosts` (
  `host_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `host_name` varchar(128) NOT NULL,
  `internal_location` varchar(255) NOT NULL DEFAULT '',
  `geo_location` point NOT NULL,
  `host_default` tinyint(1) NOT NULL DEFAULT '0',
  `host_enabled` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`host_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;