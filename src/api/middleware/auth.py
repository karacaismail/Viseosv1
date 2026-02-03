"""
Authentication middleware for VISE OS FastAPI application.

This module provides JWT-based authentication middleware for API requests.
It supports:
- Bearer token authentication via Authorization header
- API key authentication via X-API-Key header
- Extraction and validation of agency context
- Optional authentication for public endpoints

Usage:
    from fastapi import FastAPI, Depends
    from src.api.middleware.auth import AuthMiddleware, get_current_agency

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/bookings")
    async def get_bookings(agency_id: str = Depends(get_current_agency)):
        return await booking_service.get_by_agency(agency_id)
"""

from datetime import datetime, timezone
from typing import Any, Callable

import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from src.api.config import get_settings

logger = structlog.get_logger()

# Security schemes
bearer_scheme = HTTPBearer(auto_error=False)


# =============================================================================
# Authentication Middleware
# =============================================================================


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that handles authentication for API requests.

    This middleware:
    - Extracts JWT tokens from Authorization header (Bearer scheme)
    - Validates API keys from X-API-Key header
    - Stores authenticated user/agency context in request.state
    - Allows unauthenticated access to public endpoints

    Public endpoints (no authentication required):
    - /api/health/* - Health check endpoints
    - /docs, /redoc, /openapi.json - API documentation
    - / - Root endpoint

    Example:
        # Add to FastAPI app
        app.add_middleware(AuthMiddleware)

        # Access authenticated context in routes
        @app.get("/api/bookings")
        async def get_bookings(request: Request):
            agency_id = request.state.agency_id
            return await get_bookings_for_agency(agency_id)
    """

    # Endpoints that don't require authentication
    PUBLIC_PATHS: set[str] = {
        "/",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/health",
        "/api/health/ready",
        "/api/health/live",
        "/metrics",
        "/favicon.ico",
    }

    # Prefixes that don't require authentication
    PUBLIC_PREFIXES: tuple[str, ...] = (
        "/api/webhooks/",  # Webhook endpoints have their own verification
    )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        Process request and validate authentication.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The HTTP response.
        """
        # Initialize auth state
        request.state.authenticated = False
        request.state.agency_id = None
        request.state.user_id = None
        request.state.user_role = None

        # Skip authentication for public paths
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Try to authenticate
        auth_result = await self._authenticate(request)

        if auth_result is not None:
            # Authentication failed - return error response
            return auth_result

        # Authentication successful - continue with request
        return await call_next(request)

    def _is_public_path(self, path: str) -> bool:
        """
        Check if a path is public (no authentication required).

        Args:
            path: The request path.

        Returns:
            bool: True if the path is public.
        """
        # Exact match check
        if path in self.PUBLIC_PATHS:
            return True

        # Prefix match check
        for prefix in self.PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return True

        return False

    async def _authenticate(self, request: Request) -> JSONResponse | None:
        """
        Attempt to authenticate the request.

        Tries authentication in order:
        1. JWT Bearer token (Authorization header)
        2. API key (X-API-Key header)

        Args:
            request: The HTTP request.

        Returns:
            JSONResponse if authentication fails, None if successful.
        """
        settings = get_settings()

        # Try Bearer token authentication
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            result = self._validate_jwt(token, request)
            if result is not None:
                return result
            return None  # JWT auth successful

        # Try API key authentication
        api_key = request.headers.get("X-API-Key")
        if api_key:
            result = await self._validate_api_key(api_key, request)
            if result is not None:
                return result
            return None  # API key auth successful

        # No authentication provided
        logger.warning(
            "authentication_missing",
            path=request.url.path,
            method=request.method,
        )

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": "AuthenticationRequired",
                "message": "Authentication is required for this endpoint",
                "code": "AUTH_REQUIRED",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    def _validate_jwt(self, token: str, request: Request) -> JSONResponse | None:
        """
        Validate a JWT token and extract claims.

        Args:
            token: The JWT token string.
            request: The HTTP request.

        Returns:
            JSONResponse if validation fails, None if successful.
        """
        settings = get_settings()

        try:
            # Decode and validate token
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY.get_secret_value(),
                algorithms=[settings.JWT_ALGORITHM],
            )

            # Check expiration
            exp = payload.get("exp")
            if exp and datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(
                timezone.utc
            ):
                logger.warning(
                    "jwt_expired",
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "error": "TokenExpired",
                        "message": "Authentication token has expired",
                        "code": "TOKEN_EXPIRED",
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Extract claims and store in request state
            request.state.authenticated = True
            request.state.agency_id = payload.get("agency_id")
            request.state.user_id = payload.get("sub") or payload.get("user_id")
            request.state.user_role = payload.get("role")

            logger.debug(
                "jwt_authenticated",
                agency_id=request.state.agency_id,
                user_id=request.state.user_id,
                role=request.state.user_role,
            )

            return None  # Success

        except jwt.ExpiredSignatureError:
            logger.warning(
                "jwt_expired",
                path=request.url.path,
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "TokenExpired",
                    "message": "Authentication token has expired",
                    "code": "TOKEN_EXPIRED",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        except jwt.InvalidTokenError as e:
            logger.warning(
                "jwt_invalid",
                path=request.url.path,
                error=str(e),
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "InvalidToken",
                    "message": "Invalid authentication token",
                    "code": "INVALID_TOKEN",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def _validate_api_key(
        self, api_key: str, request: Request
    ) -> JSONResponse | None:
        """
        Validate an API key.

        In a full implementation, this would look up the API key in the database
        to find the associated agency. For now, we support a simple format
        where the API key encodes the agency ID.

        Args:
            api_key: The API key string.
            request: The HTTP request.

        Returns:
            JSONResponse if validation fails, None if successful.
        """
        # TODO: Implement proper API key lookup from Directus
        # For now, validate format and extract agency_id
        # Expected format: vos_<agency_id>_<random>

        if not api_key.startswith("vos_"):
            logger.warning(
                "api_key_invalid_format",
                path=request.url.path,
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "InvalidAPIKey",
                    "message": "Invalid API key format",
                    "code": "INVALID_API_KEY",
                },
            )

        # Extract agency ID from key (simplified)
        parts = api_key.split("_")
        if len(parts) < 3:
            logger.warning(
                "api_key_invalid_format",
                path=request.url.path,
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "InvalidAPIKey",
                    "message": "Invalid API key format",
                    "code": "INVALID_API_KEY",
                },
            )

        agency_id = parts[1]

        # Store in request state
        request.state.authenticated = True
        request.state.agency_id = agency_id
        request.state.user_id = None
        request.state.user_role = "api_key"

        logger.debug(
            "api_key_authenticated",
            agency_id=agency_id,
        )

        return None  # Success


# =============================================================================
# Authentication Dependencies
# =============================================================================


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    """
    FastAPI dependency to get the current authenticated user.

    This dependency extracts user information from the request state
    (set by AuthMiddleware) or from the Authorization header if middleware
    is not used.

    Args:
        request: The HTTP request.
        credentials: Optional Bearer token credentials.

    Returns:
        dict: User information with user_id, agency_id, and role.

    Raises:
        HTTPException: If not authenticated.
    """
    # Check if already authenticated by middleware
    if getattr(request.state, "authenticated", False):
        return {
            "user_id": request.state.user_id,
            "agency_id": request.state.agency_id,
            "role": request.state.user_role,
        }

    # No authentication
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_agency(request: Request) -> str:
    """
    FastAPI dependency to get the current agency ID.

    This is a convenience dependency for routes that only need
    the agency ID for multi-tenant filtering.

    Args:
        request: The HTTP request.

    Returns:
        str: The authenticated agency ID.

    Raises:
        HTTPException: If not authenticated or no agency context.
    """
    if not getattr(request.state, "authenticated", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    agency_id = getattr(request.state, "agency_id", None)
    if not agency_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agency context required",
        )

    return agency_id


def require_role(*allowed_roles: str) -> Callable:
    """
    Create a dependency that requires specific user roles.

    Args:
        *allowed_roles: Role names that are allowed access.

    Returns:
        Callable: A FastAPI dependency function.

    Example:
        @app.delete("/api/agencies/{agency_id}")
        async def delete_agency(
            agency_id: str,
            user: dict = Depends(require_role("super_admin", "platform_admin")),
        ):
            # Only super_admin and platform_admin can delete agencies
            await agency_service.delete(agency_id)
    """

    async def role_checker(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        """Check if user has required role."""
        user_role = user.get("role")
        if user_role not in allowed_roles:
            logger.warning(
                "authorization_denied",
                user_id=user.get("user_id"),
                user_role=user_role,
                required_roles=list(allowed_roles),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {user_role} not authorized for this action",
            )
        return user

    return role_checker


# =============================================================================
# JWT Token Utilities
# =============================================================================


def create_access_token(
    user_id: str,
    agency_id: str,
    role: str,
    expires_hours: int | None = None,
) -> str:
    """
    Create a JWT access token.

    Args:
        user_id: The user's unique identifier.
        agency_id: The user's agency ID.
        role: The user's role.
        expires_hours: Token expiration in hours. Defaults to settings.

    Returns:
        str: The encoded JWT token.

    Example:
        token = create_access_token(
            user_id="user-123",
            agency_id="agency-456",
            role="agency_admin",
        )
    """
    settings = get_settings()

    if expires_hours is None:
        expires_hours = settings.JWT_EXPIRATION_HOURS

    now = datetime.now(timezone.utc)
    expire = now.timestamp() + (expires_hours * 3600)

    payload = {
        "sub": user_id,
        "user_id": user_id,
        "agency_id": agency_id,
        "role": role,
        "iat": now.timestamp(),
        "exp": expire,
    }

    token = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )

    return token


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode a JWT token without verification.

    Useful for extracting claims from expired tokens for refresh flows.

    Args:
        token: The JWT token string.

    Returns:
        dict: The token payload.

    Raises:
        jwt.InvalidTokenError: If the token is malformed.
    """
    settings = get_settings()

    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithms=[settings.JWT_ALGORITHM],
        options={"verify_exp": False},
    )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "AuthMiddleware",
    "get_current_user",
    "get_current_agency",
    "require_role",
    "create_access_token",
    "decode_token",
]
