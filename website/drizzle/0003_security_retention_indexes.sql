CREATE INDEX `support_created_at_idx`
ON `support_tickets` (`created_at`);
--> statement-breakpoint
CREATE INDEX `license_validation_bucket_idx`
ON `license_validation_limits` (`minute_bucket`);
