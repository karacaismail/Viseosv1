#!/usr/bin/env python3
"""
VISE OS — Directus Schema Seed Script (v2)

Creates all collections WITH fields inline (Directus 10.10 requires this).
Then creates relations, flows, roles, and sample data.

Usage:
    python scripts/seed_directus.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

DIRECTUS_URL = os.getenv("DIRECTUS_URL", "http://localhost:8055")
API_BASE_URL = os.getenv("API_BASE_URL", "http://host.docker.internal:8000")

client: httpx.AsyncClient | None = None
JWT: str = ""


async def login() -> str:
    """Login and get JWT token."""
    r = await client.post(f"{DIRECTUS_URL}/auth/login", json={
        "email": "admin@vise-os.dev", "password": "admin123",
    }, timeout=10)
    return r.json()["data"]["access_token"]


async def req(method: str, path: str, json: Any = None) -> Any:
    r = await client.request(method, f"{DIRECTUS_URL}{path}", json=json, timeout=30,
                              headers={"Authorization": f"Bearer {JWT}"})
    if r.status_code in (400, 409):
        return None  # already exists
    if r.status_code >= 400:
        print(f"  ERR {r.status_code} {method} {path}: {r.text[:200]}")
        return None
    try:
        return r.json().get("data")
    except Exception:
        return r.status_code


# ── Field helpers ──

def pk() -> dict:
    return {"field": "id", "type": "uuid",
            "meta": {"special": ["uuid"], "interface": "input", "readonly": True, "hidden": True},
            "schema": {"is_primary_key": True, "has_auto_increment": False}}

def ts_created() -> dict:
    return {"field": "created_at", "type": "timestamp",
            "meta": {"special": ["date-created"], "interface": "datetime", "readonly": True, "width": "half"},
            "schema": {"default_value": "NOW()"}}

def ts_updated() -> dict:
    return {"field": "updated_at", "type": "timestamp",
            "meta": {"special": ["date-updated"], "interface": "datetime", "readonly": True, "width": "half"},
            "schema": {}}

def s(field: str, **kw) -> dict:
    """String field shorthand."""
    d: dict = {"field": field, "type": "string", "meta": {"interface": "input"}, "schema": {}}
    if kw.get("req"): d["schema"]["is_nullable"] = False; d["meta"]["required"] = True
    if kw.get("unique"): d["schema"]["is_unique"] = True
    if kw.get("half"): d["meta"]["width"] = "half"
    if kw.get("note"): d["meta"]["note"] = kw["note"]
    if kw.get("default"): d["schema"]["default_value"] = kw["default"]
    return d

def i(field: str, default: int = 0, **kw) -> dict:
    """Integer field shorthand."""
    d: dict = {"field": field, "type": "integer", "meta": {"interface": "input", "width": "half"}, "schema": {"default_value": default, "is_nullable": False}}
    if kw.get("note"): d["meta"]["note"] = kw["note"]
    return d

def fk(field: str, **kw) -> dict:
    """Foreign key UUID field."""
    d: dict = {"field": field, "type": "uuid", "meta": {"interface": "select-dropdown-m2o", "special": ["m2o"]}, "schema": {}}
    if kw.get("req"): d["schema"]["is_nullable"] = False; d["meta"]["required"] = True
    if kw.get("half"): d["meta"]["width"] = "half"
    return d

def dt(field: str) -> dict:
    """Datetime field."""
    return {"field": field, "type": "timestamp", "meta": {"interface": "datetime", "width": "half"}, "schema": {}}

def j(field: str) -> dict:
    """JSON field."""
    return {"field": field, "type": "json", "meta": {"interface": "input-code", "options": {"language": "JSON"}}, "schema": {}}

def b(field: str, default: bool = False) -> dict:
    """Boolean field."""
    return {"field": field, "type": "boolean", "meta": {"interface": "boolean", "width": "half"}, "schema": {"default_value": default}}

def sel(field: str, choices: list[str], default: str | None = None, **kw) -> dict:
    """Select dropdown field."""
    d: dict = {"field": field, "type": "string",
        "meta": {"interface": "select-dropdown", "width": "half",
                 "options": {"choices": [{"text": c.replace("_", " ").title(), "value": c} for c in choices]}},
        "schema": {}}
    if default: d["schema"]["default_value"] = default
    if kw.get("req"): d["schema"]["is_nullable"] = False; d["meta"]["required"] = True
    return d

def txt(field: str) -> dict:
    """Text/multiline field."""
    return {"field": field, "type": "text", "meta": {"interface": "input-multiline", "width": "full"}, "schema": {}}

def date_f(field: str, **kw) -> dict:
    """Date field."""
    d: dict = {"field": field, "type": "date", "meta": {"interface": "datetime", "width": "half"}, "schema": {}}
    if kw.get("req"): d["schema"]["is_nullable"] = False; d["meta"]["required"] = True
    return d

def time_f(field: str) -> dict:
    return {"field": field, "type": "time", "meta": {"interface": "datetime", "width": "half"}, "schema": {}}

def float_f(field: str) -> dict:
    return {"field": field, "type": "float", "meta": {"interface": "input", "width": "half"}, "schema": {}}


# ── Collection definitions ──

COLLECTIONS: list[tuple[str, str, int, list[dict]]] = []

def C(name: str, icon: str, sort: int, fields: list[dict]):
    COLLECTIONS.append((name, icon, sort, [pk()] + fields))

# 1. agencies
C("agencies", "business", 1, [
    sel("status", ["active", "suspended", "trial"], "active", req=True),
    s("name", req=True, half=True),
    s("tursab_no", half=True),
    s("contact_name", req=True, half=True),
    s("contact_email", req=True, unique=True, half=True),
    s("contact_phone", half=True),
    s("telegram_chat_id", half=True),
    s("discord_webhook"),
    s("google_sheet_id", half=True),
    b("google_sheet_sync_enabled"),
    j("default_countries"),
    j("notification_preferences"),
    ts_created(), ts_updated(),
])

# 2. agency_credits
C("agency_credits", "account_balance_wallet", 2, [
    fk("agency_id", req=True),
    i("total_credits"), i("used_credits"), i("reserved_credits"),
    dt("last_purchase_at"), dt("last_usage_at"),
])

# 3. credit_transactions
C("credit_transactions", "receipt_long", 3, [
    fk("agency_id", req=True),
    sel("type", ["purchase", "usage", "refund", "reserve", "release"], req=True),
    i("amount"), i("balance_after"),
    s("reference_id", half=True),
    s("description"),
    ts_created(),
])

# 4. applicants
C("applicants", "person", 4, [
    fk("agency_id", req=True),
    s("external_ref", half=True),
    sel("status", ["pending", "processing", "completed", "failed", "expired"], "pending", req=True),
    s("first_name", req=True, half=True, note="Encrypted PII"),
    s("last_name", req=True, half=True, note="Encrypted PII"),
    date_f("birth_date", req=True),
    s("nationality", req=True, half=True),
    s("passport_number", req=True, half=True, note="Encrypted PII"),
    date_f("passport_expiry", req=True),
    s("phone", req=True, half=True, note="Encrypted PII"),
    s("email", half=True, note="Encrypted PII"),
    s("target_country", req=True, half=True),
    s("target_city", half=True),
    s("visa_type", req=True, half=True),
    j("preferred_dates"),
    b("exclude_weekends"),
    {"field": "family_group_id", "type": "uuid", "meta": {"interface": "input", "width": "half"}, "schema": {}},
    fk("parent_applicant_id", half=True),
    dt("expires_at"), dt("deleted_at"),
    ts_created(), ts_updated(),
])

# 5. booking_requests
C("booking_requests", "event_available", 5, [
    fk("agency_id", req=True),
    fk("applicant_id", req=True),
    sel("status", ["pending", "queued", "processing", "slot_found", "booking", "payment", "verifying", "completed", "failed", "expired", "cancelled"], "pending", req=True),
    i("priority", 5, note="1=urgent, 5=normal, 10=low"),
    sel("target_system", ["vfs", "idata", "bls", "kkosmos"], req=True),
    s("target_country", req=True, half=True),
    s("target_location", half=True),
    s("visa_category", req=True, half=True),
    i("attempts"), i("max_attempts", 50),
    dt("last_attempt_at"), dt("next_attempt_at"),
    i("slot_found_count"),
    s("error_code", half=True),
    txt("error_message"),
    {"field": "assigned_account_id", "type": "uuid", "meta": {"interface": "input", "width": "half"}, "schema": {}},
    {"field": "assigned_proxy_id", "type": "uuid", "meta": {"interface": "input", "width": "half"}, "schema": {}},
    j("metadata"),
    ts_created(), ts_updated(),
    dt("completed_at"),
])

# 6. booking_results
C("booking_results", "verified", 6, [
    fk("agency_id", req=True),
    fk("booking_request_id", req=True),
    sel("status", ["success", "failed", "cancelled"], "success"),
    s("target_system", half=True), s("target_country", half=True),
    s("confirmation_number", half=True, note="Encrypted"),
    date_f("appointment_date"),
    time_f("appointment_time"),
    s("appointment_location", half=True),
    i("total_attempts"), i("total_duration_seconds"), i("credits_charged"),
    s("error_code", half=True),
    s("screenshot_url"),
    ts_created(),
])

# 7. bot_accounts
C("bot_accounts", "smart_toy", 7, [
    sel("system", ["vfs", "idata", "bls", "kkosmos"], req=True),
    s("country", req=True, half=True),
    s("email", req=True, half=True, note="Encrypted"),
    s("password", req=True, half=True, note="Encrypted"),
    sel("status", ["active", "cooldown", "banned", "retired"], "active"),
    i("health_score", 100), i("success_count"), i("failure_count"), i("consecutive_failures"),
    dt("last_used_at"), dt("cooldown_until"), dt("ban_detected_at"),
    txt("notes"),
    ts_created(), ts_updated(),
])

# 8. proxies
C("proxies", "vpn_key", 8, [
    sel("provider", ["brightdata", "oxylabs", "smartproxy"], req=True),
    sel("type", ["residential", "mobile", "datacenter"], req=True),
    s("country", req=True, half=True),
    s("host", req=True, half=True), i("port", 0),
    s("username", half=True), s("password", half=True, note="Encrypted"),
    sel("status", ["active", "slow", "blocked", "retired"], "active"),
    i("health_score", 100), i("success_count"), i("failure_count"), i("avg_response_ms"),
    dt("last_used_at"), dt("last_success_at"),
    ts_created(), ts_updated(),
])

# 9. browser_profiles
C("browser_profiles", "fingerprint", 9, [
    s("name", req=True, half=True),
    sel("status", ["active", "burned", "retired"], "active"),
    s("user_agent", req=True),
    i("viewport_width", 1366), i("viewport_height", 768),
    s("timezone", half=True, default="Europe/Istanbul"),
    s("locale", half=True, default="tr-TR"),
    s("webgl_vendor", half=True), s("webgl_renderer", half=True),
    float_f("canvas_noise"), float_f("audio_noise"),
    j("fonts"), j("plugins"),
    i("usage_count"),
    dt("last_used_at"),
    ts_created(), ts_updated(),
])

# 10. circuit_breakers
C("circuit_breakers", "electric_bolt", 10, [
    s("domain", req=True, unique=True, half=True),
    sel("state", ["closed", "open", "half_open"], "closed", req=True),
    i("failure_count"), i("success_count"),
    dt("last_failure_at"), dt("opened_at"), dt("closes_at"),
    j("metadata"),
    ts_created(), ts_updated(),
])

# 11. system_logs
C("system_logs", "description", 11, [
    sel("level", ["debug", "info", "warning", "error", "critical"], req=True),
    sel("category", ["auth", "booking", "payment", "system"], req=True),
    s("event", req=True, half=True),
    fk("agency_id", half=True),
    {"field": "booking_request_id", "type": "uuid", "meta": {"interface": "input", "width": "half"}, "schema": {}},
    s("message", req=True),
    j("details"),
    s("ip_address", half=True), s("user_agent", half=True),
    ts_created(),
])

# 12. api_configurations
C("api_configurations", "settings", 12, [
    s("service", req=True, half=True),
    s("config_key", req=True, half=True),
    s("config_value", req=True, note="Encrypted at app layer"),
    b("is_active", True),
    txt("notes"),
    ts_created(), ts_updated(),
])


# ── Relations ──

RELATIONS = [
    ("agency_credits", "agency_id", "agencies"),
    ("credit_transactions", "agency_id", "agencies"),
    ("applicants", "agency_id", "agencies"),
    ("applicants", "parent_applicant_id", "applicants"),
    ("booking_requests", "agency_id", "agencies"),
    ("booking_requests", "applicant_id", "applicants"),
    ("booking_results", "agency_id", "agencies"),
    ("booking_results", "booking_request_id", "booking_requests"),
    ("system_logs", "agency_id", "agencies"),
]


# ── Main ──

async def main():
    global client, JWT

    print("=" * 60)
    print("VISE OS — Directus Schema Seed v2")
    print(f"Target: {DIRECTUS_URL}")
    print("=" * 60)

    async with httpx.AsyncClient() as c:
        client = c

        # Health check
        r = await c.get(f"{DIRECTUS_URL}/server/health", timeout=10)
        if r.status_code != 200:
            print(f"ERROR: Directus not healthy ({r.status_code})")
            sys.exit(1)

        # Login for JWT
        JWT = await login()
        print(f"Logged in as admin (JWT: {JWT[:20]}...)\n")

        # Phase 1: Collections with all fields
        print("Phase 1: Creating Collections + Fields (inline)")
        print("-" * 50)
        for name, icon, sort, fields in COLLECTIONS:
            payload = {
                "collection": name,
                "meta": {"icon": icon, "sort": sort, "singleton": False},
                "fields": fields,
            }
            result = await req("POST", "/collections", payload)
            tag = "✓" if result else "·"
            print(f"  {tag} {name} ({len(fields)} fields)")

        print()

        # Phase 2: Relations
        print("Phase 2: Creating Relations")
        print("-" * 50)
        for many_col, many_field, one_col in RELATIONS:
            result = await req("POST", "/relations", {
                "collection": many_col,
                "field": many_field,
                "related_collection": one_col,
            })
            tag = "✓" if result else "·"
            print(f"  {tag} {many_col}.{many_field} → {one_col}")
        print()

        # Phase 3: Roles
        print("Phase 3: Creating Roles")
        print("-" * 50)
        roles_spec = [
            ("Agency Admin", "supervised_user_circle", True),
            ("Agency Operator", "person", True),
            ("Agency Viewer", "visibility", True),
            ("System Bot", "smart_toy", False),
        ]
        for rname, ricon, app_access in roles_spec:
            result = await req("POST", "/roles", {
                "name": rname, "icon": ricon,
                "admin_access": False, "app_access": app_access,
            })
            tag = "✓" if result else "·"
            rid = result["id"] if result else "exists"
            print(f"  {tag} {rname} (id={rid})")
        print()

        # Phase 4: Flow (webhook → FastAPI)
        print("Phase 4: Creating Automation Flow")
        print("-" * 50)
        flow = await req("POST", "/flows", {
            "name": "Booking Event → Bot API",
            "icon": "webhook",
            "description": "Triggers FastAPI when booking_requests or applicants change",
            "status": "active",
            "trigger": "event",
            "options": {
                "type": "action",
                "scope": ["items.create", "items.update"],
                "collections": ["booking_requests", "applicants"],
            },
        })
        if flow:
            print(f"  ✓ Flow: {flow['name']} (id={flow['id']})")
            op = await req("POST", "/operations", {
                "flow": flow["id"],
                "name": "POST to FastAPI",
                "key": "post_to_fastapi",
                "type": "request",
                "position_x": 20, "position_y": 1,
                "options": {
                    "url": f"{API_BASE_URL}/api/webhooks/directus",
                    "method": "POST",
                    "headers": [{"header": "Content-Type", "value": "application/json"}],
                    "body": "{{$trigger}}",
                },
            })
            if op:
                await req("PATCH", f"/flows/{flow['id']}", {"operation": op["id"]})
                print(f"  ✓ Operation: POST to FastAPI → linked to flow")
        else:
            print("  · Flow already exists")
        print()

        # Phase 5: Sample Data
        print("Phase 5: Sample Data")
        print("-" * 50)
        agency = await req("POST", "/items/agencies", {
            "status": "active",
            "name": "Test Travel Agency",
            "tursab_no": "12345",
            "contact_name": "Test Admin",
            "contact_email": "test@agency.com",
            "contact_phone": "+905551234567",
            "google_sheet_sync_enabled": False,
            "default_countries": ["DE", "IT", "FR"],
        })
        if agency:
            aid = agency["id"]
            print(f"  ✓ Agency: {agency['name']} (id={aid})")

            cred = await req("POST", "/items/agency_credits", {
                "agency_id": aid,
                "total_credits": 100, "used_credits": 0, "reserved_credits": 0,
            })
            if cred:
                print(f"  ✓ Credits: 100 for Test Travel Agency")

            # Sample bot account
            ba = await req("POST", "/items/bot_accounts", {
                "system": "vfs", "country": "de",
                "email": "vfsbot@test.com", "password": "test123",
                "status": "active", "health_score": 100,
                "success_count": 0, "failure_count": 0, "consecutive_failures": 0,
            })
            if ba:
                print(f"  ✓ Bot Account: vfs/de (id={ba['id']})")
        else:
            print("  · Agency already exists")
        print()

        # Verify
        print("Phase 6: Verification")
        print("-" * 50)
        r = await client.get(f"{DIRECTUS_URL}/collections",
                              headers={"Authorization": f"Bearer {JWT}"}, timeout=10)
        cols = [c["collection"] for c in r.json()["data"] if not c["collection"].startswith("directus_")]
        print(f"  Custom collections: {len(cols)}")
        for c in sorted(cols):
            r2 = await client.get(f"{DIRECTUS_URL}/fields/{c}",
                                   headers={"Authorization": f"Bearer {JWT}"}, timeout=10)
            nf = len(r2.json().get("data", []))
            print(f"    {c}: {nf} fields")
        print()

        print("=" * 60)
        print("DONE!")
        print(f"  Directus Panel: {DIRECTUS_URL}")
        print(f"  Admin Login:    admin@vise-os.dev / admin123")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
