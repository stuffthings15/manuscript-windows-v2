CREATE TABLE `license_validation_limits` (
  `request_fingerprint` text NOT NULL,
  `minute_bucket` integer NOT NULL,
  `attempts` integer DEFAULT 1 NOT NULL,
  PRIMARY KEY(`request_fingerprint`, `minute_bucket`)
);
