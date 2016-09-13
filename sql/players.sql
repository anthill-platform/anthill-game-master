CREATE TABLE `players` (
  `record_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(11) NOT NULL,
  `account_id` int(11) NOT NULL,
  `room_id` int(11) NOT NULL,
  `state` enum('RESERVED','JOINED') NOT NULL DEFAULT 'RESERVED',
  `joined_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `key` varchar(64) NOT NULL DEFAULT '',
  `access_token` mediumtext NOT NULL,
  PRIMARY KEY (`record_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;