-- VISE OS — Full Database Schema
-- Creates all 12 custom collections as PostgreSQL tables
-- Run: docker exec -i vise-os-postgres psql -U vise_os -d vise_os < scripts/seed_schema.sql

BEGIN;

-- ══════════════════════════════════════════════
-- 1. agencies
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS agencies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    name VARCHAR(255) NOT NULL,
    tursab_no VARCHAR(50),
    contact_name VARCHAR(255) NOT NULL,
    contact_email VARCHAR(255) NOT NULL UNIQUE,
    contact_phone VARCHAR(50),
    telegram_chat_id VARCHAR(100),
    discord_webhook TEXT,
    google_sheet_id VARCHAR(255),
    google_sheet_sync_enabled BOOLEAN DEFAULT FALSE,
    default_countries JSONB,
    notification_preferences JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 2. agency_credits
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS agency_credits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    total_credits INTEGER NOT NULL DEFAULT 0,
    used_credits INTEGER NOT NULL DEFAULT 0,
    reserved_credits INTEGER NOT NULL DEFAULT 0,
    last_purchase_at TIMESTAMPTZ,
    last_usage_at TIMESTAMPTZ
);

-- ══════════════════════════════════════════════
-- 3. credit_transactions
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reference_id UUID,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 4. applicants
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS applicants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    external_ref VARCHAR(255),
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    first_name VARCHAR(255) NOT NULL,
    last_name VARCHAR(255) NOT NULL,
    birth_date DATE NOT NULL,
    nationality VARCHAR(10) NOT NULL,
    passport_number VARCHAR(255) NOT NULL,
    passport_expiry DATE NOT NULL,
    phone VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    target_country VARCHAR(10) NOT NULL,
    target_city VARCHAR(100),
    visa_type VARCHAR(100) NOT NULL,
    preferred_dates JSONB,
    exclude_weekends BOOLEAN DEFAULT FALSE,
    family_group_id UUID,
    parent_applicant_id UUID REFERENCES applicants(id),
    expires_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 5. booking_requests
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS booking_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    applicant_id UUID NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 5,
    target_system VARCHAR(20) NOT NULL,
    target_country VARCHAR(10) NOT NULL,
    target_location VARCHAR(100),
    visa_category VARCHAR(100) NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 50,
    last_attempt_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    slot_found_count INTEGER DEFAULT 0,
    error_code VARCHAR(100),
    error_message TEXT,
    assigned_account_id UUID,
    assigned_proxy_id UUID,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- ══════════════════════════════════════════════
-- 6. booking_results
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS booking_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    booking_request_id UUID NOT NULL REFERENCES booking_requests(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'success',
    target_system VARCHAR(20),
    target_country VARCHAR(10),
    confirmation_number VARCHAR(255),
    appointment_date DATE,
    appointment_time TIME,
    appointment_location VARCHAR(255),
    total_attempts INTEGER DEFAULT 0,
    total_duration_seconds INTEGER DEFAULT 0,
    credits_charged INTEGER DEFAULT 0,
    error_code VARCHAR(100),
    screenshot_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 7. bot_accounts
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS bot_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    system VARCHAR(20) NOT NULL,
    country VARCHAR(10) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    health_score INTEGER DEFAULT 100,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ,
    ban_detected_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 8. proxies
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS proxies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(50) NOT NULL,
    type VARCHAR(50) NOT NULL,
    country VARCHAR(10) NOT NULL,
    host VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL,
    username VARCHAR(255),
    password VARCHAR(255),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    health_score INTEGER DEFAULT 100,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    avg_response_ms INTEGER,
    last_used_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 9. browser_profiles
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS browser_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    user_agent TEXT NOT NULL,
    viewport_width INTEGER DEFAULT 1366,
    viewport_height INTEGER DEFAULT 768,
    timezone VARCHAR(100) DEFAULT 'Europe/Istanbul',
    locale VARCHAR(20) DEFAULT 'tr-TR',
    webgl_vendor VARCHAR(255),
    webgl_renderer VARCHAR(255),
    canvas_noise REAL,
    audio_noise REAL,
    fonts JSONB,
    plugins JSONB,
    usage_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 10. circuit_breakers
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS circuit_breakers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain VARCHAR(100) NOT NULL UNIQUE,
    state VARCHAR(20) NOT NULL DEFAULT 'closed',
    failure_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    last_failure_at TIMESTAMPTZ,
    opened_at TIMESTAMPTZ,
    closes_at TIMESTAMPTZ,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 11. system_logs
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS system_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    level VARCHAR(20) NOT NULL,
    category VARCHAR(50) NOT NULL,
    event VARCHAR(255) NOT NULL,
    agency_id UUID REFERENCES agencies(id),
    booking_request_id UUID REFERENCES booking_requests(id),
    message TEXT NOT NULL,
    details JSONB,
    ip_address VARCHAR(50),
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- 12. api_configurations
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS api_configurations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service VARCHAR(100) NOT NULL,
    config_key VARCHAR(100) NOT NULL,
    config_value TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- INDEXES
-- ══════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_agencies_status ON agencies(status);
CREATE INDEX IF NOT EXISTS idx_credits_agency ON agency_credits(agency_id);
CREATE INDEX IF NOT EXISTS idx_transactions_agency ON credit_transactions(agency_id);
CREATE INDEX IF NOT EXISTS idx_transactions_type ON credit_transactions(type);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON credit_transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_applicants_agency ON applicants(agency_id);
CREATE INDEX IF NOT EXISTS idx_applicants_status ON applicants(status);
CREATE INDEX IF NOT EXISTS idx_applicants_country ON applicants(target_country);
CREATE INDEX IF NOT EXISTS idx_applicants_expires ON applicants(expires_at);
CREATE INDEX IF NOT EXISTS idx_requests_agency ON booking_requests(agency_id);
CREATE INDEX IF NOT EXISTS idx_requests_status ON booking_requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_priority ON booking_requests(priority, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_requests_system ON booking_requests(target_system, target_country);
CREATE INDEX IF NOT EXISTS idx_results_agency ON booking_results(agency_id);
CREATE INDEX IF NOT EXISTS idx_results_date ON booking_results(created_at);
CREATE INDEX IF NOT EXISTS idx_accounts_system ON bot_accounts(system, country, status);
CREATE INDEX IF NOT EXISTS idx_accounts_health ON bot_accounts(health_score DESC);
CREATE INDEX IF NOT EXISTS idx_proxies_provider ON proxies(provider, status);
CREATE INDEX IF NOT EXISTS idx_proxies_country ON proxies(country, status);
CREATE INDEX IF NOT EXISTS idx_proxies_health ON proxies(health_score DESC);
CREATE INDEX IF NOT EXISTS idx_profiles_status ON browser_profiles(status);
CREATE INDEX IF NOT EXISTS idx_cb_domain ON circuit_breakers(domain);
CREATE INDEX IF NOT EXISTS idx_logs_level ON system_logs(level, created_at);
CREATE INDEX IF NOT EXISTS idx_logs_category ON system_logs(category, created_at);
CREATE INDEX IF NOT EXISTS idx_logs_agency ON system_logs(agency_id, created_at);

-- ══════════════════════════════════════════════
-- SAMPLE DATA
-- ══════════════════════════════════════════════
INSERT INTO agencies (status, name, tursab_no, contact_name, contact_email, contact_phone, google_sheet_sync_enabled, default_countries)
VALUES ('active', 'Test Travel Agency', '12345', 'Test Admin', 'test@agency.com', '+905551234567', FALSE, '["DE","IT","FR"]')
ON CONFLICT (contact_email) DO NOTHING;

-- Credits for test agency
INSERT INTO agency_credits (agency_id, total_credits, used_credits, reserved_credits)
SELECT id, 100, 0, 0 FROM agencies WHERE contact_email = 'test@agency.com'
ON CONFLICT DO NOTHING;

-- Sample bot account
INSERT INTO bot_accounts (system, country, email, password, status, health_score, success_count, failure_count, consecutive_failures)
VALUES ('vfs', 'de', 'vfsbot@test.com', 'test123', 'active', 100, 0, 0, 0);

COMMIT;

-- Summary
SELECT 'TABLES CREATED:' AS info, count(*) AS cnt
FROM information_schema.tables
WHERE table_schema = 'public' AND table_name NOT LIKE 'directus_%';
