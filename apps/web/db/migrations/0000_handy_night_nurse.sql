CREATE TABLE `agent_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`session_id` text NOT NULL,
	`task_id` text,
	`agent` text NOT NULL,
	`model_id` text NOT NULL,
	`status` text NOT NULL,
	`prompt_hash` text,
	`prompt_chars` integer,
	`tokens_in` integer,
	`tokens_out` integer,
	`tokens_cached` integer,
	`cost_usd` real,
	`duration_ms` integer,
	`error` text,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`session_id`) REFERENCES `research_sessions`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "ck_agent_runs_agent" CHECK("agent" IN ('research_manager', 'crypto_research', 'stock_research', 'web_research', 'fact_checker', 'report_writer'))
);
--> statement-breakpoint
CREATE INDEX `idx_agent_runs_session` ON `agent_runs` (`session_id`);--> statement-breakpoint
CREATE INDEX `idx_agent_runs_agent` ON `agent_runs` (`agent`);--> statement-breakpoint
CREATE TABLE `claim_sources` (
	`claim_id` text NOT NULL,
	`source_id` text NOT NULL,
	PRIMARY KEY(`claim_id`, `source_id`),
	FOREIGN KEY (`claim_id`) REFERENCES `claims`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`source_id`) REFERENCES `sources`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `idx_claim_sources_source` ON `claim_sources` (`source_id`);--> statement-breakpoint
CREATE TABLE `claims` (
	`id` text PRIMARY KEY NOT NULL,
	`session_id` text NOT NULL,
	`task_id` text,
	`agent` text,
	`text` text NOT NULL,
	`epistemic_type` text NOT NULL,
	`confidence` text NOT NULL,
	`as_of` integer,
	`verification` text DEFAULT 'unverified' NOT NULL,
	`verification_note` text,
	`citation_index` integer,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`session_id`) REFERENCES `research_sessions`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "ck_claims_epistemic" CHECK("epistemic_type" IN ('fact', 'source_backed_fact', 'analysis', 'inference', 'prediction', 'opinion')),
	CONSTRAINT "ck_claims_confidence" CHECK("confidence" IN ('high', 'medium', 'low')),
	CONSTRAINT "ck_claims_verification" CHECK("verification" IN ('unverified', 'verified', 'conflicting', 'unsupported', 'refuted'))
);
--> statement-breakpoint
CREATE INDEX `idx_claims_session` ON `claims` (`session_id`);--> statement-breakpoint
CREATE INDEX `idx_claims_epistemic` ON `claims` (`session_id`,`epistemic_type`);--> statement-breakpoint
CREATE TABLE `research_events` (
	`id` text PRIMARY KEY NOT NULL,
	`session_id` text NOT NULL,
	`seq` integer NOT NULL,
	`type` text NOT NULL,
	`agent` text,
	`tool` text,
	`task_id` text,
	`message` text,
	`payload` text,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`session_id`) REFERENCES `research_sessions`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uq_events_session_seq` ON `research_events` (`session_id`,`seq`);--> statement-breakpoint
CREATE INDEX `idx_events_session_type` ON `research_events` (`session_id`,`type`);--> statement-breakpoint
CREATE TABLE `research_reports` (
	`id` text PRIMARY KEY NOT NULL,
	`session_id` text NOT NULL,
	`title` text NOT NULL,
	`executive_summary` text,
	`sections` text,
	`markdown` text NOT NULL,
	`metadata` text,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`session_id`) REFERENCES `research_sessions`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uq_reports_session` ON `research_reports` (`session_id`);--> statement-breakpoint
CREATE TABLE `research_sessions` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text DEFAULT 'local' NOT NULL,
	`question` text NOT NULL,
	`question_type` text,
	`status` text DEFAULT 'pending' NOT NULL,
	`model_id` text NOT NULL,
	`model_snapshot` text,
	`plan` text,
	`error` text,
	`duration_ms` integer,
	`token_usage` text,
	`cost_usd` real,
	`created_at` integer NOT NULL,
	`started_at` integer,
	`completed_at` integer,
	CONSTRAINT "ck_sessions_status" CHECK("status" IN ('pending', 'planning', 'researching', 'checking', 'writing', 'completed', 'failed', 'cancelled'))
);
--> statement-breakpoint
CREATE INDEX `idx_sessions_user_created` ON `research_sessions` (`user_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `idx_sessions_status` ON `research_sessions` (`status`);--> statement-breakpoint
CREATE TABLE `settings` (
	`key` text PRIMARY KEY NOT NULL,
	`user_id` text DEFAULT 'local' NOT NULL,
	`value` text,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `sources` (
	`id` text PRIMARY KEY NOT NULL,
	`session_id` text NOT NULL,
	`url` text NOT NULL,
	`url_canonical` text NOT NULL,
	`title` text,
	`domain` text,
	`source_type` text NOT NULL,
	`provider` text,
	`reliability` text DEFAULT 'unknown' NOT NULL,
	`published_at` integer,
	`retrieved_at` integer NOT NULL,
	`excerpt` text,
	`citation_index` integer,
	`http_status` integer,
	FOREIGN KEY (`session_id`) REFERENCES `research_sessions`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "ck_sources_type" CHECK("source_type" IN ('web', 'news', 'official', 'sec', 'api', 'docs', 'github', 'social')),
	CONSTRAINT "ck_sources_reliability" CHECK("reliability" IN ('primary', 'secondary', 'aggregator', 'unknown'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uq_sources_session_url` ON `sources` (`session_id`,`url_canonical`);--> statement-breakpoint
CREATE INDEX `idx_sources_session` ON `sources` (`session_id`);--> statement-breakpoint
CREATE TABLE `tool_calls` (
	`id` text PRIMARY KEY NOT NULL,
	`session_id` text NOT NULL,
	`task_id` text,
	`agent` text,
	`tool` text NOT NULL,
	`provider` text,
	`input` text,
	`output_summary` text,
	`ok` integer NOT NULL,
	`error_code` text,
	`cache_hit` integer DEFAULT false NOT NULL,
	`duration_ms` integer NOT NULL,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`session_id`) REFERENCES `research_sessions`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `idx_tool_calls_session` ON `tool_calls` (`session_id`);--> statement-breakpoint
CREATE INDEX `idx_tool_calls_tool_ok` ON `tool_calls` (`tool`,`ok`);--> statement-breakpoint
CREATE TABLE `watchlist` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text DEFAULT 'local' NOT NULL,
	`asset_type` text NOT NULL,
	`symbol` text NOT NULL,
	`display_name` text,
	`notes` text,
	`created_at` integer NOT NULL,
	CONSTRAINT "ck_watchlist_asset_type" CHECK("asset_type" IN ('crypto', 'stock'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `uq_watchlist_user_asset` ON `watchlist` (`user_id`,`asset_type`,`symbol`);