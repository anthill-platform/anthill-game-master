CREATE TABLE `deployments` (
  `deployment_id` int(11) NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(11) NOT NULL,
  `game_name` varchar(64) NOT NULL DEFAULT '',
  `game_version` varchar(64) NOT NULL DEFAULT '',
  `deployment_date` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `deployment_hash` varchar(256) NOT NULL DEFAULT '',
  `deployment_status` enum('delivering','uploading','uploaded','delivered','error','deleting','deleted') NOT NULL DEFAULT 'uploading',
  PRIMARY KEY (`deployment_id`)
) ENGINE=InnoDB AUTO_INCREMENT=32 DEFAULT CHARSET=utf8;