"""
FastAPI routers for VISE OS API.

This package contains all API route handlers organized by domain:
- health: Health check and readiness endpoints
- bookings: Booking request management
- agencies: Agency profile and credit management
- applicants: Applicant data management
- webhooks: External webhook endpoints

Usage:
    from src.api.routers import health, bookings, agencies, applicants, webhooks

    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(bookings.router, prefix="/api/bookings", tags=["bookings"])
    app.include_router(agencies.router, prefix="/api/agencies", tags=["agencies"])
    app.include_router(applicants.router, prefix="/api/applicants", tags=["applicants"])
    app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])
"""

from src.api.routers import agencies, applicants, bookings, health, webhooks

__all__ = [
    "agencies",
    "applicants",
    "bookings",
    "health",
    "webhooks",
]
