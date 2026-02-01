"""
VISE OS Agency Module.

Provides agency management functionality including:
- Agency CRUD operations
- Credit balance management
- Credit transaction tracking

Components:
- repository: AgencyRepository for data access

Usage:
    from src.core.agency import AgencyRepository

    repo = AgencyRepository()

    # Get agency
    agency = await repo.get_by_id("uuid-here")

    # Get available credits
    credits = await repo.get_available_credits("uuid-here")

    # Reserve credits for booking
    await repo.reserve_credits("uuid-here", amount=1, booking_id="...")
"""

from src.core.agency.repository import AgencyRepository

__all__ = [
    "AgencyRepository",
]
