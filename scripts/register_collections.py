#!/usr/bin/env python3
"""
Register existing SQL tables as Directus collections.
Creates collection metadata, field metadata, relations, roles, flows, and verifies data.
"""
from __future__ import annotations
import asyncio, os, sys, httpx
from dotenv import load_dotenv
load_dotenv()

DIRECTUS_URL = os.getenv("DIRECTUS_URL", "http://localhost:8055")
API_BASE_URL = os.getenv("API_BASE_URL", "http://host.docker.internal:8000")
JWT = ""
client: httpx.AsyncClient | None = None


async def login():
    global JWT
    r = await client.post(f"{DIRECTUS_URL}/auth/login", json={
        "email": "admin@vise-os.dev", "password": "admin123"
    }, timeout=10)
    JWT = r.json()["data"]["access_token"]


def h():
    return {"Authorization": f"Bearer {JWT}"}


async def register_collection(name: str, icon: str, sort: int):
    """Register an existing SQL table as a Directus collection."""
    # Just insert metadata — table already exists
    r = await client.post(f"{DIRECTUS_URL}/collections", json={
        "collection": name,
        "meta": {"icon": icon, "sort": sort, "singleton": False},
        "schema": {},  # key: schema={} tells Directus to use existing table
    }, headers=h(), timeout=15)
    if r.status_code < 300:
        print(f"  ✓ {name}")
        return True
    elif r.status_code in (400, 409):
        print(f"  · {name} (already registered)")
        return True
    else:
        print(f"  ✗ {name}: {r.status_code} {r.text[:150]}")
        return False


async def create_relation(many_col: str, many_field: str, one_col: str):
    r = await client.post(f"{DIRECTUS_URL}/relations", json={
        "collection": many_col,
        "field": many_field,
        "related_collection": one_col,
    }, headers=h(), timeout=10)
    ok = r.status_code < 300 or r.status_code in (400, 409)
    tag = "✓" if r.status_code < 300 else "·"
    print(f"  {tag} {many_col}.{many_field} → {one_col}")
    return ok


async def main():
    global client
    async with httpx.AsyncClient() as c:
        client = c

        # Check health
        r = await c.get(f"{DIRECTUS_URL}/server/health", timeout=10)
        assert r.status_code == 200, f"Directus unhealthy: {r.status_code}"

        await login()
        print(f"Logged in. JWT: {JWT[:20]}...\n")

        # ── Phase 1: Register collections ──
        print("Phase 1: Register Collections")
        print("-" * 50)
        collections = [
            ("agencies", "business", 1),
            ("agency_credits", "account_balance_wallet", 2),
            ("credit_transactions", "receipt_long", 3),
            ("applicants", "person", 4),
            ("booking_requests", "event_available", 5),
            ("booking_results", "verified", 6),
            ("bot_accounts", "smart_toy", 7),
            ("proxies", "vpn_key", 8),
            ("browser_profiles", "fingerprint", 9),
            ("circuit_breakers", "electric_bolt", 10),
            ("system_logs", "description", 11),
            ("api_configurations", "settings", 12),
        ]
        for name, icon, sort in collections:
            await register_collection(name, icon, sort)
        print()

        # ── Phase 2: Relations ──
        print("Phase 2: Relations")
        print("-" * 50)
        rels = [
            ("agency_credits", "agency_id", "agencies"),
            ("credit_transactions", "agency_id", "agencies"),
            ("applicants", "agency_id", "agencies"),
            ("applicants", "parent_applicant_id", "applicants"),
            ("booking_requests", "agency_id", "agencies"),
            ("booking_requests", "applicant_id", "applicants"),
            ("booking_results", "agency_id", "agencies"),
            ("booking_results", "booking_request_id", "booking_requests"),
            ("system_logs", "agency_id", "agencies"),
            ("system_logs", "booking_request_id", "booking_requests"),
        ]
        for mc, mf, oc in rels:
            await create_relation(mc, mf, oc)
        print()

        # ── Phase 3: Roles ──
        print("Phase 3: Roles")
        print("-" * 50)
        for rname, ricon, app in [
            ("Agency Admin", "supervised_user_circle", True),
            ("Agency Operator", "person", True),
            ("Agency Viewer", "visibility", True),
            ("System Bot", "smart_toy", False),
        ]:
            r = await client.post(f"{DIRECTUS_URL}/roles", json={
                "name": rname, "icon": ricon, "admin_access": False, "app_access": app,
            }, headers=h(), timeout=10)
            tag = "✓" if r.status_code < 300 else "·"
            print(f"  {tag} {rname}")
        print()

        # ── Phase 4: Flow (webhook) ──
        print("Phase 4: Automation Flow (Directus → FastAPI)")
        print("-" * 50)
        r = await client.post(f"{DIRECTUS_URL}/flows", json={
            "name": "Booking Event → Bot API",
            "icon": "webhook",
            "description": "On booking_requests/applicants change, POST to FastAPI",
            "status": "active",
            "trigger": "event",
            "options": {
                "type": "action",
                "scope": ["items.create", "items.update"],
                "collections": ["booking_requests", "applicants"],
            },
        }, headers=h(), timeout=10)

        if r.status_code < 300:
            flow = r.json()["data"]
            print(f"  ✓ Flow: {flow['name']} (id={flow['id']})")
            # Create operation
            r2 = await client.post(f"{DIRECTUS_URL}/operations", json={
                "flow": flow["id"],
                "name": "POST to FastAPI webhook",
                "key": "post_to_api",
                "type": "request",
                "position_x": 20, "position_y": 1,
                "options": {
                    "url": f"{API_BASE_URL}/api/webhooks/directus",
                    "method": "POST",
                    "headers": [{"header": "Content-Type", "value": "application/json"}],
                    "body": "{{$trigger}}",
                },
            }, headers=h(), timeout=10)
            if r2.status_code < 300:
                op = r2.json()["data"]
                # Link
                await client.patch(f"{DIRECTUS_URL}/flows/{flow['id']}", json={"operation": op["id"]}, headers=h(), timeout=10)
                print(f"  ✓ Operation linked: POST to FastAPI")
        else:
            print(f"  · Flow already exists or error")
        print()

        # ── Phase 5: Verify ──
        print("Phase 5: Verification")
        print("-" * 50)

        # List collections
        r = await client.get(f"{DIRECTUS_URL}/collections", headers=h(), timeout=10)
        custom = [c for c in r.json()["data"] if not c["collection"].startswith("directus_")]
        print(f"  Registered collections: {len(custom)}")

        # Check fields per collection
        for cc in sorted(custom, key=lambda x: x["collection"]):
            name = cc["collection"]
            r2 = await client.get(f"{DIRECTUS_URL}/fields/{name}", headers=h(), timeout=10)
            nf = len(r2.json().get("data", []))
            print(f"    {name}: {nf} fields")

        # Check sample data
        r = await client.get(f"{DIRECTUS_URL}/items/agencies", headers=h(), timeout=10)
        if r.status_code == 200:
            items = r.json().get("data", [])
            print(f"\n  Agencies in DB: {len(items)}")
            for a in items:
                print(f"    - {a['name']} ({a['contact_email']}) id={a['id']}")

        r = await client.get(f"{DIRECTUS_URL}/items/bot_accounts", headers=h(), timeout=10)
        if r.status_code == 200:
            items = r.json().get("data", [])
            print(f"  Bot accounts: {len(items)}")

        r = await client.get(f"{DIRECTUS_URL}/items/agency_credits", headers=h(), timeout=10)
        if r.status_code == 200:
            items = r.json().get("data", [])
            for cr in items:
                print(f"  Credits: total={cr['total_credits']}, used={cr['used_credits']}")
        print()

        print("=" * 50)
        print("DONE!")
        print(f"  Panel: {DIRECTUS_URL}")
        print(f"  Login: admin@vise-os.dev / admin123")
        print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
