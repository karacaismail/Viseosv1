# 003 - Google Sheets Template Specification

## Amaç

Acentelerin müşteri verilerini sisteme aktarması için standart Google Sheets şablonu. Field mapping, validation rules, auto-sync ve işlem sonrası data cleanup mekanizmaları.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 001-ARCHITECTURE-OVERVIEW | Data flow tanımı |
| 002-DIRECTUS-SCHEMA | applicants collection mapping |
| 019-CREDIT-SYSTEM | Kredi kontrolü |

---

## Sheet Yapısı

### Ana Sheet: "Başvurular"

Her satır bir başvuru sahibini temsil eder.

| Sütun | Header | Tip | Zorunlu | Validation | Örnek |
|-------|--------|-----|---------|------------|-------|
| A | REF_NO | string | Hayır | Boş veya alfanumerik | ACM-2025-001 |
| B | AD | string | Evet | Min 2 karakter, sadece harf | Ahmet |
| C | SOYAD | string | Evet | Min 2 karakter, sadece harf | Yılmaz |
| D | DOGUM_TARIHI | date | Evet | DD.MM.YYYY, > 1900, < bugün | 15.03.1985 |
| E | UYRUK | string | Evet | ISO 3166-1 alpha-2 | TR |
| F | PASAPORT_NO | string | Evet | 7-9 karakter alfanumerik | U12345678 |
| G | PASAPORT_BITIS | date | Evet | DD.MM.YYYY, > bugün + 3 ay | 15.03.2030 |
| H | TELEFON | string | Evet | +90 ile başlayan, 12-13 hane | +905551234567 |
| I | EMAIL | string | Hayır | Valid email format | ahmet@email.com |
| J | HEDEF_ULKE | string | Evet | Dropdown: DE, IT, FR, NL, ES, NO, SE, GR | DE |
| K | HEDEF_SEHIR | string | Hayır | Dropdown: ülkeye göre dinamik | Istanbul |
| L | VIZE_TIPI | string | Evet | Dropdown: ülkeye göre dinamik | tourist |
| M | TARIH_BASLANGIC | date | Hayır | DD.MM.YYYY, >= bugün | 01.03.2025 |
| N | TARIH_BITIS | date | Hayır | DD.MM.YYYY, > TARIH_BASLANGIC | 30.04.2025 |
| O | HAFTA_SONU_HARIC | boolean | Hayır | Checkbox | TRUE |
| P | AILE_GRUP_ID | string | Hayır | UUID veya boş | family-001 |
| Q | EBEVEYN_REF | string | Hayır | Başka satırın REF_NO'su | ACM-2025-001 |
| R | ONCELIK | integer | Hayır | 1-10 arası, default 5 | 5 |
| S | NOTLAR | string | Hayır | Max 500 karakter | Premium müşteri |
| T | STATUS | string | Auto | Sistem tarafından güncellenir | pending |
| U | ISLEM_ID | string | Auto | Booking request UUID | - |
| V | SONUC | string | Auto | Başarılı/Başarısız mesajı | - |
| W | RANDEVU_TARIHI | date | Auto | Alınan randevu tarihi | - |
| X | RANDEVU_SAATI | time | Auto | Alınan randevu saati | - |
| Y | ONAY_NO | string | Auto | Randevu onay numarası | - |
| Z | SON_GUNCELLEME | datetime | Auto | Son güncelleme zamanı | - |

---

## Dropdown Değerleri

### J: HEDEF_ULKE

```
DE - Almanya (VFS/iDATA)
IT - İtalya (VFS/iDATA)
FR - Fransa (VFS)
NL - Hollanda (VFS)
ES - İspanya (BLS)
NO - Norveç (VFS)
SE - İsveç (VFS)
GR - Yunanistan (KKOSMOS)
```

### K: HEDEF_SEHIR (Dinamik - Ülkeye Göre)

**Almanya (DE):**
```
Istanbul - İstanbul
Ankara - Ankara
Izmir - İzmir
Antalya - Antalya
Gaziantep - Gaziantep
```

**İtalya (IT):**
```
Istanbul - İstanbul
Ankara - Ankara
Izmir - İzmir
```

**Fransa (FR):**
```
Istanbul_Beyoglu - İstanbul Beyoğlu
Istanbul_Altunizade - İstanbul Altunizade
Ankara - Ankara
Izmir - İzmir
```

**Hollanda (NL):**
```
Istanbul_Altunizade - İstanbul Altunizade
Ankara - Ankara
```

### L: VIZE_TIPI (Dinamik - Ülkeye Göre)

**Almanya (DE):**
```
tourist - Turist
business - İş
family - Aile Ziyareti
student - Öğrenci
medical - Sağlık
conference - Konferans
```

**İtalya (IT):**
```
tourist - Turist
business - İş
family - Aile Ziyareti
student - Öğrenci
elective_residence - İkamet
```

**Fransa (FR):**
```
tourist - Turist
business - İş (Ticari)
business_pro - İş (Profesyonel)
family - Aile Ziyareti
student - Öğrenci
transit - Transit
```

---

## Validation Rules (Data Validation)

### A: REF_NO
```
Custom formula: =OR(ISBLANK(A2), REGEXMATCH(A2, "^[A-Za-z0-9\-]+$"))
Error message: "Referans numarası sadece harf, rakam ve tire içerebilir"
```

### B-C: AD, SOYAD
```
Custom formula: =AND(LEN(B2)>=2, REGEXMATCH(B2, "^[A-Za-zÇçĞğİıÖöŞşÜü\s]+$"))
Error message: "İsim en az 2 karakter olmalı ve sadece harf içermeli"
```

### D: DOGUM_TARIHI
```
Custom formula: =AND(ISNUMBER(D2), D2>DATE(1900,1,1), D2<TODAY())
Error message: "Geçerli bir doğum tarihi girin (DD.MM.YYYY)"
```

### F: PASAPORT_NO
```
Custom formula: =AND(LEN(F2)>=7, LEN(F2)<=9, REGEXMATCH(F2, "^[A-Z0-9]+$"))
Error message: "Pasaport numarası 7-9 karakter ve büyük harf/rakam olmalı"
```

### G: PASAPORT_BITIS
```
Custom formula: =AND(ISNUMBER(G2), G2>TODAY()+90)
Error message: "Pasaport bitiş tarihi en az 3 ay sonra olmalı"
```

### H: TELEFON
```
Custom formula: =REGEXMATCH(H2, "^\+90[0-9]{10}$")
Error message: "Telefon +905XXXXXXXXX formatında olmalı"
```

### I: EMAIL
```
Custom formula: =OR(ISBLANK(I2), REGEXMATCH(I2, "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"))
Error message: "Geçerli bir email adresi girin"
```

### M-N: TARIH_BASLANGIC, TARIH_BITIS
```
Custom formula for N: =OR(ISBLANK(N2), N2>M2)
Error message: "Bitiş tarihi başlangıç tarihinden sonra olmalı"
```

---

## Conditional Formatting

### STATUS Sütunu (T) Renklendirme

| Status | Arka Plan | Yazı |
|--------|-----------|------|
| pending | #FFF3CD (sarı) | #856404 |
| queued | #CCE5FF (mavi) | #004085 |
| processing | #D4EDDA (yeşil) | #155724 |
| completed | #28A745 (koyu yeşil) | #FFFFFF |
| failed | #F8D7DA (kırmızı) | #721C24 |
| expired | #6C757D (gri) | #FFFFFF |

### Validation Hata Satırları

```
Format: Tüm satır kırmızı border
Condition: Herhangi bir zorunlu alan boş veya hatalı
```

---

## Sheet: "Ayarlar"

Acente konfigürasyonları.

| Hücre | Açıklama | Değer |
|-------|----------|-------|
| A1 | Başlık | "AYARLAR" |
| A2 | Acente ID | [Directus'tan atanan UUID] |
| A3 | API Key | [Auto-generated, read-only] |
| A4 | Varsayılan Ülke | DE |
| A5 | Varsayılan Şehir | Istanbul |
| A6 | Varsayılan Vize Tipi | tourist |
| A7 | Hafta Sonu Hariç | TRUE |
| A8 | Telegram Chat ID | [Acente Telegram ID] |
| A9 | Son Sync | [Auto-updated timestamp] |
| A10 | Kalan Kredi | [Auto-updated from Directus] |

---

## Sheet: "Vize Tipleri"

Referans tablosu (hidden sheet).

| Ülke | Kod | Açıklama | Sistem |
|------|-----|----------|--------|
| DE | tourist | Turist | idata |
| DE | business | İş | idata |
| IT | tourist | Turist | idata |
| IT | student | Öğrenci | idata |
| FR | tourist | Turist | vfs |
| FR | business | İş (Ticari) | vfs |
| ... | ... | ... | ... |

---

## Sheet: "Geçmiş"

Tamamlanan işlemlerin arşivi (auto-populated).

| Sütun | Header | Açıklama |
|-------|--------|----------|
| A | TARIH | İşlem tamamlanma tarihi |
| B | REF_NO | Acente referansı |
| C | AD_SOYAD | İsim (maskelenmiş: A*** Y***) |
| D | ULKE | Hedef ülke |
| E | SONUC | Başarılı/Başarısız |
| F | RANDEVU | Randevu tarihi |
| G | ONAY_NO | Onay numarası |
| H | KREDI | Kullanılan kredi |

**Retention:** 90 gün, sonra auto-delete

---

## Sync Mekanizması

### Google Sheets API Flow

```
[Celery Beat: Her 5 dakika]
    │
    ▼
[Sheets Sync Service]
    │
    ├── 1. Google Sheets API ile bağlan
    │      - Service Account authentication
    │      - Sheet ID: agencies.google_sheet_id
    │
    ├── 2. "Başvurular" sheet'ini oku
    │      - A2:S{last_row}
    │      - STATUS = "pending" veya boş olanlar
    │
    ├── 3. Her satır için:
    │      ├── Validation check
    │      ├── IF valid:
    │      │   ├── Directus: applicants.create()
    │      │   ├── Directus: booking_requests.create()
    │      │   ├── Credit reserve (1 kredi)
    │      │   └── Sheets: STATUS = "queued", ISLEM_ID = uuid
    │      └── IF invalid:
    │          └── Sheets: STATUS = "validation_error", SONUC = hata mesajı
    │
    └── 4. Log sync sonucu
```

### Status Update Flow (Directus → Sheets)

```
[Webhook: booking_requests.items.update]
    │
    ▼
[Sheets Update Service]
    │
    ├── Find row by ISLEM_ID (U sütunu)
    │
    ├── Update columns:
    │   ├── T (STATUS) = new status
    │   ├── V (SONUC) = result message
    │   ├── W (RANDEVU_TARIHI) = appointment date
    │   ├── X (RANDEVU_SAATI) = appointment time
    │   ├── Y (ONAY_NO) = confirmation number
    │   └── Z (SON_GUNCELLEME) = timestamp
    │
    └── IF status == "completed":
        ├── Copy row to "Geçmiş" sheet (masked)
        └── Clear row from "Başvurular" (after 1 hour)
```

---

## Data Cleanup Flow

### Başarılı İşlem Sonrası

```
[Status: completed]
    │
    ├── T+0: Status update, notification sent
    │
    ├── T+1h: Row moved to "Geçmiş" (masked)
    │
    └── T+2h: Original row deleted from "Başvurular"
```

### Başarısız İşlem

```
[Status: failed]
    │
    ├── Retriable error:
    │   └── STATUS remains, retry scheduled
    │
    └── Non-retriable error:
        ├── T+24h: Row archived to "Geçmiş"
        └── T+25h: Row deleted from "Başvurular"
```

### Manuel Cleanup Trigger

```
Sheets Menu: VISE OS → Tamamlananları Temizle
  - Tüm "completed" satırları arşivle ve sil
  - Tüm "failed" (24h+) satırları arşivle ve sil
```

---

## Alternatif: XLSX Upload

Bazı acenteler Google Sheets kullanmak istemeyebilir. Directus üzerinden XLSX upload desteği:

### Upload Endpoint

```
POST /items/applicants/import
Content-Type: multipart/form-data

file: [xlsx_file]
agency_id: [uuid]
```

### XLSX Format

Aynı sütun yapısı (A-S), ilk satır header.

### Processing Flow

```
[XLSX Upload]
    │
    ├── 1. Parse with openpyxl
    ├── 2. Validate all rows
    ├── 3. IF all valid:
    │      ├── Bulk insert to applicants
    │      ├── Bulk insert to booking_requests
    │      └── Delete original file immediately
    └── 4. IF any invalid:
           └── Return error report (row numbers + errors)
```

### Security

```
- File size limit: 5MB
- Max rows: 500
- Supported formats: .xlsx only (no .xls, no macros)
- Virus scan before processing
- File deleted after processing (success or fail)
- No file retention
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Tüm validation rule'ları çalışmalı | Manual test |
| AC-002 | Dropdown'lar dinamik güncellenmeli | Integration test |
| AC-003 | Sync 5 dk içinde gerçekleşmeli | Timing test |
| AC-004 | Status renklendirme doğru çalışmalı | Visual test |
| AC-005 | Completed satırlar 2h içinde silinmeli | Scheduled test |
| AC-006 | XLSX upload validasyonu çalışmalı | Unit test |
| AC-007 | Geçmiş sheet'te PII maskelenmiş olmalı | Data audit |

---

## Edge Cases

### Duplicate Detection

```
Aynı PASAPORT_NO + HEDEF_ULKE + 7 gün içinde:
  - Warn: "Bu kişi için son 7 günde başvuru mevcut"
  - Allow override with confirmation
```

### Partial Row

```
Satır başladı ama tamamlanmadı:
  - Validation error vermeden bekle
  - STATUS boş kal
  - 24h sonra "incomplete" olarak işaretle
```

### Rate Limit

```
Sheets API quota: 300 requests/minute
  - Batch reads (100 rows per request)
  - Batch writes (100 cells per request)
  - Exponential backoff on 429
```

### Concurrent Edit

```
Acente ve sistem aynı anda düzenlerse:
  - Sistem yazımı öncelikli
  - Conflict detection via "SON_GUNCELLEME"
  - Alert if mismatch
```

---

## Güvenlik Notları

1. **Service Account:** Minimal scope (spreadsheets.edit)
2. **Sheet ID:** Directus'ta encrypted storage
3. **No PII in Logs:** Sadece row numbers ve status
4. **Access Control:** Service account sadece belirli sheet'lere erişebilir
5. **Audit Trail:** Her sync işlemi logged
6. **Data Residency:** Sheets verisi Google'da, ancak geçici - 24h max

---

## Google Apps Script (Optional)

Acentenin sheet'ine eklenebilecek custom menu:

```javascript
function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu('VISE OS')
    .addItem('Durumları Güncelle', 'refreshStatuses')
    .addItem('Tamamlananları Temizle', 'cleanupCompleted')
    .addItem('Kredi Bakiyesi', 'showCreditBalance')
    .addSeparator()
    .addItem('Yardım', 'showHelp')
    .addToUi();
}

function refreshStatuses() {
  // API call to sync service
}

function cleanupCompleted() {
  // Archive and delete completed rows
}

function showCreditBalance() {
  var balance = getCreditsFromAPI();
  SpreadsheetApp.getUi().alert('Kalan Kredi: ' + balance);
}
```

---

## Sonraki Adımlar

Bu template onaylandıktan sonra:
1. **Sample Google Sheet** oluştur ve paylaş
2. **Sheets Sync Service** implementasyonu
3. **XLSX Import Endpoint** implementasyonu
