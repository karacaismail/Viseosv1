# 001 - VISE OS Architecture Overview

## Amaç

Türkiye'deki 2000+ vize acentesine hizmet verecek, günlük 1000 randevu kapasiteli, 200 paralel session destekleyen, VFS Global / iDATA / BLS Spain / KKOSMOS sistemlerinde tam otomatik vize randevusu alan enterprise-grade otomasyon platformu. Offshore şirket yapısına uygun, KVKK/GDPR kapsam dışı mimari.

---

## Sistem Bileşenleri (High-Level)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CUSTOMER LAYER                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  Google Sheets        │  Directus Panel       │  Telegram/Discord Bot       │
│  (Data Input)         │  (Admin & Monitoring) │  (Notifications)            │
└───────────┬───────────┴───────────┬───────────┴───────────┬─────────────────┘
            │                       │                       │
            ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API GATEWAY (FastAPI)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  Authentication  │  Rate Limiting  │  Webhook Handler  │  Credit Validator  │
└───────────┬──────┴────────┬────────┴────────┬──────────┴────────┬───────────┘
            │               │                 │                   │
            ▼               ▼                 ▼                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ORCHESTRATION LAYER                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Celery     │  │    Redis     │  │  PostgreSQL  │  │   AI Agent   │     │
│  │   Beat       │  │    Queue     │  │   State DB   │  │   Decision   │     │
│  │  (Scheduler) │  │  (Task Mgmt) │  │              │  │   Engine     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘     │
└───────────┬──────────────┬──────────────┬──────────────────┬────────────────┘
            │              │              │                  │
            ▼              ▼              ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RESOURCE POOL LAYER                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Account    │  │    Proxy     │  │   Browser    │  │   Phone      │     │
│  │   Pool       │  │    Pool      │  │   Profile    │  │   Number     │     │
│  │   Manager    │  │   Manager    │  │   Manager    │  │   Pool       │     │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘     │
└───────────┬──────────────┬──────────────┬──────────────────┬────────────────┘
            │              │              │                  │
            ▼              ▼              ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AUTOMATION LAYER                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Stealth     │  │   CAPTCHA    │  │   Payment    │  │  Verification│     │
│  │  Browser     │  │   Solver     │  │   Processor  │  │  Handler     │     │
│  │  Engine      │  │   Chain      │  │              │  │  (Email/SMS) │     │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘     │
└───────────┬──────────────┬──────────────┬──────────────────┬────────────────┘
            │              │              │                  │
            ▼              ▼              ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SITE ADAPTER LAYER                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │     VFS      │  │    iDATA     │  │  BLS Spain   │  │   KKOSMOS    │     │
│  │   Adapter    │  │   Adapter    │  │   Adapter    │  │   Adapter    │     │
│  │  (Cloudflare)│  │  (jQuery API)│  │ (Keyboard)   │  │  (SMS Call)  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
            │              │              │                  │
            ▼              ▼              ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL SERVICES                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Bright Data  │  2Captcha   │  5sim.net    │  Mailcow     │  OpenAI API    │
│  (Proxy)      │  CapSolver  │  Twilio      │  (Email)     │  Claude API    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Bağımlılıklar

| Bileşen | Bağımlı Olduğu | Açıklama |
|---------|----------------|----------|
| API Gateway | Directus, Redis | Auth token validation, rate limit state |
| Orchestration | PostgreSQL, Redis | Job state, queue management |
| Automation | Proxy Pool, Account Pool | Her session'a resource allocation |
| Site Adapters | Stealth Engine, CAPTCHA Solver | Browser instance + challenge bypass |
| Payment | Account Pool, Stealth Engine | Authenticated session + card data |
| AI Decision | All Adapters, Monitoring | Cross-system anomaly detection |

---

## Data Flow

### 1. Müşteri Data Girişi (Google Sheets → System)

```
[Acente Google Sheets]
    │
    │ (Google Sheets API - 5 dakika polling)
    ▼
[Sheets Sync Service]
    │
    │ Validation + Normalization
    ▼
[Directus: applicants collection]
    │
    │ status: "pending"
    ▼
[Queue: booking_requests]
```

### 2. Randevu Arama ve Booking Flow

```
[Celery Beat: Her 60-300 saniye]
    │
    ▼
[AI Decision Engine]
    │
    │ "Hangi ülke/sistem şu an müsait?"
    │ "Hangi account/proxy kullanılmalı?"
    ▼
[Resource Allocation]
    │
    ├── Account Pool → En sağlıklı hesap
    ├── Proxy Pool → Fresh residential IP
    └── Browser Profile → Unique fingerprint
    │
    ▼
[Site Adapter: VFS/iDATA/BLS/KKOSMOS]
    │
    ├── LOGIN ──────────────────────────┐
    │   ├── Cloudflare bypass           │
    │   ├── CAPTCHA solve               │
    │   └── Session establish           │
    │                                   │
    ├── SLOT CHECK ─────────────────────┤
    │   ├── Date range filter           │
    │   ├── Availability detection      │
    │   └── Slot lock attempt           │
    │                                   │
    ├── BOOKING ────────────────────────┤
    │   ├── Form fill (applicant data)  │
    │   ├── Slot selection              │
    │   └── Pre-payment validation      │
    │                                   │
    ├── PAYMENT ────────────────────────┤
    │   ├── Card data injection         │
    │   ├── 3DS handling (if needed)    │
    │   └── Confirmation capture        │
    │                                   │
    └── VERIFICATION ───────────────────┤
        ├── Email confirmation          │
        └── SMS code (if needed)        │
    │
    ▼
[Success/Failure Handler]
    │
    ├── SUCCESS:
    │   ├── Directus: status → "completed"
    │   ├── Credit deduct (100 krediden 1)
    │   ├── Google Sheets: row delete/archive
    │   └── Telegram: success notification
    │
    └── FAILURE:
        ├── Error classification
        ├── Retry queue (if retriable)
        ├── Account cooldown (if banned)
        └── Alert (if critical)
```

### 3. Ban Recovery Flow

```
[Detection: Account Banned]
    │
    ▼
[Account Pool Manager]
    │
    ├── cooldown_until = now + 600  (10 dk)
    ├── health_score -= 20
    ├── consecutive_failures += 1
    │
    ▼
[Circuit Breaker Check]
    │
    ├── IF failures < 5:
    │   └── HALF_OPEN: 10 dk sonra tekrar dene
    │
    └── IF failures >= 5:
        └── OPEN: 2 saat cooldown
    │
    ▼
[Fallback: Yeni account/proxy ile devam]
```

---

## Gereksinimler

### Fonksiyonel Gereksinimler

| ID | Gereksinim | Öncelik |
|----|------------|---------|
| FR-001 | Google Sheets'ten applicant data okuma ve senkronizasyon | Kritik |
| FR-002 | Directus panel üzerinden tüm operasyonları izleme | Kritik |
| FR-003 | VFS Global'de Cloudflare bypass ile login ve booking | Kritik |
| FR-004 | iDATA API üzerinden slot check ve booking | Kritik |
| FR-005 | BLS Spain'de keyboard-only form doldurma | Yüksek |
| FR-006 | KKOSMOS'ta SMS verification handling | Orta |
| FR-007 | Otomatik ödeme işlemi (kart bilgisi ile) | Kritik |
| FR-008 | Email verification auto-click | Yüksek |
| FR-009 | SMS verification code extraction | Yüksek |
| FR-010 | Ön ödemeli kredi sistemi (100 işlem paketi) | Kritik |
| FR-011 | Başarılı işlem sonrası Sheets data silme | Kritik |
| FR-012 | Real-time status update (Directus + Telegram) | Yüksek |
| FR-013 | 200 paralel session desteği | Kritik |
| FR-014 | Günlük 1000 randevu kapasitesi | Kritik |

### Non-Fonksiyonel Gereksinimler

| ID | Gereksinim | Metrik |
|----|------------|--------|
| NFR-001 | Uptime | >= 99.5% |
| NFR-002 | Booking başarı oranı | >= 85% (overall), >= 95% (slot bulunduğunda) |
| NFR-003 | Ortalama booking süresi | < 3 dakika (slot'tan completion'a) |
| NFR-004 | Ban recovery süresi | < 30 dakika |
| NFR-005 | Concurrent sessions | 200 (peak), 50 (normal) |
| NFR-006 | Data retention | 0 (işlem sonrası applicant PII silinir) |

---

## Teknik Tasarım Kararları

### 1. Neden Directus (Frappe Değil)?

| Kriter | Directus | Frappe |
|--------|----------|--------|
| Headless API | Native REST/GraphQL | ERPNext odaklı |
| Custom logic | Hooks + Extensions | DocType bağımlı |
| Offshore uyum | Kolay (minimal footprint) | Karmaşık |
| Öğrenme eğrisi | Düşük | Yüksek |
| Lisans | GPL-3.0 (self-host free) | GPL-3.0 |

### 2. Browser Engine Seçimi

**Primary: Camoufox + Playwright**
- C++ seviye fingerprint modification
- Hook detection imkansız
- Native Playwright API

**Fallback: Patchright**
- Playwright fork, undetected patches
- Daha az maintenance

**Emergency: SeleniumBase UC Mode**
- Daha bilinen, daha fazla detection riski
- Son çare

### 3. Queue Architecture

```
Redis Streams (Primary Queue)
    │
    ├── high_priority    ← Slot bulundu, hemen book et
    ├── normal           ← Standart booking request
    ├── retry            ← Failed jobs, exponential backoff
    └── scheduled        ← Gece batch jobs
```

**Celery Workers:**
- `worker_vfs` × 10 (VFS dedicated)
- `worker_idata` × 5 (iDATA dedicated)
- `worker_bls` × 3 (BLS dedicated)
- `worker_generic` × 5 (overflow)

### 4. Database Schema (High-Level)

```
PostgreSQL
├── agencies              # Müşteri firmalar
├── agency_credits        # Kredi bakiyeleri
├── applicants            # Başvuru sahipleri (geçici, PII)
├── booking_requests      # İş emirleri
├── booking_results       # Sonuçlar (PII olmadan)
├── accounts              # VFS/iDATA hesap havuzu
├── proxies               # Proxy health tracking
├── browser_profiles      # Fingerprint configs
├── phone_numbers         # SMS verification pool
├── system_logs           # Audit trail
└── circuit_breakers      # Per-domain state
```

### 5. Offshore Uyumlu Data Handling

```
[Applicant Data Lifecycle]

1. INPUT
   Google Sheets → Encrypted transit → PostgreSQL (AES-256)
   
2. PROCESSING
   Memory-only decryption → Browser automation → Clear on complete
   
3. DELETION
   Success → Immediate PII wipe (name, passport, card)
   Failure → 24h retention for retry, then wipe
   
4. AUDIT
   booking_results tablosunda sadece:
   - booking_id
   - agency_id
   - country
   - status
   - timestamp
   - error_code (if failed)
   
   NO PII RETAINED
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Google Sheets'ten data 5 dk içinde sync olmalı | Integration test |
| AC-002 | VFS login %90+ başarı oranı (Cloudflare bypass) | Load test (100 attempt) |
| AC-003 | Slot bulunduğunda booking %95+ başarı | Staging environment |
| AC-004 | Ban sonrası 10 dk recovery çalışmalı | Chaos test |
| AC-005 | 200 concurrent session, no crash | Stress test |
| AC-006 | Credit deduct sadece success'te | Unit test |
| AC-007 | Applicant PII 24h içinde silinmeli | Audit script |
| AC-008 | Telegram notification <30 saniye | E2E test |

---

## Edge Cases ve Recovery

### Senaryo 1: Slot Görünüyor Ama Booking'de Kayboluyor

```
Trigger: SELECTING_SLOT → HTTP 200 ama slot listede yok
Action:
  1. slot_lost_counter++
  2. IF counter < 3: Aynı date range tekrar ara
  3. IF counter >= 3: Date range genişlet
  4. IF counter >= 5: Farklı şehir/lokasyon dene
  5. IF counter >= 10: Human escalation
```

### Senaryo 2: CAPTCHA Loop (Sürekli Challenge)

```
Trigger: 3+ ardışık CAPTCHA request
Action:
  1. Current provider'dan farklı provider'a geç
  2. Browser profile rotate
  3. Proxy rotate (farklı ASN)
  4. 5 dakika cooldown
  5. IF devam ederse: Account retire, yeni account
```

### Senaryo 3: Payment 3DS Timeout

```
Trigger: 3DS sayfası 60 saniye içinde complete olmadı
Action:
  1. Screenshot capture (debug için)
  2. Session terminate (partial booking önle)
  3. 5 dakika sonra retry (slot hala varsa)
  4. IF 3x fail: Alert + manual review queue
```

### Senaryo 4: Email/SMS Verification Timeout

```
Trigger: 2 dakika içinde verification tamamlanmadı
Action:
  EMAIL:
    1. Spam folder check
    2. 30 saniye daha bekle
    3. IF timeout: Session'ı pause, human alert
    
  SMS:
    1. Number'ı "slow" olarak mark
    2. Yeni number ile retry
    3. IF 3x fail: Müşterinin kendi numarası fallback
```

### Senaryo 5: Gece Açılan Randevular

```
Trigger: Celery Beat, 00:00-06:00 arası daha agresif polling
Action:
  1. Polling interval: 60 saniye (normal 300)
  2. Pre-warmed browser sessions (10 adet hazır)
  3. High-priority queue'ya instant push
  4. First-come-first-serve: Slot bulan ilk worker kitler
```

---

## Güvenlik Notları

### Offshore Uyumluluk Checklist

- [ ] Şirket KVKK/GDPR dışı jurisdiksiyon'da kurulu
- [ ] Sunucular aynı jurisdiksiyon'da veya data processing agreement'sız ülkede
- [ ] Applicant PII encrypted at rest (AES-256)
- [ ] PII retention: maksimum 24 saat
- [ ] Kart bilgisi: PCI-DSS gerektirmeyen offshore processor veya encrypted storage
- [ ] Audit log'da PII yok, sadece booking_id reference
- [ ] Data export endpoint yok (GDPR right-to-access N/A)

### Erişim Kontrolü

```
Directus Roles:
├── super_admin     # Tüm erişim, system config
├── agency_admin    # Kendi firmasının verileri + kredi
├── agency_viewer   # Read-only kendi firma
└── system_bot      # API-only, no UI access
```

### Secret Management

```
Environment Variables (Runtime):
├── DATABASE_URL
├── REDIS_URL
├── DIRECTUS_KEY
├── OPENAI_API_KEY
├── CAPTCHA_API_KEYS (JSON array)
├── PROXY_API_KEYS (JSON array)
├── PAYMENT_ENCRYPTION_KEY
└── SMS_PROVIDER_KEYS (JSON array)

NEVER in codebase, NEVER in logs.
```

---

## Sonraki Adımlar

Bu doküman onaylandıktan sonra sırasıyla:

1. **002-DIRECTUS-SCHEMA.md** - Collection ve field tanımları
2. **003-GOOGLE-SHEETS-TEMPLATE.md** - Input format ve validation
3. **004-STEALTH-ENGINE.md** - Browser automation core

---

## Referanslar

- Araştırma: VFS Global/iDATA Anti-Bot Mekanizmaları (proje dosyaları)
- vize_taksim.md: Saha notları ve manuel operasyon insight'ları
- Auto-Claude-workflow.md: Spec format ve development guidelines
