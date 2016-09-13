CREATE TABLE `servers` (
  `server_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `internal_location` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`server_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;