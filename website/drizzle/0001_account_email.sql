ALTER TABLE `subscriptions` ADD `account_email` text;
--> statement-breakpoint
CREATE UNIQUE INDEX `subscriptions_account_email_idx`
ON `subscriptions` (`account_email`);
