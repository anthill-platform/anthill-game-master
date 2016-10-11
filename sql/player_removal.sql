CREATE TRIGGER `player_removal`
AFTER DELETE ON `players`
FOR EACH ROW
  UPDATE `rooms` r
  SET `players`=(
    SELECT COUNT(*)
    FROM `players` p
    WHERE p.room_id = r.room_id
  )
  WHERE `room_id`=OLD.`room_id`;
