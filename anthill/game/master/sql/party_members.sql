CREATE TABLE `party_members` (
  `account_id` int(11) unsigned NOT NULL,
  `gamespace_id` int(11) NOT NULL,
  `party_id` int(10) unsigned NOT NULL,
  `member_role` int(11) NOT NULL DEFAULT '0',
  `member_profile` json NOT NULL,
  `member_token` mediumtext NOT NULL,
  KEY `party_id` (`party_id`),
  KEY `account_id` (`account_id`,`gamespace_id`,`party_id`),
  CONSTRAINT `party_members_ibfk_1` FOREIGN KEY (`party_id`) REFERENCES `parties` (`party_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;