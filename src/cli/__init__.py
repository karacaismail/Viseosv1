"""
VISE OS CLI tools.

Provides command-line interface for direct booking operations,
bypassing the queue system. Useful for testing and debugging.

Usage:
    python -m src.cli vfs-book --country de --category tourist
    python -m src.cli vfs-search --country de --category tourist
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="VISE OS - Direct booking CLI",
        prog="vise",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # vfs-search: Search for available VFS slots
    search_parser = subparsers.add_parser("vfs-search", help="Search VFS slots")
    search_parser.add_argument("--country", default="de", help="Target country code")
    search_parser.add_argument("--category", default="tourist", help="Visa category")
    search_parser.add_argument("--city", default=None, help="Application center city")
    search_parser.add_argument("--email", required=True, help="VFS account email")
    search_parser.add_argument("--password", required=True, help="VFS account password")
    search_parser.add_argument(
        "--source-country", default="tr", help="Source country code"
    )

    # vfs-book: Run a full VFS booking
    book_parser = subparsers.add_parser("vfs-book", help="Run full VFS booking")
    book_parser.add_argument("--country", default="de", help="Target country code")
    book_parser.add_argument("--category", default="tourist", help="Visa category")
    book_parser.add_argument("--city", default=None, help="Application center city")
    book_parser.add_argument("--email", required=True, help="VFS account email")
    book_parser.add_argument("--password", required=True, help="VFS account password")
    book_parser.add_argument(
        "--source-country", default="tr", help="Source country code"
    )
    book_parser.add_argument(
        "--applicant-json", default=None, help="Path to applicant JSON file"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "vfs-search":
        asyncio.run(_run_vfs_search(args))
    elif args.command == "vfs-book":
        asyncio.run(_run_vfs_book(args))


async def _run_vfs_search(args: argparse.Namespace) -> None:
    """Search for available VFS appointment slots."""
    from src.bot.adapters.base import AdapterConfig, SlotSearchCriteria, SiteCode
    from src.bot.container import BotServiceContainer
    from src.bot.services.account import BotAccount, AccountStatus, TargetSystem

    print(f"Initializing VISE OS services...")
    container = BotServiceContainer.get_instance()
    await container.initialize()

    try:
        adapter = container.get_adapter(
            "vfs",
            config=AdapterConfig(
                site_code=SiteCode.VFS,
                base_url="https://visa.vfsglobal.com",
                custom_settings={"source_country": args.source_country},
            ),
        )

        account = BotAccount(
            id="cli-search",
            system=TargetSystem.VFS,
            country=args.country,
            email=args.email,
            password=args.password,
            status=AccountStatus.ACTIVE,
        )

        proxy = await container.proxy_manager.get_proxy(
            target_site="vfs",
            country=args.source_country,
            sticky_session=True,
        )

        async with adapter.create_session(account, proxy) as session:
            print(f"Logging in as {args.email}...")
            login_ok = await adapter.login(session, account)
            if not login_ok:
                print("Login failed!")
                return

            print(f"Searching slots for {args.country}/{args.category}...")
            criteria = SlotSearchCriteria(
                country=args.country,
                category=args.category,
                city=args.city,
                from_date=date.today(),
            )
            slots = await adapter.search_slots(session, criteria)

            if not slots:
                print("No available slots found.")
                return

            print(f"\nFound {len(slots)} available slots:")
            for i, slot in enumerate(slots, 1):
                print(f"  {i}. {slot.date} {slot.time or 'N/A'} @ {slot.location or 'N/A'}")

    finally:
        await container.shutdown()


async def _run_vfs_book(args: argparse.Namespace) -> None:
    """Run a full VFS booking flow."""
    import json
    from src.bot.adapters.base import AdapterConfig, SlotSearchCriteria, SiteCode
    from src.bot.container import BotServiceContainer
    from src.bot.services.account import BotAccount, AccountStatus, TargetSystem

    print(f"Initializing VISE OS services...")
    container = BotServiceContainer.get_instance()
    await container.initialize()

    # Load applicant data
    applicant_data: dict[str, Any] = {}
    if args.applicant_json:
        with open(args.applicant_json) as f:
            applicant_data = json.load(f)

    try:
        adapter = container.get_adapter(
            "vfs",
            config=AdapterConfig(
                site_code=SiteCode.VFS,
                base_url="https://visa.vfsglobal.com",
                custom_settings={"source_country": args.source_country},
            ),
        )

        account = BotAccount(
            id="cli-book",
            system=TargetSystem.VFS,
            country=args.country,
            email=args.email,
            password=args.password,
            status=AccountStatus.ACTIVE,
        )

        proxy = await container.proxy_manager.get_proxy(
            target_site="vfs",
            country=args.source_country,
            sticky_session=True,
        )

        async with adapter.create_session(account, proxy) as session:
            print(f"Logging in as {args.email}...")
            login_ok = await adapter.login(session, account)
            if not login_ok:
                print("Login failed!")
                return

            print(f"Searching slots for {args.country}/{args.category}...")
            criteria = SlotSearchCriteria(
                country=args.country,
                category=args.category,
                city=args.city,
                from_date=date.today(),
            )
            slots = await adapter.search_slots(session, criteria)

            if not slots:
                print("No available slots found.")
                return

            print(f"Found {len(slots)} slots. Booking first available: {slots[0].date}...")
            result = await adapter.book_slot(session, slots[0], applicant_data)

            if result.is_success:
                print(f"\nBooking successful!")
                print(f"  Confirmation: {result.confirmation_number}")
                print(f"  Date: {result.appointment_date}")
                print(f"  Time: {result.appointment_time}")
            else:
                print(f"\nBooking failed: {result.error_message}")

    finally:
        await container.shutdown()


if __name__ == "__main__":
    main()
