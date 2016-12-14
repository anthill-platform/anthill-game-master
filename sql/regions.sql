CREATE TABLE `regions` (
  `region_id` int(11) NOT NULL AUTO_INCREMENT,
  `region_name` varchar(255) NOT NULL DEFAULT '',
  `region_location` point NOT NULL,
  `region_default` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`region_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;