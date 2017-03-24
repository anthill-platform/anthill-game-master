CREATE TABLE `players` (
  `record_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(11) NOT NULL,
  `account_id` int(11) NOT NULL,
  `room_id` int(11) NOT NULL,
  `host_id` int(11) NOT NULL,
  `state` enum('RESERVED','JOINED') NOT NULL DEFAULT 'RESERVED',
  `key` varchar(64) NOT NULL DEFAULT '',
  `access_token` mediumtext NOT NULL,
  PRIMARY KEY (`record_id`),
  KEY `room_id` (`room_id`),
  KEY `key` (`key`),
  KEY `account_id` (`account_id`),
  KEY `gamespace_id` (`gamespace_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;