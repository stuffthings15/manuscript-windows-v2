ALTER TABLE `subscriptions` ADD `account_id` text;
CREATE UNIQUE INDEX `subscriptions_account_id_idx`
  ON `subscriptions` (`account_id`);
