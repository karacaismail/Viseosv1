# 🚀 VISE OS - B2B2C Visa Appointment Automation Platform

[![License](https://img.shields.io/badge/License-Proprietary-red.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![Directus](https://img.shields.io/badge/Directus-10.10-purple.svg)](https://directus.io/)

Enterprise-grade automation platform for Turkish visa agencies to automate appointment booking processes across VFS Global, iDATA, BLS Spain, and KKOSMOS systems.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Database Setup](#database-setup)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Documentation](#documentation)
- [Security](#security)
- [License](#license)

---

## 🎯 Overview

**VISE OS** is a fully automated visa appointment booking platform designed to serve **2000+ Turkish visa agencies** with:

- ✅ **1000 daily bookings** capacity
- ✅ **200 parallel sessions** support
- ✅ **4 visa systems** integration (VFS Global, iDATA, BLS Spain, KKOSMOS)
- ✅ **Offshore-compliant** architecture (GDPR/KVKK exempt)
- ✅ **Enterprise-grade** reliability (99.5% uptime target)

### Key Capabilities

| Capability | Description |
|------------|-------------|
| 🤖 **Stealth Automation** | Camoufox + Playwright with C++ level fingerprint modification |
| 🧩 **CAPTCHA Solving** | Multi-provider chain (2Captcha → CapSolver) |
| 🌐 **Proxy Management** | Bright Data residential IPs with health tracking |
| 💳 **Payment Processing** | Automatic payment with iyzico/Stripe |
| 📧 **Verification** | Email/SMS auto-verification |
| 🧠 **AI Decision Making** | Claude 3.5 Sonnet + GPT-4 for anomaly detection |
| 📊 **Real-time Dashboard** | Directus-based admin panel |
| 🔐 **PII Protection** | 24-hour retention with AES-256-GCM encryption |

---

## 🌟 Features

### For Agencies

- 📝 **Google Sheets Integration** - Simple data input via familiar interface
- 💰 **Prepaid Credit System** - Transparent pay-per-booking model
- 📱 **Real-time Notifications** - Telegram/Discord alerts
- 📈 **Analytics Dashboard** - Success rates, duration, country breakdowns
- 🔄 **Auto-retry Logic** - Intelligent failure recovery

### For System Operators

- 🎛️ **Directus Admin Panel** - Full system control
- 🔍 **Health Monitoring** - Prometheus + Grafana metrics
- 🚨 **Alert System** - Sentry error tracking
- 📊 **Resource Pools** - Account/Proxy/Browser profile management
- ⚙️ **Circuit Breakers** - Automatic ban recovery (10min-2h cooldown)

---

## 🏗️ Architecture

```
┌─ CUSTOMER LAYER (Google Sheets, Directus Panel, Telegram/Discord)
│
├─ API GATEWAY (FastAPI - Auth, Rate Limiting, Webhooks)
│
├─ ORCHESTRATION LAYER (Celery Beat, Redis Queue, PostgreSQL, AI Agent)
│
├─ RESOURCE POOL LAYER (Account/Proxy/Browser/Phone Number Pools)
│
├─ AUTOMATION LAYER (Stealth Browser, CAPTCHA Solver, Payment, Verification)
│
├─ SITE ADAPTER LAYER (VFS/iDATA/BLS/KKOSMOS Adapters)
│
└─ EXTERNAL SERVICES (Bright Data, 2Captcha, 5sim, Mailcow, OpenAI/Claude)
```

### Data Flow

1. **Input**: Agencies enter applicant data via Google Sheets
2. **Sync**: System polls Sheets every 5 minutes → Directus
3. **Queue**: Celery Beat schedules booking attempts with AI priority
4. **Execution**: Site adapter → Login → Slot Check → Booking → Payment
5. **Verification**: Email/SMS auto-verification
6. **Result**: Success → Credit deduct, Sheets cleanup, notification

---

## 🛠️ Tech Stack

### Backend

- **Python 3.12+** - Core language
- **FastAPI** - REST API framework
- **Celery + Redis** - Distributed task queue
- **asyncpg** - PostgreSQL async driver
- **Playwright + Camoufox** - Browser automation
- **Anthropic Claude / OpenAI GPT-4** - AI decision engine

### Frontend / Admin

- **Directus 10.10** - Headless CMS / Admin panel
- **PostgreSQL 15+** - Primary database

### Infrastructure

- **Docker + Docker Compose** - Containerization
- **Hetzner Cloud** - EU hosting (Germany + Finland)
- **Cloudflare** - WAF + DDoS protection + CDN
- **GitHub Actions** - CI/CD

### Monitoring

- **Prometheus** - Metrics collection
- **Grafana** - Visualization
- **Loki** - Log aggregation
- **Sentry** - Error tracking

---

## 📁 Project Structure

```
vise-os/
├── 001-ARCHITECTURE-OVERVIEW.md      # System architecture
├── 002-DIRECTUS-SCHEMA.md            # Database schema
├── 003-GOOGLE-SHEETS-TEMPLATE.md     # Input format
├── 004-STEALTH-ENGINE.md             # Bot bypass techniques
├── 005-PROXY-MANAGER.md              # Proxy pool management
├── 006-CAPTCHA-SOLVER.md             # CAPTCHA solving chain
├── 007-ACCOUNT-POOL-MANAGER.md       # Account pool management
├── 008-VFS-ADAPTER.md                # VFS Global adapter
├── 009-IDATA-ADAPTER.md              # iDATA adapter
├── 010-BLS-ADAPTER.md                # BLS Spain adapter
├── 011-KKOSMOS-ADAPTER.md            # KKOSMOS adapter
├── 012-AI-DECISION-ENGINE.md         # AI decision engine
├── 013-STATE-MACHINE.md              # Booking state machine
├── 014-QUEUE-ORCHESTRATOR.md         # Queue orchestration
├── 015-PAYMENT-AUTOMATION.md         # Payment processing
├── 016-EMAIL-VERIFICATION.md         # Email verification
├── 017-SMS-VERIFICATION.md           # SMS verification
├── 018-MONITORING-DASHBOARD.md       # Monitoring setup
├── 019-ALERT-SYSTEM.md               # Alert configuration
├── 020-ANALYTICS-REPORTING.md        # Analytics & reporting
├── 021-IMPLEMENTATION.md             # Deployment guide
│
├── src/                               # Python source code
│   ├── ai/                           # AI providers & decision engine
│   ├── api/                          # FastAPI application
│   ├── bot/                          # Automation engine
│   │   ├── adapters/                 # Site-specific adapters
│   │   ├── engine/                   # Browser & stealth
│   │   ├── services/                 # Account, proxy, CAPTCHA
│   │   └── verification/             # Email/SMS verification
│   ├── core/                         # Business logic
│   │   ├── agency/
│   │   ├── applicant/
│   │   └── booking/
│   ├── integrations/                 # Google Sheets, Telegram
│   ├── monitoring/                   # Metrics & logging
│   ├── payment/                      # Payment gateways
│   └── queue/                        # Celery tasks
│
├── directus/                         # Directus extensions
│   └── extensions/
│       ├── endpoints/                # Custom API endpoints
│       │   ├── booking-stats/        # Statistics endpoint
│       │   └── health-check/         # Health check endpoint
│       └── hooks/                    # Event hooks
│           └── booking-hooks/        # Booking event triggers
│
├── database/                         # Database files
│   ├── migrations/                   # SQL migrations
│   │   └── 001_initial_schema.sql   # Initial schema
│   └── seeds/                        # Seed data
│
├── docker/                           # Docker configurations
├── config/                           # Configuration files
│   ├── grafana/                      # Grafana dashboards
│   └── prometheus/                   # Prometheus config
│
├── tests/                            # Test suite
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── .env.example                      # Environment variables template
├── pyproject.toml                    # Python dependencies
├── docker-compose.yml                # Docker compose setup
└── README.md                         # This file
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.12+**
- **PostgreSQL 15+**
- **Redis 7+**
- **Docker & Docker Compose** (recommended)
- **Node.js 20+** (for Directus extensions)

### Installation

#### Option 1: Docker (Recommended)

```bash
# Clone repository
git clone https://github.com/karacaismail/Viseosv1.git
cd Viseosv1

# Copy environment template
cp .env.example .env

# Edit .env with your credentials
nano .env

# Start services
docker-compose up -d

# Check status
docker-compose ps
```

#### Option 2: Manual Setup

```bash
# Clone repository
git clone https://github.com/karacaismail/Viseosv1.git
cd Viseosv1

# Create virtual environment
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Install Playwright browsers
playwright install chromium

# Setup database
psql -U postgres -f database/migrations/001_initial_schema.sql

# Copy environment template
cp .env.example .env

# Edit .env
nano .env

# Run migrations (if using Directus)
cd directus
npx directus bootstrap

# Start services
# Terminal 1: API
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Celery Worker
celery -A src.queue worker -l info

# Terminal 3: Celery Beat
celery -A src.queue beat -l info

# Terminal 4: Directus
npx directus start
```

---

## 🗄️ Database Setup

### Initialize Database

```bash
# Create database
createdb vise_production

# Run migration
psql -U postgres -d vise_production -f database/migrations/001_initial_schema.sql

# Verify tables
psql -U postgres -d vise_production -c "\dt"
```

### Database Schema

The database includes 14 main collections:

1. **agencies** - Customer firms
2. **agency_credits** - Credit balances
3. **credit_transactions** - Credit audit trail
4. **applicants** - Applicant data (24h PII retention)
5. **booking_requests** - Booking jobs
6. **booking_results** - Results (no PII)
7. **bot_accounts** - Bot account pool
8. **proxies** - Proxy pool
9. **browser_profiles** - Fingerprint profiles
10. **phone_numbers** - SMS verification pool
11. **payment_cards** - Payment cards (encrypted)
12. **circuit_breakers** - Circuit breaker states
13. **system_logs** - Audit logs
14. **api_configurations** - API credentials

See [002-DIRECTUS-SCHEMA.md](002-DIRECTUS-SCHEMA.md) for complete schema documentation.

---

## ⚙️ Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/vise_production

# Redis
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1

# Directus
DIRECTUS_URL=http://localhost:8055
DIRECTUS_SECRET=your-secret-key

# External APIs
ANTHROPIC_API_KEY=sk-ant-xxx
OPENAI_API_KEY=sk-xxx
BRIGHTDATA_TOKEN=xxx
CAPTCHA_2CAPTCHA_API_KEY=xxx
SMS_5SIM_API_KEY=xxx

# Encryption (CRITICAL - Generate secure keys)
APPLICANT_ENCRYPTION_KEY=xxx
PAYMENT_ENCRYPTION_KEY=xxx

# Monitoring
SENTRY_DSN=https://xxx@sentry.io/xxx
TELEGRAM_BOT_TOKEN=xxx
```

### Generating Encryption Keys

```bash
# Generate secure encryption keys
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 🚀 Deployment

### Production Deployment

See [021-IMPLEMENTATION.md](021-IMPLEMENTATION.md) for complete deployment guide.

#### Quick Overview

**Hosting**: Hetzner Cloud (Germany + Finland)
**Monthly Cost**: ~$1,171 ($360 infrastructure + $811 services)

**Infrastructure**:
- 2× API servers (CPX31)
- 4× Worker servers (CPX41)
- 1× Database server (CCX33)
- 2× Redis servers (CPX21)
- 1× Monitoring server (CPX31)

**Services**:
- Cloudflare Pro (DDoS + WAF)
- Bright Data (Proxy)
- 2Captcha + CapSolver (CAPTCHA)
- 5sim.net (SMS)
- Anthropic + OpenAI (AI)
- Sentry (Error tracking)

---

## 📚 Documentation

All documentation is available in numbered markdown files:

- **Architecture**: [001-ARCHITECTURE-OVERVIEW.md](001-ARCHITECTURE-OVERVIEW.md)
- **Database**: [002-DIRECTUS-SCHEMA.md](002-DIRECTUS-SCHEMA.md)
- **Data Input**: [003-GOOGLE-SHEETS-TEMPLATE.md](003-GOOGLE-SHEETS-TEMPLATE.md)
- **Site Adapters**: [008-VFS-ADAPTER.md](008-VFS-ADAPTER.md) through [011-KKOSMOS-ADAPTER.md](011-KKOSMOS-ADAPTER.md)
- **Deployment**: [021-IMPLEMENTATION.md](021-IMPLEMENTATION.md)

---

## 🔐 Security

### Data Protection

- ✅ **PII Encryption**: AES-256-GCM for all personal data
- ✅ **24-Hour Retention**: Auto-cleanup of applicant PII
- ✅ **Payment Encryption**: Separate encryption key for card data
- ✅ **Offshore Hosting**: GDPR/KVKK exempt jurisdiction
- ✅ **Audit Logging**: All operations logged (no PII in logs)
- ✅ **RBAC**: 5-tier role-based access control

### Secret Management

All secrets are managed via **SOPS + age encryption**:

```bash
# Encrypt secrets
sops --encrypt secrets.yaml > secrets.enc.yaml

# Decrypt for deployment
sops --decrypt secrets.enc.yaml > secrets.yaml
```

**Never commit**:
- `.env` files
- `secrets.yaml` (unencrypted)
- API keys
- Encryption keys

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test suite
pytest tests/unit
pytest tests/integration
pytest tests/e2e

# Run linting
ruff check src/
mypy src/
```

---

## 📊 Monitoring

Access monitoring dashboards:

- **Grafana**: http://localhost:3000 (metrics visualization)
- **Prometheus**: http://localhost:9090 (metrics collection)
- **Directus**: http://localhost:8055 (admin panel)
- **API Docs**: http://localhost:8000/docs (Swagger UI)

---

## 📄 License

**Proprietary** - All rights reserved.

This software is proprietary and confidential. Unauthorized copying, distribution, or use is strictly prohibited.

---

## 👥 Support

For technical support or questions:

- 📧 Email: support@vise-os.com
- 💬 Telegram: @vise-support
- 📘 Documentation: [Full docs](https://docs.vise-os.com)

---

## 🗺️ Roadmap

- [x] Core automation engine
- [x] VFS Global adapter
- [x] iDATA adapter
- [x] BLS Spain adapter
- [x] KKOSMOS adapter
- [ ] Production deployment
- [ ] Agency onboarding
- [ ] Mobile app (React Native)
- [ ] Advanced analytics
- [ ] Machine learning optimization

---

**Built with ❤️ for Turkish visa agencies**
