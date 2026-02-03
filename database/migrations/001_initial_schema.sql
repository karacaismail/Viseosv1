-- ==========================================
-- VISE OS Database Schema
-- PostgreSQL 15+
-- Based on: 002-DIRECTUS-SCHEMA.md
-- ==========================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pgcrypto for encryption functions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ==========================================
-- 1. AGENCIES (Müşteri Firmalar)
-- ==========================================
CREATE TABLE agencies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status VARCHAR(20) NOT NULL CHECK (status IN ('active', 'suspended', 'trial')),
    name VARCHAR(255) NOT NULL,
    tursab_no VARCHAR(50),
    contact_name VARCHAR(255) NOT NULL,
    contact_email VARCHAR(255) NOT NULL UNIQUE,
    contact_phone VARCHAR(50) NOT NULL,
    telegram_chat_id VARCHAR(100),
    discord_webhook TEXT,
    google_sheet_id VARCHAR(100),
    google_sheet_sync_enabled BOOLEAN NOT NULL DEFAULT false,
    default_countries JSONB,
    notification_preferences JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agencies_status ON agencies(status);
CREATE INDEX idx_agencies_email ON agencies(contact_email);

-- ==========================================
-- 2. AGENCY_CREDITS (Kredi Bakiyeleri)
-- ==========================================
CREATE TABLE agency_credits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id UUID NOT NULL UNIQUE REFERENCES agencies(id) ON DELETE CASCADE,
    total_credits INTEGER NOT NULL DEFAULT 0,
    used_credits INTEGER NOT NULL DEFAULT 0,
    reserved_credits INTEGER NOT NULL DEFAULT 0,
    available_credits INTEGER GENERATED ALWAYS AS (total_credits - used_credits - reserved_credits) STORED,
    last_purchase_at TIMESTAMP WITH TIME ZONE,
    last_usage_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_credits_agency ON agency_credits(agency_id);

-- ==========================================
-- 3. CREDIT_TRANSACTIONS (Kredi Hareketleri)
-- ==========================================
CREATE TABLE credit_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    type VARCHAR(20) NOT NULL CHECK (type IN ('purchase', 'usage', 'refund', 'reserve', 'release')),
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reference_id UUID,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_transactions_agency ON credit_transactions(agency_id);
CREATE INDEX idx_transactions_type ON credit_transactions(type);
CREATE INDEX idx_transactions_date ON credit_transactions(created_at);

-- ==========================================
-- 4. APPLICANTS (Başvuru Sahipleri - PII)
-- ==========================================
CREATE TABLE applicants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    external_ref VARCHAR(100),
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'expired')),
    first_name TEXT NOT NULL, -- Encrypted
    last_name TEXT NOT NULL, -- Encrypted
    birth_date DATE NOT NULL,
    nationality VARCHAR(2) NOT NULL,
    passport_number TEXT NOT NULL, -- Encrypted
    passport_expiry DATE NOT NULL,
    phone TEXT NOT NULL, -- Encrypted
    email TEXT, -- Encrypted
    target_country VARCHAR(2) NOT NULL,
    target_city VARCHAR(100),
    visa_type VARCHAR(50) NOT NULL,
    preferred_dates JSONB,
    exclude_weekends BOOLEAN DEFAULT false,
    family_group_id UUID,
    parent_applicant_id UUID REFERENCES applicants(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_applicants_agency ON applicants(agency_id);
CREATE INDEX idx_applicants_status ON applicants(status);
CREATE INDEX idx_applicants_country ON applicants(target_country);
CREATE INDEX idx_applicants_expires ON applicants(expires_at);

-- ==========================================
-- 5. BOOKING_REQUESTS (İş Emirleri)
-- ==========================================
CREATE TABLE booking_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    applicant_id UUID NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'queued', 'processing', 'slot_found', 'booking', 'payment', 'verifying', 'completed', 'failed', 'expired', 'cancelled')),
    priority INTEGER NOT NULL DEFAULT 5,
    target_system VARCHAR(20) NOT NULL CHECK (target_system IN ('vfs', 'idata', 'bls', 'kkosmos')),
    target_country VARCHAR(2) NOT NULL,
    target_location VARCHAR(100),
    visa_category VARCHAR(50) NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 50,
    last_attempt_at TIMESTAMP WITH TIME ZONE,
    next_attempt_at TIMESTAMP WITH TIME ZONE,
    slot_found_count INTEGER NOT NULL DEFAULT 0,
    error_code VARCHAR(50),
    error_message TEXT,
    assigned_account_id UUID,
    assigned_proxy_id UUID,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_requests_agency ON booking_requests(agency_id);
CREATE INDEX idx_requests_status ON booking_requests(status);
CREATE INDEX idx_requests_priority ON booking_requests(priority, next_attempt_at);
CREATE INDEX idx_requests_system ON booking_requests(target_system, target_country);

-- ==========================================
-- 6. BOOKING_RESULTS (Sonuçlar - PII Yok)
-- ==========================================
CREATE TABLE booking_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    booking_request_id UUID NOT NULL REFERENCES booking_requests(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL CHECK (status IN ('success', 'failed', 'cancelled')),
    target_system VARCHAR(20) NOT NULL,
    target_country VARCHAR(2) NOT NULL,
    confirmation_number TEXT, -- Encrypted
    appointment_date DATE,
    appointment_time TIME,
    appointment_location VARCHAR(255),
    total_attempts INTEGER NOT NULL,
    total_duration_seconds INTEGER NOT NULL,
    credits_charged INTEGER NOT NULL,
    error_code VARCHAR(50),
    screenshot_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_results_agency ON booking_results(agency_id);
CREATE INDEX idx_results_date ON booking_results(created_at);
CREATE INDEX idx_results_country ON booking_results(target_country);

-- ==========================================
-- 7. BOT_ACCOUNTS (Bot Hesap Havuzu)
-- ==========================================
CREATE TABLE bot_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    system VARCHAR(20) NOT NULL CHECK (system IN ('vfs', 'idata', 'bls', 'kkosmos')),
    country VARCHAR(2) NOT NULL,
    email TEXT NOT NULL, -- Encrypted
    password TEXT NOT NULL, -- Encrypted
    status VARCHAR(20) NOT NULL CHECK (status IN ('active', 'cooldown', 'banned', 'retired')),
    health_score INTEGER NOT NULL DEFAULT 100 CHECK (health_score BETWEEN 0 AND 100),
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMP WITH TIME ZONE,
    cooldown_until TIMESTAMP WITH TIME ZONE,
    ban_detected_at TIMESTAMP WITH TIME ZONE,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_accounts_system ON bot_accounts(system, country, status);
CREATE INDEX idx_accounts_health ON bot_accounts(health_score DESC);
CREATE INDEX idx_accounts_cooldown ON bot_accounts(cooldown_until);

-- ==========================================
-- 8. PROXIES (Proxy Havuzu)
-- ==========================================
CREATE TABLE proxies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider VARCHAR(50) NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('residential', 'mobile', 'datacenter')),
    country VARCHAR(2) NOT NULL,
    host VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL,
    username VARCHAR(255) NOT NULL,
    password TEXT NOT NULL, -- Encrypted
    status VARCHAR(20) NOT NULL CHECK (status IN ('active', 'slow', 'blocked', 'retired')),
    health_score INTEGER NOT NULL DEFAULT 100 CHECK (health_score BETWEEN 0 AND 100),
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    avg_response_ms INTEGER,
    last_used_at TIMESTAMP WITH TIME ZONE,
    last_success_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_proxies_provider ON proxies(provider, status);
CREATE INDEX idx_proxies_country ON proxies(country, status);
CREATE INDEX idx_proxies_health ON proxies(health_score DESC);

-- ==========================================
-- 9. BROWSER_PROFILES (Fingerprint)
-- ==========================================
CREATE TABLE browser_profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('active', 'burned', 'retired')),
    user_agent TEXT NOT NULL,
    viewport_width INTEGER NOT NULL,
    viewport_height INTEGER NOT NULL,
    timezone VARCHAR(50) NOT NULL,
    locale VARCHAR(10) NOT NULL,
    webgl_vendor VARCHAR(255),
    webgl_renderer VARCHAR(255),
    canvas_noise FLOAT,
    audio_noise FLOAT,
    fonts JSONB,
    plugins JSONB,
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_profiles_status ON browser_profiles(status);
CREATE INDEX idx_profiles_usage ON browser_profiles(usage_count);

-- ==========================================
-- 10. PHONE_NUMBERS (SMS Verification)
-- ==========================================
CREATE TABLE phone_numbers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider VARCHAR(50) NOT NULL,
    number TEXT NOT NULL, -- Encrypted
    country VARCHAR(2) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('available', 'in_use', 'cooldown', 'burned')),
    usage_count INTEGER NOT NULL DEFAULT 0,
    max_usage INTEGER NOT NULL DEFAULT 5,
    last_used_at TIMESTAMP WITH TIME ZONE,
    cooldown_until TIMESTAMP WITH TIME ZONE,
    burned_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_phones_status ON phone_numbers(status, country);
CREATE INDEX idx_phones_cooldown ON phone_numbers(cooldown_until);

-- ==========================================
-- 11. PAYMENT_CARDS (Ödeme Kartları)
-- ==========================================
CREATE TABLE payment_cards (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    label VARCHAR(100) NOT NULL,
    cardholder_name TEXT NOT NULL, -- Encrypted
    card_number TEXT NOT NULL, -- Encrypted
    expiry_month TEXT NOT NULL, -- Encrypted
    expiry_year TEXT NOT NULL, -- Encrypted
    cvv TEXT NOT NULL, -- Encrypted
    card_type VARCHAR(20),
    is_default BOOLEAN NOT NULL DEFAULT false,
    status VARCHAR(20) NOT NULL CHECK (status IN ('active', 'expired', 'disabled')),
    last_used_at TIMESTAMP WITH TIME ZONE,
    failure_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cards_agency ON payment_cards(agency_id);
CREATE INDEX idx_cards_default ON payment_cards(agency_id, is_default);

-- ==========================================
-- 12. CIRCUIT_BREAKERS
-- ==========================================
CREATE TABLE circuit_breakers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    domain VARCHAR(100) NOT NULL UNIQUE,
    state VARCHAR(20) NOT NULL CHECK (state IN ('closed', 'open', 'half_open')),
    failure_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    last_failure_at TIMESTAMP WITH TIME ZONE,
    opened_at TIMESTAMP WITH TIME ZONE,
    closes_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cb_domain ON circuit_breakers(domain);
CREATE INDEX idx_cb_state ON circuit_breakers(state);

-- ==========================================
-- 13. SYSTEM_LOGS (Audit Trail)
-- ==========================================
CREATE TABLE system_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    level VARCHAR(20) NOT NULL CHECK (level IN ('debug', 'info', 'warning', 'error', 'critical')),
    category VARCHAR(50) NOT NULL,
    event VARCHAR(100) NOT NULL,
    agency_id UUID REFERENCES agencies(id) ON DELETE SET NULL,
    booking_request_id UUID REFERENCES booking_requests(id) ON DELETE SET NULL,
    message TEXT NOT NULL,
    details JSONB,
    ip_address VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_logs_level ON system_logs(level, created_at);
CREATE INDEX idx_logs_category ON system_logs(category, created_at);
CREATE INDEX idx_logs_agency ON system_logs(agency_id, created_at);

-- ==========================================
-- 14. API_CONFIGURATIONS
-- ==========================================
CREATE TABLE api_configurations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    service VARCHAR(50) NOT NULL,
    config_key VARCHAR(100) NOT NULL,
    config_value TEXT NOT NULL, -- Encrypted
    is_active BOOLEAN NOT NULL DEFAULT true,
    notes TEXT,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_by UUID,
    UNIQUE(service, config_key)
);

-- ==========================================
-- TRIGGERS
-- ==========================================

-- Auto-update updated_at timestamps
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_agencies_updated_at BEFORE UPDATE ON agencies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_booking_requests_updated_at BEFORE UPDATE ON booking_requests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_circuit_breakers_updated_at BEFORE UPDATE ON circuit_breakers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_api_configurations_updated_at BEFORE UPDATE ON api_configurations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ==========================================
-- PII AUTO-CLEANUP FUNCTION
-- ==========================================
CREATE OR REPLACE FUNCTION cleanup_expired_pii()
RETURNS void AS $$
BEGIN
    UPDATE applicants 
    SET 
        first_name = '[REDACTED]',
        last_name = '[REDACTED]',
        passport_number = '[REDACTED]',
        phone = '[REDACTED]',
        email = '[REDACTED]',
        deleted_at = NOW()
    WHERE expires_at < NOW() 
      AND deleted_at IS NULL;
END;
$$ LANGUAGE plpgsql;

-- Schedule this to run hourly via pg_cron or external scheduler
-- SELECT cleanup_expired_pii();

-- ==========================================
-- COMMENTS
-- ==========================================
COMMENT ON TABLE applicants IS 'PII data with 24h retention policy. Fields marked as encrypted require AES-256-GCM encryption.';
COMMENT ON TABLE payment_cards IS 'Payment card data. All card fields must be encrypted with separate PAYMENT_ENCRYPTION_KEY.';
COMMENT ON TABLE booking_results IS 'Audit trail without PII. Safe for long-term retention.';
COMMENT ON TABLE system_logs IS 'System audit logs. 90-day retention recommended.';
