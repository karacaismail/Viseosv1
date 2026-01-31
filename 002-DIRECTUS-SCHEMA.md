# 002 - Directus Schema Specification

## Amaç

VISE OS platformunun veri katmanını tanımlar. 2000+ acente, günlük 1000 randevu, 200 paralel session destekleyen Directus collection yapıları, field tanımları, role-based access control ve webhook trigger'ları.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 001-ARCHITECTURE-OVERVIEW | Genel sistem mimarisi |
| 003-GOOGLE-SHEETS-TEMPLATE | Applicant data mapping |
| 014-QUEUE-ORCHESTRATOR | Webhook → Queue trigger |
| 019-CREDIT-SYSTEM | Agency credits management |

---

## Collection Yapıları

### 1. agencies (Müşteri Firmalar)

Acentelerin temel bilgileri ve konfigürasyonları.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| status | string | Yes | `active`, `suspended`, `trial` |
| name | string | Yes | Firma adı |
| tursab_no | string | No | TÜRSAB belge numarası |
| contact_name | string | Yes | İletişim kişisi |
| contact_email | string | Yes | E-posta (login) |
| contact_phone | string | Yes | Telefon |
| telegram_chat_id | string | No | Bildirim için Telegram ID |
| discord_webhook | string | No | Bildirim için Discord webhook |
| google_sheet_id | string | No | Bağlı Google Sheet ID |
| google_sheet_sync_enabled | boolean | Yes | Sheets sync aktif mi |
| default_countries | json | No | Tercih edilen ülkeler `["DE", "IT", "FR"]` |
| notification_preferences | json | No | Bildirim ayarları |
| created_at | datetime | Auto | Oluşturma tarihi |
| updated_at | datetime | Auto | Güncelleme tarihi |

**İndeksler:**
- `idx_agencies_status` (status)
- `idx_agencies_email` (contact_email) UNIQUE

---

### 2. agency_credits (Kredi Bakiyeleri)

Ön ödemeli kredi sistemi takibi.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| agency_id | uuid (FK) | Yes | agencies.id |
| total_credits | integer | Yes | Toplam satın alınan kredi |
| used_credits | integer | Yes | Kullanılan kredi |
| reserved_credits | integer | Yes | İşlemdeki (reserved) kredi |
| available_credits | integer | Computed | total - used - reserved |
| last_purchase_at | datetime | No | Son satın alma |
| last_usage_at | datetime | No | Son kullanım |

**Computed Field:**
```sql
available_credits = total_credits - used_credits - reserved_credits
```

**İndeksler:**
- `idx_credits_agency` (agency_id) UNIQUE

---

### 3. credit_transactions (Kredi Hareketleri)

Kredi alım/kullanım audit trail.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| agency_id | uuid (FK) | Yes | agencies.id |
| type | string | Yes | `purchase`, `usage`, `refund`, `reserve`, `release` |
| amount | integer | Yes | Miktar (+ veya -) |
| balance_after | integer | Yes | İşlem sonrası bakiye |
| reference_id | uuid | No | İlişkili booking_request.id |
| description | string | No | Açıklama |
| created_at | datetime | Auto | İşlem tarihi |

**İndeksler:**
- `idx_transactions_agency` (agency_id)
- `idx_transactions_type` (type)
- `idx_transactions_date` (created_at)

---

### 4. applicants (Başvuru Sahipleri - Geçici PII)

**KRITIK: Bu collection PII içerir. Offshore uyumluluk için 24 saat retention.**

| Field | Type | Required | Encrypted | Description |
|-------|------|----------|-----------|-------------|
| id | uuid | Auto | No | Primary key |
| agency_id | uuid (FK) | Yes | No | agencies.id |
| external_ref | string | No | No | Acente'nin kendi referansı |
| status | string | Yes | No | `pending`, `processing`, `completed`, `failed`, `expired` |
| first_name | string | Yes | **YES** | Ad |
| last_name | string | Yes | **YES** | Soyad |
| birth_date | date | Yes | No | Doğum tarihi |
| nationality | string | Yes | No | ISO 3166-1 alpha-2 |
| passport_number | string | Yes | **YES** | Pasaport numarası |
| passport_expiry | date | Yes | No | Pasaport bitiş tarihi |
| phone | string | Yes | **YES** | Telefon (+90...) |
| email | string | No | **YES** | E-posta |
| target_country | string | Yes | No | Hedef ülke kodu |
| target_city | string | No | No | Tercih edilen şehir |
| visa_type | string | Yes | No | Vize tipi kodu |
| preferred_dates | json | No | No | `{"from": "2025-03-01", "to": "2025-04-30"}` |
| exclude_weekends | boolean | No | No | Hafta sonu hariç |
| family_group_id | uuid | No | No | Aile grubu (çocuk bağlama) |
| parent_applicant_id | uuid (FK) | No | No | Ebeveyn applicant (çocuklar için) |
| created_at | datetime | Auto | No | Oluşturma tarihi |
| expires_at | datetime | Yes | No | PII silme zamanı (created + 24h) |
| deleted_at | datetime | No | No | Soft delete timestamp |

**Encryption:**
- AES-256-GCM
- Key: `APPLICANT_ENCRYPTION_KEY` env variable
- IV: Per-field random

**İndeksler:**
- `idx_applicants_agency` (agency_id)
- `idx_applicants_status` (status)
- `idx_applicants_country` (target_country)
- `idx_applicants_expires` (expires_at) - Cleanup job için

**Auto-Cleanup Job:**
```sql
-- Her saat çalışır
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
```

---

### 5. booking_requests (İş Emirleri)

Randevu alma talepleri ve durumları.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| agency_id | uuid (FK) | Yes | agencies.id |
| applicant_id | uuid (FK) | Yes | applicants.id |
| status | string | Yes | Aşağıdaki state machine |
| priority | integer | Yes | 1=urgent, 5=normal, 10=low |
| target_system | string | Yes | `vfs`, `idata`, `bls`, `kkosmos` |
| target_country | string | Yes | Ülke kodu |
| target_location | string | No | Şehir/şube kodu |
| visa_category | string | Yes | Vize kategorisi |
| attempts | integer | Yes | Deneme sayısı (default: 0) |
| max_attempts | integer | Yes | Max deneme (default: 50) |
| last_attempt_at | datetime | No | Son deneme zamanı |
| next_attempt_at | datetime | No | Sonraki deneme zamanı |
| slot_found_count | integer | Yes | Slot bulunup kaçırılan sayısı |
| error_code | string | No | Son hata kodu |
| error_message | string | No | Son hata mesajı |
| assigned_account_id | uuid (FK) | No | Atanan bot hesabı |
| assigned_proxy_id | uuid (FK) | No | Atanan proxy |
| metadata | json | No | Ek bilgiler |
| created_at | datetime | Auto | Oluşturma |
| updated_at | datetime | Auto | Güncelleme |
| completed_at | datetime | No | Tamamlanma |

**Status State Machine:**
```
pending → queued → processing → slot_found → booking → 
  → payment → verifying → completed
  → failed (any state)
  → expired (timeout)
  → cancelled (manual)
```

**İndeksler:**
- `idx_requests_agency` (agency_id)
- `idx_requests_status` (status)
- `idx_requests_priority` (priority, next_attempt_at)
- `idx_requests_system` (target_system, target_country)

---

### 6. booking_results (Sonuçlar - PII Yok)

Tamamlanan randevuların audit kaydı. **PII içermez.**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| agency_id | uuid (FK) | Yes | agencies.id |
| booking_request_id | uuid (FK) | Yes | booking_requests.id |
| status | string | Yes | `success`, `failed`, `cancelled` |
| target_system | string | Yes | `vfs`, `idata`, `bls`, `kkosmos` |
| target_country | string | Yes | Ülke kodu |
| confirmation_number | string | No | Randevu onay numarası (encrypted) |
| appointment_date | date | No | Randevu tarihi |
| appointment_time | time | No | Randevu saati |
| appointment_location | string | No | Randevu lokasyonu |
| total_attempts | integer | Yes | Toplam deneme sayısı |
| total_duration_seconds | integer | Yes | Toplam süre |
| credits_charged | integer | Yes | Kesilen kredi |
| error_code | string | No | Hata kodu (failed için) |
| screenshot_url | string | No | Onay ekran görüntüsü (S3 URL) |
| created_at | datetime | Auto | Oluşturma |

**İndeksler:**
- `idx_results_agency` (agency_id)
- `idx_results_date` (created_at)
- `idx_results_country` (target_country)

---

### 7. bot_accounts (Bot Hesap Havuzu)

VFS/iDATA/BLS hesapları.

| Field | Type | Required | Encrypted | Description |
|-------|------|----------|-----------|-------------|
| id | uuid | Auto | No | Primary key |
| system | string | Yes | No | `vfs`, `idata`, `bls`, `kkosmos` |
| country | string | Yes | No | Ülke kodu |
| email | string | Yes | **YES** | Login email |
| password | string | Yes | **YES** | Login password |
| status | string | Yes | No | `active`, `cooldown`, `banned`, `retired` |
| health_score | integer | Yes | No | 0-100 sağlık puanı |
| success_count | integer | Yes | No | Başarılı işlem sayısı |
| failure_count | integer | Yes | No | Başarısız işlem sayısı |
| consecutive_failures | integer | Yes | No | Ardışık başarısızlık |
| last_used_at | datetime | No | No | Son kullanım |
| cooldown_until | datetime | No | No | Cooldown bitiş |
| ban_detected_at | datetime | No | No | Ban tespit |
| notes | text | No | No | Notlar |
| created_at | datetime | Auto | No | Oluşturma |

**Health Score Calculation:**
```python
base_score = (success_count / (success_count + failure_count)) * 100
penalty = min(consecutive_failures * 10, 50)
health_score = max(0, base_score - penalty)
```

**İndeksler:**
- `idx_accounts_system` (system, country, status)
- `idx_accounts_health` (health_score DESC)
- `idx_accounts_cooldown` (cooldown_until)

---

### 8. proxies (Proxy Havuzu)

Residential proxy tracking.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| provider | string | Yes | `brightdata`, `oxylabs`, `smartproxy` |
| type | string | Yes | `residential`, `mobile`, `datacenter` |
| country | string | Yes | Proxy ülkesi |
| host | string | Yes | Proxy host |
| port | integer | Yes | Proxy port |
| username | string | Yes | Auth username |
| password | string | Yes | Auth password (encrypted) |
| status | string | Yes | `active`, `slow`, `blocked`, `retired` |
| health_score | integer | Yes | 0-100 |
| success_count | integer | Yes | Başarılı request |
| failure_count | integer | Yes | Başarısız request |
| avg_response_ms | integer | No | Ortalama response süresi |
| last_used_at | datetime | No | Son kullanım |
| last_success_at | datetime | No | Son başarılı |
| created_at | datetime | Auto | Oluşturma |

**İndeksler:**
- `idx_proxies_provider` (provider, status)
- `idx_proxies_country` (country, status)
- `idx_proxies_health` (health_score DESC)

---

### 9. browser_profiles (Fingerprint Profilleri)

Unique browser fingerprint konfigürasyonları.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| name | string | Yes | Profile adı |
| status | string | Yes | `active`, `burned`, `retired` |
| user_agent | string | Yes | User agent string |
| viewport_width | integer | Yes | Viewport genişliği |
| viewport_height | integer | Yes | Viewport yüksekliği |
| timezone | string | Yes | Timezone (Europe/Istanbul) |
| locale | string | Yes | Locale (tr-TR) |
| webgl_vendor | string | No | WebGL vendor |
| webgl_renderer | string | No | WebGL renderer |
| canvas_noise | float | No | Canvas noise factor |
| audio_noise | float | No | AudioContext noise |
| fonts | json | No | Available fonts list |
| plugins | json | No | Browser plugins |
| usage_count | integer | Yes | Kullanım sayısı |
| last_used_at | datetime | No | Son kullanım |
| created_at | datetime | Auto | Oluşturma |

**İndeksler:**
- `idx_profiles_status` (status)
- `idx_profiles_usage` (usage_count)

---

### 10. phone_numbers (SMS Verification Pool)

Sanal telefon numaraları.

| Field | Type | Required | Encrypted | Description |
|-------|------|----------|-----------|-------------|
| id | uuid | Auto | No | Primary key |
| provider | string | Yes | No | `5sim`, `smshub`, `twilio` |
| number | string | Yes | **YES** | Telefon numarası |
| country | string | Yes | No | Numara ülkesi |
| status | string | Yes | No | `available`, `in_use`, `cooldown`, `burned` |
| usage_count | integer | Yes | No | Kullanım sayısı |
| max_usage | integer | Yes | No | Max kullanım limiti |
| last_used_at | datetime | No | No | Son kullanım |
| cooldown_until | datetime | No | No | Cooldown bitiş |
| burned_at | datetime | No | No | Ban tarihi |
| created_at | datetime | Auto | No | Oluşturma |

**İndeksler:**
- `idx_phones_status` (status, country)
- `idx_phones_cooldown` (cooldown_until)

---

### 11. payment_cards (Ödeme Kartları)

**KRITIK: Offshore uyumlu encrypted storage.**

| Field | Type | Required | Encrypted | Description |
|-------|------|----------|-----------|-------------|
| id | uuid | Auto | No | Primary key |
| agency_id | uuid (FK) | Yes | No | agencies.id |
| label | string | Yes | No | Kart etiketi (iş kartı, vs) |
| cardholder_name | string | Yes | **YES** | Kart üzerindeki isim |
| card_number | string | Yes | **YES** | Kart numarası |
| expiry_month | string | Yes | **YES** | Son kullanma ay (MM) |
| expiry_year | string | Yes | **YES** | Son kullanma yıl (YY) |
| cvv | string | Yes | **YES** | CVV/CVC |
| card_type | string | No | No | `visa`, `mastercard`, `amex` |
| is_default | boolean | Yes | No | Varsayılan kart mı |
| status | string | Yes | No | `active`, `expired`, `disabled` |
| last_used_at | datetime | No | No | Son kullanım |
| failure_count | integer | Yes | No | Başarısız deneme |
| created_at | datetime | Auto | No | Oluşturma |

**Encryption:**
- AES-256-GCM with separate key: `PAYMENT_ENCRYPTION_KEY`
- PCI-DSS NOT applicable (offshore)
- Decryption only at payment time, memory-only

**İndeksler:**
- `idx_cards_agency` (agency_id)
- `idx_cards_default` (agency_id, is_default)

---

### 12. circuit_breakers (Devre Kesici Durumları)

Per-domain circuit breaker state.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| domain | string | Yes | Domain identifier (vfs_de, idata_it) |
| state | string | Yes | `closed`, `open`, `half_open` |
| failure_count | integer | Yes | Mevcut hata sayısı |
| success_count | integer | Yes | Half-open'da başarı |
| last_failure_at | datetime | No | Son hata zamanı |
| opened_at | datetime | No | Open state geçiş |
| closes_at | datetime | No | Tahmini kapanma |
| metadata | json | No | Ek bilgiler |
| updated_at | datetime | Auto | Güncelleme |

**İndeksler:**
- `idx_cb_domain` (domain) UNIQUE
- `idx_cb_state` (state)

---

### 13. system_logs (Audit Trail)

Sistem olayları. **PII içermez.**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | uuid | Auto | Primary key |
| level | string | Yes | `debug`, `info`, `warning`, `error`, `critical` |
| category | string | Yes | `auth`, `booking`, `payment`, `system` |
| event | string | Yes | Event tipi |
| agency_id | uuid (FK) | No | İlişkili acente |
| booking_request_id | uuid (FK) | No | İlişkili request |
| message | string | Yes | Log mesajı |
| details | json | No | Ek detaylar (PII olmadan) |
| ip_address | string | No | IP adresi |
| user_agent | string | No | User agent |
| created_at | datetime | Auto | Oluşturma |

**İndeksler:**
- `idx_logs_level` (level, created_at)
- `idx_logs_category` (category, created_at)
- `idx_logs_agency` (agency_id, created_at)

**Retention:** 90 gün, sonra archive/delete

---

### 14. api_configurations (Servis API Ayarları)

Directus panelden yönetilen API keys.

| Field | Type | Required | Encrypted | Description |
|-------|------|----------|-----------|-------------|
| id | uuid | Auto | No | Primary key |
| service | string | Yes | No | Servis adı |
| config_key | string | Yes | No | Config anahtarı |
| config_value | string | Yes | **YES** | Config değeri |
| is_active | boolean | Yes | No | Aktif mi |
| notes | text | No | No | Notlar |
| updated_at | datetime | Auto | No | Güncelleme |
| updated_by | uuid (FK) | No | No | Güncelleyen user |

**Örnek Kayıtlar:**
```
service: "captcha", config_key: "2captcha_api_key", config_value: "[encrypted]"
service: "captcha", config_key: "capsolver_api_key", config_value: "[encrypted]"
service: "proxy", config_key: "brightdata_username", config_value: "[encrypted]"
service: "proxy", config_key: "brightdata_password", config_value: "[encrypted]"
service: "sms", config_key: "5sim_api_key", config_value: "[encrypted]"
service: "ai", config_key: "openai_api_key", config_value: "[encrypted]"
service: "notification", config_key: "telegram_bot_token", config_value: "[encrypted]"
```

---

## Role-Based Access Control (RBAC)

### Roller

| Role | Açıklama |
|------|----------|
| `super_admin` | Tam sistem erişimi |
| `agency_admin` | Acente yöneticisi |
| `agency_operator` | Acente operatörü |
| `agency_viewer` | Salt okuma |
| `system_bot` | API-only bot user |

### Permission Matrix

| Collection | super_admin | agency_admin | agency_operator | agency_viewer | system_bot |
|------------|-------------|--------------|-----------------|---------------|------------|
| agencies | CRUD | R (own) | R (own) | R (own) | R |
| agency_credits | CRUD | R (own) | R (own) | R (own) | RU |
| credit_transactions | CRUD | R (own) | R (own) | R (own) | C |
| applicants | CRUD | CRUD (own) | CRU (own) | R (own) | CRUD |
| booking_requests | CRUD | CRUD (own) | CRU (own) | R (own) | CRUD |
| booking_results | CRUD | R (own) | R (own) | R (own) | CR |
| bot_accounts | CRUD | - | - | - | RU |
| proxies | CRUD | - | - | - | RU |
| browser_profiles | CRUD | - | - | - | RU |
| phone_numbers | CRUD | - | - | - | RU |
| payment_cards | CRUD | CRUD (own) | - | - | R |
| circuit_breakers | CRUD | R | R | R | RU |
| system_logs | CRUD | R (own) | R (own) | R (own) | C |
| api_configurations | CRUD | - | - | - | R |

**Filter Rules (agency_* roles):**
```javascript
// Sadece kendi agency verilerini görebilir
{
  "agency_id": {
    "_eq": "$CURRENT_USER.agency_id"
  }
}
```

---

## Webhook Triggers

### 1. applicant.items.create

**Trigger:** Yeni applicant oluşturulduğunda
**Action:** booking_requests tablosuna iş emri oluştur

```javascript
// Webhook payload → Queue
{
  "event": "applicant.items.create",
  "payload": {
    "id": "applicant_uuid",
    "agency_id": "agency_uuid",
    "target_country": "DE",
    "visa_type": "tourist"
  }
}
```

### 2. booking_requests.items.update (status → completed)

**Trigger:** Randevu başarıyla alındığında
**Actions:**
1. Credit deduct (reserved → used)
2. Google Sheets row delete/archive
3. Telegram notification
4. applicant PII cleanup schedule

### 3. booking_requests.items.update (status → failed)

**Trigger:** Randevu başarısız
**Actions:**
1. Credit release (reserved → available)
2. Error notification
3. Retry scheduling (if retriable)

### 4. agency_credits.items.update (available_credits < 10)

**Trigger:** Düşük kredi uyarısı
**Action:** Telegram/Email notification

### 5. bot_accounts.items.update (status → banned)

**Trigger:** Hesap ban tespit
**Actions:**
1. Alert notification
2. Circuit breaker update
3. Account rotation trigger

---

## Directus Extensions

### 1. Custom Endpoint: /items/booking-stats

```javascript
// GET /items/booking-stats?agency_id=xxx&range=7d
{
  "total_requests": 150,
  "completed": 120,
  "failed": 20,
  "pending": 10,
  "success_rate": 0.857,
  "avg_duration_seconds": 145,
  "credits_used": 120,
  "by_country": {
    "DE": { "completed": 50, "failed": 5 },
    "IT": { "completed": 40, "failed": 10 },
    "FR": { "completed": 30, "failed": 5 }
  }
}
```

### 2. Custom Endpoint: /items/health-check

```javascript
// GET /items/health-check
{
  "status": "healthy",
  "components": {
    "database": "ok",
    "redis": "ok",
    "celery": "ok",
    "proxy_pool": { "status": "ok", "active": 45, "total": 50 },
    "account_pool": { "status": "warning", "active": 80, "cooldown": 15, "banned": 5 },
    "circuit_breakers": {
      "vfs_de": "closed",
      "vfs_fr": "half_open",
      "idata_it": "closed"
    }
  },
  "timestamp": "2025-02-01T12:00:00Z"
}
```

### 3. Custom Hook: PII Auto-Cleanup

```javascript
// Her saat çalışan scheduled hook
module.exports = function registerHook({ schedule }) {
  schedule('0 * * * *', async () => {
    await database.raw(`
      UPDATE applicants 
      SET 
        first_name = '[REDACTED]',
        last_name = '[REDACTED]',
        passport_number = '[REDACTED]',
        phone = '[REDACTED]',
        email = '[REDACTED]',
        deleted_at = NOW()
      WHERE expires_at < NOW() 
        AND deleted_at IS NULL
    `);
  });
};
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Tüm collection'lar migration ile oluşturulabilmeli | Schema migration test |
| AC-002 | Encryption field'lar decrypt edilebilmeli | Unit test |
| AC-003 | RBAC filter'ları doğru çalışmalı | Permission test |
| AC-004 | Webhook'lar doğru event'lerde trigger olmalı | Integration test |
| AC-005 | PII cleanup job 24h+ kayıtları silmeli | Scheduled job test |
| AC-006 | Health check endpoint tüm component'ları raporlamalı | API test |
| AC-007 | Credit transaction atomik olmalı | Concurrent test |

---

## Edge Cases

### Concurrent Credit Deduction

```sql
-- Atomic credit operation with row locking
BEGIN;
SELECT * FROM agency_credits 
WHERE agency_id = $1 
FOR UPDATE;

-- Check sufficient credits
-- Deduct
-- Commit or rollback
COMMIT;
```

### Applicant Expires While Processing

```
IF applicant.expires_at < NOW() AND booking_request.status IN ('processing', 'slot_found'):
  - DO NOT redact PII yet
  - Extend expires_at by 1 hour
  - Complete or fail first, then cleanup
```

### Circuit Breaker Race Condition

```
- Use Redis distributed lock for circuit breaker state updates
- TTL: 5 seconds
- Retry with backoff if lock fails
```

---

## Güvenlik Notları

1. **Encryption Keys:** Environment variable, asla DB'de veya kodda
2. **PII Fields:** AES-256-GCM, field-level encryption
3. **Payment Data:** Ayrı encryption key, memory-only decrypt
4. **Audit Logs:** PII reference sadece UUID, asla raw data
5. **API Access:** Rate limiting per agency (100 req/min)
6. **Webhook Secrets:** HMAC signature validation

---

## Sonraki Adımlar

Bu schema onaylandıktan sonra:
1. **003-GOOGLE-SHEETS-TEMPLATE.md** - Input format ve validation
2. **Directus Migration Script** - Schema creation
3. **Seed Data** - Test accounts, sample agencies
