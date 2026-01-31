# 021 - Implementation & Deployment Specification

## Amaç

VISE OS platformunun production ortamına deployment için kapsamlı checklist. Hosting altyapısı, servis abonelikleri, domain yapılandırması, API key yönetimi, offshore setup ve operasyonel hazırlık. Hukuki riskleri minimize eden altyapı kararları.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 001-ARCHITECTURE-OVERVIEW | Infrastructure requirements |
| 018-MONITORING-DASHBOARD | Monitoring setup |
| 019-ALERT-SYSTEM | Alert infrastructure |
| Tüm modüller | Service dependencies |

---

## Infrastructure Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     VISE OS PRODUCTION INFRASTRUCTURE                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────┐            │
│  │                    OFFSHORE LAYER (EU)                       │            │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │            │
│  │  │  Hetzner    │  │  Hetzner    │  │  Hetzner    │         │            │
│  │  │  Germany    │  │  Finland    │  │  (Backup)   │         │            │
│  │  │  Primary    │  │  Secondary  │  │             │         │            │
│  │  └─────────────┘  └─────────────┘  └─────────────┘         │            │
│  └─────────────────────────────────────────────────────────────┘            │
│                              │                                               │
│                              ▼                                               │
│  ┌─────────────────────────────────────────────────────────────┐            │
│  │                    CLOUDFLARE LAYER                          │            │
│  │  • DDoS Protection  • WAF  • CDN  • DNS                     │            │
│  └─────────────────────────────────────────────────────────────┘            │
│                              │                                               │
│                              ▼                                               │
│  ┌─────────────────────────────────────────────────────────────┐            │
│  │                    APPLICATION LAYER                         │            │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │            │
│  │  │  API    │  │ Workers │  │  Bot    │  │ Admin   │        │            │
│  │  │ Servers │  │ (Celery)│  │ Browsers│  │  Panel  │        │            │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │            │
│  └─────────────────────────────────────────────────────────────┘            │
│                              │                                               │
│                              ▼                                               │
│  ┌─────────────────────────────────────────────────────────────┐            │
│  │                    DATA LAYER                                │            │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │            │
│  │  │PostgreSQL│ │  Redis  │  │  Minio  │  │  Loki   │        │            │
│  │  │(Directus)│ │ (Queue) │  │ (Files) │  │ (Logs)  │        │            │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │            │
│  └─────────────────────────────────────────────────────────────┘            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Offshore Setup Strategy

### Neden Offshore?

1. **Hukuki Risk Dağıtımı:** Türkiye dışında hosting, TCK kapsamındaki riskleri azaltır
2. **GDPR Uyumu:** EU hosting, GDPR compliance kolaylaştırır
3. **Maliyet:** Hetzner EU, Türkiye hosting'den %60-70 ucuz
4. **Performans:** EU visa sitelerine yakınlık
5. **Veri Güvenliği:** EU data protection standartları

### Önerilen Jurisdiction

| Seçenek | Avantaj | Dezavantaj | Öneri |
|---------|---------|------------|-------|
| **Germany (DE)** | GDPR uyumu, Hetzner ana lokasyon | Strict regulations | Primary |
| **Finland (FI)** | Privacy-friendly, Hetzner lokasyon | - | Secondary |
| **Netherlands (NL)** | Business-friendly | - | Alternative |
| **Estonia (EE)** | E-residency, digital-friendly | Limited infrastructure | Company setup |

### Şirket Yapısı Önerisi

```
┌─────────────────────────────────────────┐
│     VISE OS Holding (Estonia EE)        │
│     • E-residency ile kurulum           │
│     • IP ownership                       │
│     • Lisans hakları                     │
└──────────────────┬──────────────────────┘
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
┌─────────────────┐  ┌─────────────────┐
│  VISE OS EU     │  │  VISE OS TR     │
│  (Germany/NL)   │  │  (Türkiye)      │
│  • Operations   │  │  • Sales        │
│  • Data hosting │  │  • Support      │
│  • Technical    │  │  • Local billing│
└─────────────────┘  └─────────────────┘
```

---

## Hosting Infrastructure

### Primary: Hetzner Cloud

**Neden Hetzner?**
- Maliyet: AWS/GCP'den %70 ucuz
- EU GDPR compliance
- Bare metal + cloud hybrid
- DDoS protection included
- ISO 27001 certified

### Server Configuration

```yaml
# Production Environment
production:
  # API & Web Servers
  api_servers:
    type: CPX31  # 4 vCPU, 8GB RAM
    count: 2
    location: nbg1  # Nuremberg, Germany
    monthly_cost: €15.59 x 2 = €31.18
    
  # Worker Servers (Bot execution)
  worker_servers:
    type: CPX41  # 8 vCPU, 16GB RAM
    count: 4
    location: nbg1
    monthly_cost: €29.59 x 4 = €118.36
    
  # Database Server
  database:
    type: CCX33  # Dedicated, 8 vCPU, 32GB RAM
    count: 1
    location: nbg1
    monthly_cost: €89.00
    storage: 240GB NVMe
    
  # Redis/Queue Server
  cache:
    type: CPX21  # 3 vCPU, 4GB RAM
    count: 2  # Master + Replica
    location: nbg1
    monthly_cost: €8.69 x 2 = €17.38
    
  # Monitoring Stack
  monitoring:
    type: CPX31
    count: 1
    location: nbg1
    monthly_cost: €15.59
    
  # Storage (Minio)
  storage:
    type: Volume
    size: 500GB
    monthly_cost: €24.50

# Total Monthly: ~€296 (~$320)
```

### Backup Location: Finland

```yaml
backup:
  location: hel1  # Helsinki, Finland
  servers:
    - type: CPX21
      purpose: Database replica
      monthly_cost: €8.69
    - type: Volume
      size: 200GB
      purpose: Backup storage
      monthly_cost: €9.80
      
# Backup Monthly: ~€18.49
```

### Network Configuration

```yaml
networking:
  # Private Network
  private_network:
    name: vise-internal
    ip_range: 10.0.0.0/16
    subnets:
      - name: api
        range: 10.0.1.0/24
      - name: workers
        range: 10.0.2.0/24
      - name: data
        range: 10.0.3.0/24
        
  # Floating IPs
  floating_ips:
    - purpose: API load balancer
      location: nbg1
    - purpose: Admin panel
      location: nbg1
      
  # Load Balancer
  load_balancer:
    type: lb11
    location: nbg1
    monthly_cost: €5.39
```

---

## Domain & DNS Setup

### Domain Strategy

```yaml
domains:
  primary:
    domain: vise.io  # veya viseapp.com
    registrar: Cloudflare Registrar
    purpose: Main application
    
  api:
    domain: api.vise.io
    purpose: API endpoints
    
  admin:
    domain: admin.vise.io
    purpose: Admin panel (IP restricted)
    
  agency:
    domain: app.vise.io
    purpose: Agency portal
    
  webhook:
    domain: hooks.vise.io
    purpose: Webhook endpoints
```

### Cloudflare Configuration

```yaml
cloudflare:
  plan: Pro  # $20/month
  features:
    - WAF (Web Application Firewall)
    - DDoS protection (Unlimited)
    - CDN
    - SSL/TLS (Full Strict)
    - Page Rules
    - Rate Limiting
    
  dns_records:
    - type: A
      name: "@"
      content: ${FLOATING_IP_API}
      proxied: true
      
    - type: A
      name: "api"
      content: ${FLOATING_IP_API}
      proxied: true
      
    - type: A
      name: "admin"
      content: ${FLOATING_IP_ADMIN}
      proxied: true
      # Access restricted by Cloudflare Access
      
    - type: CNAME
      name: "www"
      content: "@"
      proxied: true
      
  page_rules:
    - url: "api.vise.io/*"
      settings:
        cache_level: bypass
        ssl: full_strict
        
    - url: "admin.vise.io/*"
      settings:
        security_level: high
        
  waf_rules:
    - description: "Block known bad bots"
      expression: "(cf.client.bot)"
      action: challenge
      
    - description: "Rate limit API"
      expression: "(http.request.uri.path contains '/api/')"
      action: challenge
      threshold: 100 requests/minute
```

---

## Service Subscriptions

### External Services Checklist

```yaml
services:
  # ============================================
  # PROXY SERVICES
  # ============================================
  proxy:
    primary:
      provider: Bright Data
      plan: Pay As You Go
      type: Residential
      estimated_monthly: $200-500
      api_endpoint: https://api.brightdata.com
      features:
        - Residential IPs
        - Country targeting (TR, DE, NL, etc.)
        - Session persistence
        
    backup:
      provider: Oxylabs
      plan: Micro ($99/month)
      type: Residential
      features:
        - Backup pool
        - Different IP ranges
        
  # ============================================
  # CAPTCHA SERVICES
  # ============================================
  captcha:
    primary:
      provider: 2Captcha
      pricing: $2.99/1000 solves
      estimated_monthly: $50-150
      api_key_env: CAPTCHA_2CAPTCHA_API_KEY
      
    backup:
      provider: CapSolver
      pricing: $0.8-1.5/1000 solves
      api_key_env: CAPTCHA_CAPSOLVER_API_KEY
      
    turnstile:
      provider: CapSolver
      pricing: $2.5/1000 solves
      
  # ============================================
  # SMS SERVICES
  # ============================================
  sms:
    primary:
      provider: 5sim.net
      pricing: $0.10-0.50/number
      estimated_monthly: $50-200
      api_key_env: SMS_5SIM_API_KEY
      
    backup:
      provider: SMSHub
      pricing: $0.05-0.30/number
      api_key_env: SMS_SMSHUB_API_KEY
      
    tertiary:
      provider: SMS-Activate
      pricing: $0.08-0.40/number
      api_key_env: SMS_SMSACTIVATE_API_KEY
      
  # ============================================
  # AI/LLM SERVICES
  # ============================================
  ai:
    primary:
      provider: Anthropic (Claude)
      model: claude-3-5-sonnet
      pricing: $3/MTok input, $15/MTok output
      estimated_monthly: $50-200
      api_key_env: ANTHROPIC_API_KEY
      
    backup:
      provider: OpenAI (GPT-4)
      model: gpt-4-turbo
      pricing: $10/MTok input, $30/MTok output
      api_key_env: OPENAI_API_KEY
      
  # ============================================
  # EMAIL SERVICES
  # ============================================
  email:
    transactional:
      provider: Resend
      plan: Pro ($20/month)
      features:
        - 50K emails/month
        - Custom domains
      api_key_env: RESEND_API_KEY
      
    imap_access:
      providers:
        - Gmail (OAuth)
        - Outlook (OAuth)
        - Yandex
      note: Bot account emails
      
  # ============================================
  # PAYMENT SERVICES
  # ============================================
  payment:
    turkey:
      provider: iyzico
      fee: 2.49% + ₺0.35
      features:
        - TRY processing
        - 3D Secure
        - Marketplace
      api_key_env: IYZICO_API_KEY
      
    international:
      provider: Stripe
      fee: 2.9% + $0.30
      features:
        - Multi-currency
        - Subscriptions
      api_key_env: STRIPE_API_KEY
      
  # ============================================
  # MONITORING & ALERTING
  # ============================================
  monitoring:
    uptime:
      provider: BetterUptime
      plan: Free tier
      features:
        - 10 monitors
        - 3 min check interval
        
    error_tracking:
      provider: Sentry
      plan: Team ($26/month)
      features:
        - Error tracking
        - Performance monitoring
      dsn_env: SENTRY_DSN
      
    logging:
      provider: Self-hosted (Loki)
      note: On Hetzner
      
  # ============================================
  # COMMUNICATION
  # ============================================
  communication:
    alerts:
      provider: Telegram Bot
      cost: Free
      bot_token_env: TELEGRAM_BOT_TOKEN
      
    team:
      provider: Slack
      plan: Free
      webhook_env: SLACK_WEBHOOK_URL
      
  # ============================================
  # CODE & DEPLOYMENT
  # ============================================
  devops:
    code:
      provider: GitHub
      plan: Team ($4/user/month)
      features:
        - Private repos
        - Actions CI/CD
        
    registry:
      provider: GitHub Container Registry
      cost: Included with GitHub
      
    secrets:
      provider: SOPS + age
      cost: Free (self-managed)
```

### Monthly Cost Summary

```yaml
monthly_costs:
  infrastructure:
    hetzner_production: €296
    hetzner_backup: €19
    cloudflare: $20
    subtotal: ~$360
    
  services:
    proxy_brightdata: $350 (avg)
    proxy_oxylabs: $99
    captcha: $100 (avg)
    sms: $100 (avg)
    ai: $100 (avg)
    email_resend: $20
    sentry: $26
    github: $16 (4 users)
    subtotal: ~$811
    
  payment_fees:
    iyzico: Variable (2.49%)
    stripe: Variable (2.9%)
    
  total_fixed: ~$1,171/month
  
  # Break-even: ~47 bookings @ $25 average
```

---

## API Keys & Secrets Management

### Secret Categories

```yaml
secrets:
  # Level 1: Critical (encrypted, limited access)
  critical:
    - MASTER_ENCRYPTION_KEY
    - DATABASE_PASSWORD
    - DIRECTUS_ADMIN_PASSWORD
    - JWT_SECRET
    - PAYMENT_API_KEYS
    
  # Level 2: Sensitive (encrypted)
  sensitive:
    - PROXY_API_KEYS
    - CAPTCHA_API_KEYS
    - SMS_API_KEYS
    - AI_API_KEYS
    - EMAIL_API_KEYS
    
  # Level 3: Configuration
  configuration:
    - TELEGRAM_BOT_TOKEN
    - SLACK_WEBHOOK_URL
    - SENTRY_DSN
    - CLOUDFLARE_API_TOKEN
```

### SOPS + age Setup

```bash
# 1. Install age
brew install age  # macOS
apt install age   # Ubuntu

# 2. Generate key pair
age-keygen -o keys.txt
# Public key: age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 3. Create .sops.yaml
cat > .sops.yaml << EOF
creation_rules:
  - path_regex: \.enc\.yaml$
    age: >-
      age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
EOF

# 4. Encrypt secrets file
sops --encrypt secrets.yaml > secrets.enc.yaml

# 5. Decrypt for use
sops --decrypt secrets.enc.yaml > secrets.yaml
```

### Secrets File Structure

```yaml
# secrets.yaml (ENCRYPTED with SOPS)
production:
  # Database
  database:
    host: 10.0.3.10
    port: 5432
    name: vise_production
    user: vise_app
    password: ENC[AES256_GCM,data:xxxxx]
    
  # Directus
  directus:
    admin_email: admin@vise.io
    admin_password: ENC[AES256_GCM,data:xxxxx]
    secret: ENC[AES256_GCM,data:xxxxx]
    
  # Redis
  redis:
    host: 10.0.3.20
    port: 6379
    password: ENC[AES256_GCM,data:xxxxx]
    
  # External APIs
  apis:
    anthropic_key: ENC[AES256_GCM,data:xxxxx]
    openai_key: ENC[AES256_GCM,data:xxxxx]
    brightdata_token: ENC[AES256_GCM,data:xxxxx]
    oxylabs_user: ENC[AES256_GCM,data:xxxxx]
    oxylabs_pass: ENC[AES256_GCM,data:xxxxx]
    captcha_2captcha: ENC[AES256_GCM,data:xxxxx]
    captcha_capsolver: ENC[AES256_GCM,data:xxxxx]
    sms_5sim: ENC[AES256_GCM,data:xxxxx]
    sms_smshub: ENC[AES256_GCM,data:xxxxx]
    iyzico_api_key: ENC[AES256_GCM,data:xxxxx]
    iyzico_secret: ENC[AES256_GCM,data:xxxxx]
    resend_api_key: ENC[AES256_GCM,data:xxxxx]
    
  # Communication
  communication:
    telegram_bot_token: ENC[AES256_GCM,data:xxxxx]
    telegram_alert_chat: ENC[AES256_GCM,data:xxxxx]
    slack_webhook: ENC[AES256_GCM,data:xxxxx]
    
  # Monitoring
  monitoring:
    sentry_dsn: ENC[AES256_GCM,data:xxxxx]
```

### Environment Variables Template

```bash
# .env.template (NOT encrypted, no real values)

# ============================================
# DATABASE
# ============================================
DATABASE_HOST=10.0.3.10
DATABASE_PORT=5432
DATABASE_NAME=vise_production
DATABASE_USER=vise_app
DATABASE_PASSWORD=<from-secrets>

# ============================================
# DIRECTUS
# ============================================
DIRECTUS_URL=http://10.0.1.10:8055
DIRECTUS_ADMIN_EMAIL=admin@vise.io
DIRECTUS_ADMIN_PASSWORD=<from-secrets>
DIRECTUS_SECRET=<from-secrets>

# ============================================
# REDIS
# ============================================
REDIS_URL=redis://:password@10.0.3.20:6379/0
CELERY_BROKER_URL=redis://:password@10.0.3.20:6379/1

# ============================================
# PROXY SERVICES
# ============================================
BRIGHTDATA_TOKEN=<from-secrets>
OXYLABS_USER=<from-secrets>
OXYLABS_PASS=<from-secrets>

# ============================================
# CAPTCHA SERVICES
# ============================================
CAPTCHA_2CAPTCHA_API_KEY=<from-secrets>
CAPTCHA_CAPSOLVER_API_KEY=<from-secrets>

# ============================================
# SMS SERVICES
# ============================================
SMS_5SIM_API_KEY=<from-secrets>
SMS_SMSHUB_API_KEY=<from-secrets>
SMS_SMSACTIVATE_API_KEY=<from-secrets>

# ============================================
# AI SERVICES
# ============================================
ANTHROPIC_API_KEY=<from-secrets>
OPENAI_API_KEY=<from-secrets>

# ============================================
# PAYMENT
# ============================================
IYZICO_API_KEY=<from-secrets>
IYZICO_SECRET_KEY=<from-secrets>
IYZICO_BASE_URL=https://api.iyzipay.com

# ============================================
# EMAIL
# ============================================
RESEND_API_KEY=<from-secrets>
EMAIL_FROM=noreply@vise.io

# ============================================
# MONITORING
# ============================================
SENTRY_DSN=<from-secrets>
TELEGRAM_BOT_TOKEN=<from-secrets>
TELEGRAM_ALERT_CHAT_ID=<from-secrets>
SLACK_WEBHOOK_URL=<from-secrets>
```

---

## Docker Deployment

### Docker Compose Structure

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  # ============================================
  # API SERVER
  # ============================================
  api:
    image: ghcr.io/vise-os/api:${VERSION:-latest}
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '2'
          memory: 4G
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
    networks:
      - vise-internal
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.api.rule=Host(`api.vise.io`)"
      
  # ============================================
  # DIRECTUS
  # ============================================
  directus:
    image: directus/directus:10.10
    deploy:
      replicas: 1
      resources:
        limits:
          cpus: '2'
          memory: 4G
    environment:
      - DB_CLIENT=pg
      - DB_HOST=${DATABASE_HOST}
      - DB_PORT=${DATABASE_PORT}
      - DB_DATABASE=${DATABASE_NAME}
      - DB_USER=${DATABASE_USER}
      - DB_PASSWORD=${DATABASE_PASSWORD}
      - SECRET=${DIRECTUS_SECRET}
      - ADMIN_EMAIL=${DIRECTUS_ADMIN_EMAIL}
      - ADMIN_PASSWORD=${DIRECTUS_ADMIN_PASSWORD}
    volumes:
      - directus-uploads:/directus/uploads
    networks:
      - vise-internal
      
  # ============================================
  # CELERY WORKERS
  # ============================================
  worker-vfs:
    image: ghcr.io/vise-os/worker:${VERSION:-latest}
    command: celery -A vise worker -Q vfs,high_priority -c 8
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '4'
          memory: 8G
    environment:
      - CELERY_BROKER_URL=${CELERY_BROKER_URL}
    volumes:
      - /dev/shm:/dev/shm  # For browser
    networks:
      - vise-internal
      
  worker-idata:
    image: ghcr.io/vise-os/worker:${VERSION:-latest}
    command: celery -A vise worker -Q idata,normal -c 12
    deploy:
      replicas: 1
      resources:
        limits:
          cpus: '2'
          memory: 4G
    environment:
      - CELERY_BROKER_URL=${CELERY_BROKER_URL}
    networks:
      - vise-internal
      
  worker-generic:
    image: ghcr.io/vise-os/worker:${VERSION:-latest}
    command: celery -A vise worker -Q normal,retry,scheduled -c 10
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '2'
          memory: 4G
    environment:
      - CELERY_BROKER_URL=${CELERY_BROKER_URL}
    networks:
      - vise-internal
      
  # ============================================
  # CELERY BEAT (SCHEDULER)
  # ============================================
  beat:
    image: ghcr.io/vise-os/worker:${VERSION:-latest}
    command: celery -A vise beat --loglevel=info
    deploy:
      replicas: 1
    environment:
      - CELERY_BROKER_URL=${CELERY_BROKER_URL}
    networks:
      - vise-internal
      
  # ============================================
  # MONITORING STACK
  # ============================================
  prometheus:
    image: prom/prometheus:v2.47.0
    volumes:
      - ./prometheus:/etc/prometheus
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=30d'
    networks:
      - vise-internal
      
  grafana:
    image: grafana/grafana:10.2.0
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
      - GF_SERVER_ROOT_URL=https://admin.vise.io/grafana
    networks:
      - vise-internal
      
  loki:
    image: grafana/loki:2.9.0
    volumes:
      - ./loki:/etc/loki
      - loki-data:/loki
    command: -config.file=/etc/loki/loki-config.yml
    networks:
      - vise-internal
      
  # ============================================
  # REDIS
  # ============================================
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD} --appendonly yes
    volumes:
      - redis-data:/data
    networks:
      - vise-internal
      
  # ============================================
  # TRAEFIK (REVERSE PROXY)
  # ============================================
  traefik:
    image: traefik:v3.0
    command:
      - "--api.dashboard=true"
      - "--providers.docker=true"
      - "--providers.docker.swarmMode=true"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.letsencrypt.acme.tlschallenge=true"
      - "--certificatesresolvers.letsencrypt.acme.email=admin@vise.io"
      - "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - traefik-certs:/letsencrypt
    networks:
      - vise-internal

networks:
  vise-internal:
    driver: overlay
    attachable: true

volumes:
  directus-uploads:
  prometheus-data:
  grafana-data:
  loki-data:
  redis-data:
  traefik-certs:
```

---

## CI/CD Pipeline

### GitHub Actions Workflow

```yaml
# .github/workflows/deploy.yml
name: Deploy to Production

on:
  push:
    branches: [main]
    tags: ['v*']
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
          
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
          
      - name: Run tests
        run: pytest --cov=vise tests/
        
      - name: Upload coverage
        uses: codecov/codecov-action@v3

  build:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
      
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
        
      - name: Login to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
          
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=semver,pattern={{version}}
            type=sha,prefix=
            
      - name: Build and push API image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/Dockerfile.api
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          
      - name: Build and push Worker image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/Dockerfile.worker
          push: true
          tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}/worker:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Install SOPS
        run: |
          curl -LO https://github.com/getsops/sops/releases/download/v3.8.1/sops-v3.8.1.linux.amd64
          sudo mv sops-v3.8.1.linux.amd64 /usr/local/bin/sops
          sudo chmod +x /usr/local/bin/sops
          
      - name: Decrypt secrets
        env:
          SOPS_AGE_KEY: ${{ secrets.SOPS_AGE_KEY }}
        run: |
          sops --decrypt secrets.enc.yaml > secrets.yaml
          
      - name: Deploy to Hetzner
        env:
          SSH_PRIVATE_KEY: ${{ secrets.HETZNER_SSH_KEY }}
          DEPLOY_HOST: ${{ secrets.HETZNER_HOST }}
        run: |
          # Setup SSH
          mkdir -p ~/.ssh
          echo "$SSH_PRIVATE_KEY" > ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa
          ssh-keyscan -H $DEPLOY_HOST >> ~/.ssh/known_hosts
          
          # Copy files
          scp docker-compose.prod.yml $DEPLOY_HOST:~/vise/
          scp secrets.yaml $DEPLOY_HOST:~/vise/
          
          # Deploy
          ssh $DEPLOY_HOST << 'ENDSSH'
            cd ~/vise
            export VERSION=${{ github.sha }}
            docker stack deploy -c docker-compose.prod.yml vise
          ENDSSH
          
      - name: Notify Telegram
        if: always()
        run: |
          STATUS="${{ job.status }}"
          MESSAGE="🚀 Deployment $STATUS: ${{ github.sha }}"
          curl -X POST "https://api.telegram.org/bot${{ secrets.TELEGRAM_BOT_TOKEN }}/sendMessage" \
            -d "chat_id=${{ secrets.TELEGRAM_CHAT_ID }}" \
            -d "text=$MESSAGE"
```

---

## Pre-Launch Checklist

### Infrastructure Checklist

```markdown
## 1. Hetzner Setup
- [ ] Account created and verified
- [ ] SSH keys uploaded
- [ ] Private network created (vise-internal)
- [ ] Firewall rules configured
- [ ] API servers provisioned (2x CPX31)
- [ ] Worker servers provisioned (4x CPX41)
- [ ] Database server provisioned (CCX33)
- [ ] Redis servers provisioned (2x CPX21)
- [ ] Monitoring server provisioned (CPX31)
- [ ] Backup server provisioned (Helsinki)
- [ ] Floating IPs assigned
- [ ] Load balancer configured

## 2. Domain & SSL
- [ ] Domain registered/transferred to Cloudflare
- [ ] DNS records configured
- [ ] SSL certificates provisioned (Let's Encrypt)
- [ ] Cloudflare WAF rules configured
- [ ] Page rules configured
- [ ] DDoS protection enabled

## 3. Database
- [ ] PostgreSQL installed
- [ ] Database created
- [ ] User permissions configured
- [ ] Backup schedule configured
- [ ] Replication to Finland enabled
- [ ] Connection pooling (PgBouncer) configured

## 4. Directus
- [ ] Directus installed
- [ ] Admin account created
- [ ] Schema migrated
- [ ] API tokens generated
- [ ] CORS configured
- [ ] Rate limiting enabled

## 5. Redis
- [ ] Redis installed
- [ ] Password configured
- [ ] Persistence enabled (AOF)
- [ ] Master-replica configured
- [ ] Sentinel for failover (optional)

## 6. Monitoring
- [ ] Prometheus installed
- [ ] Grafana installed and configured
- [ ] Loki installed
- [ ] Alertmanager configured
- [ ] Dashboards imported
- [ ] Alert rules configured
```

### Service Accounts Checklist

```markdown
## External Services
- [ ] Bright Data account and API key
- [ ] Oxylabs account and credentials
- [ ] 2Captcha account and API key
- [ ] CapSolver account and API key
- [ ] 5sim account and API key
- [ ] SMSHub account and API key
- [ ] Anthropic account and API key
- [ ] OpenAI account and API key (backup)
- [ ] iyzico merchant account
- [ ] Resend account and API key
- [ ] Sentry project and DSN
- [ ] Telegram bot created
- [ ] Slack webhook configured
- [ ] GitHub organization created
```

### Security Checklist

```markdown
## Security
- [ ] SOPS + age keys generated
- [ ] Secrets encrypted
- [ ] SSH key rotation scheduled
- [ ] Firewall rules audited
- [ ] Admin panel IP restricted
- [ ] Rate limiting configured
- [ ] Audit logging enabled
- [ ] Backup encryption enabled
- [ ] VPN access for admin (optional)
- [ ] 2FA enabled on all service accounts
```

### Testing Checklist

```markdown
## Pre-Production Testing
- [ ] Unit tests passing
- [ ] Integration tests passing
- [ ] E2E tests for each site adapter
- [ ] Load test (100 concurrent bookings)
- [ ] Failover test (kill primary, verify backup)
- [ ] Backup restore test
- [ ] Alert trigger test
- [ ] Payment flow test (sandbox)
- [ ] Email verification test
- [ ] SMS verification test
```

---

## Disaster Recovery

### Backup Strategy

```yaml
backup:
  database:
    frequency: Every 6 hours
    retention: 30 days
    method: pg_dump + upload to Minio
    offsite: Hetzner Finland
    
  redis:
    frequency: Every hour
    retention: 7 days
    method: RDB snapshot
    
  files:
    frequency: Daily
    retention: 30 days
    method: Restic to Minio
    
  configuration:
    frequency: On change
    retention: Forever
    method: Git (encrypted secrets)
```

### Recovery Procedures

```markdown
## Database Recovery
1. Identify latest backup: `ls -la /backups/postgres/`
2. Stop Directus: `docker service scale vise_directus=0`
3. Restore: `pg_restore -d vise_production backup.dump`
4. Verify: `psql -c "SELECT count(*) FROM booking_requests"`
5. Restart: `docker service scale vise_directus=1`

## Full System Recovery
1. Provision new servers (Terraform)
2. Deploy Docker stack
3. Restore database from backup
4. Restore Redis from RDB
5. Update DNS to new IPs
6. Verify all services
7. Monitor for 1 hour
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Hetzner servers provision edilmeli | Infrastructure test |
| AC-002 | Docker stack deploy çalışmalı | Deployment test |
| AC-003 | SSL/TLS certificates valid olmalı | SSL test |
| AC-004 | All API keys functional olmalı | Integration test |
| AC-005 | Monitoring stack operational olmalı | Monitoring test |
| AC-006 | Backup/restore çalışmalı | DR test |
| AC-007 | CI/CD pipeline başarılı olmalı | Pipeline test |

---

## Monthly Operations

```markdown
## Weekly Tasks
- [ ] Review error logs
- [ ] Check account pool health
- [ ] Review proxy success rates
- [ ] Check service balances (SMS, CAPTCHA)
- [ ] Review booking success rates

## Monthly Tasks
- [ ] Rotate SSH keys
- [ ] Update Docker images (security patches)
- [ ] Review and rotate API keys
- [ ] Capacity planning review
- [ ] Cost optimization review
- [ ] Security audit

## Quarterly Tasks
- [ ] Full backup restore test
- [ ] Penetration testing
- [ ] Infrastructure audit
- [ ] Vendor review (pricing, alternatives)
```

---

## Sonraki Adımlar

1. Hetzner hesap açma ve server provisioning
2. Domain transfer ve Cloudflare setup
3. Service hesapları oluşturma
4. Docker images build ve push
5. Production deployment
6. Monitoring setup
7. First customer onboarding
