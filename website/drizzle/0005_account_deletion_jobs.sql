CREATE TABLE `account_deletion_jobs` (
  `account_id` text PRIMARY KEY NOT NULL,
  `account_email` text NOT NULL,
  `attempts` integer NOT NULL DEFAULT 0,
  `last_error` text,
  `created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX `account_deletion_jobs_updated_idx`
  ON `account_deletion_jobs` (`updated_at`);
