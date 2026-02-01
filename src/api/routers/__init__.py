"""
FastAPI routers for VISE OS API.

This package contains all API route handlers organized by domain:
- health: Health check and readiness endpoints
- bookings: Booking request management
- agencies: Agency profile and credit management
- applicants: Applicant data management
- webhooks: External webhook endpoints

Usage:
    from src.api.routers import health

    app.include_router(health.router, prefix="/api", tags=["health"])
"""

from src.api.routers import health

__all__ = [
    "health",
]
