CREATE TABLE `bans` (
  `ban_id` int(11) NOT NULL AUTO_INCREMENT,
  `ban_gamespace` int(11) NOT NULL,
  `ban_account` int(11) NOT NULL,
  `ban_reason` varchar(255) NOT NULL,
  `ban_ip` varchar(64) DEFAULT NULL,
  `ban_expires` datetime NOT NULL,
  PRIMARY KEY (`ban_id`),
  KEY `ban_account` (`ban_account`),
  KEY `ban_ip` (`ban_ip`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;