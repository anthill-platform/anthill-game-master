CREATE TABLE `deployment_deliveries` (
  `delivery_id` int(11) NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(11) NOT NULL,
  `deployment_id` int(11) NOT NULL,
  `host_id` int(11) NOT NULL,
  `delivery_status` enum('delivering','delivered','error','deleting','deleted') NOT NULL DEFAULT 'delivering',
  `error_reason` varchar(256) NOT NULL DEFAULT '',
  PRIMARY KEY (`delivery_id`),
  UNIQUE KEY `gamespace_id` (`gamespace_id`,`deployment_id`,`host_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;