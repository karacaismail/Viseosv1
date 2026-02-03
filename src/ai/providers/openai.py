"""
VISE OS OpenAI (GPT-4) LLM Provider.

Async client for OpenAI's GPT-4 API with visa booking-specific
system prompts and structured JSON response handling.

Features:
- Async HTTP client using httpx
- GPT-4 Turbo / GPT-4o support
- Booking automation system prompts
- JSON response parsing
- Health checking
- Proper error handling with custom exceptions

Supported Models:
- gpt-4-turbo-preview (recommended)
- gpt-4o
- gpt-4o-mini
- gpt-4-0125-preview

Usage:
    from src.ai.providers.openai import OpenAIProvider
    from src.ai.providers import LLMConfig, LLMProvider

    config = LLMConfig(
        provider=LLMProvider.GPT4,
        api_key="sk-...",
        model="gpt-4-turbo-preview",
    )

    provider = OpenAIProvider(config)
    response = await provider.complete("Analyze this error...")
    await provider.close()
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from src.core.exceptions import AIProviderError

if TYPE_CHECKING:
    from src.ai.providers import LLMConfig

logger = structlog.get_logger(__name__)


# =============================================================================
# System Prompt
# =============================================================================


VISA_BOOKING_SYSTEM_PROMPT = """You are an AI decision engine for a visa appointment booking automation system.

Your role is to analyze browser states, error conditions, and booking flows to make optimal decisions.

Key responsibilities:
1. Selector healing: When elements can't be found, suggest alternative selectors
2. Strategy switching: Recommend when to change approach (aggressive/conservative)
3. Error recovery: Classify errors and suggest recovery actions
4. Anomaly detection: Identify unusual patterns that might indicate detection
5. Human escalation: Know when to escalate to human operators

Always respond with structured JSON containing your decision and reasoning.
Format: {"decision": "...", "action": "...", "confidence": 0.0-1.0, "reasoning": "..."}

Be conservative with confidence scores. Below 0.7 means human review recommended.

Important guidelines:
- Consider the time-critical nature of visa appointments
- Prioritize avoiding detection over speed when detection is suspected
- Suggest specific CSS/XPath selectors when healing is needed
- Provide actionable recovery steps, not just descriptions
- Factor in previous actions and failure counts when making decisions"""


# =============================================================================
# OpenAI Provider
# =============================================================================


class OpenAIProvider:
    """
    OpenAI GPT-4 LLM provider.

    Async client for OpenAI API with optimized settings for
    visa booking automation decision making.

    Attributes:
        config: LLM configuration.
        client: Async HTTP client.
        _closed: Whether the client has been closed.

    Example:
        provider = OpenAIProvider(config)
        try:
            response = await provider.complete("Analyze this scenario...")
        finally:
            await provider.close()
    """

    BASE_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, config: LLMConfig) -> None:
        """
        Initialize OpenAI provider.

        Args:
            config: LLM configuration with API key and model settings.
        """
        self.config = config
        self._closed = False

        # Use custom base URL if provided
        base_url = config.base_url or "https://api.openai.com"

        self.client = httpx.AsyncClient(
            base_url=base_url if config.base_url else None,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(config.timeout, connect=10.0),
        )

        logger.debug(
            "openai_provider_initialized",
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

    async def complete(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """
        Generate a completion using GPT-4.

        Args:
            prompt: The user prompt for the model.
            context: Optional context dictionary for system prompt customization.

        Returns:
            The model's response text.

        Raises:
            AIProviderError: If the API call fails or returns an error.

        Example:
            response = await provider.complete(
                "A CSS selector is not finding the expected element...",
                context={"site": "vfs", "state": "login"},
            )
        """
        if self._closed:
            raise AIProviderError(
                "OpenAI client has been closed",
                provider="openai",
            )

        system_prompt = self._build_system_prompt(context)

        try:
            response = await self.client.post(
                self.BASE_URL,
                json={
                    "model": self.config.model,
                    "max_tokens": self.config.max_tokens,
                    "temperature": self.config.temperature,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                },
            )

            if response.status_code == 200:
                data = response.json()
                choices = data.get("choices", [])

                if not choices:
                    raise AIProviderError(
                        "OpenAI returned empty choices",
                        provider="openai",
                        status_code=200,
                    )

                result = choices[0].get("message", {}).get("content", "")

                if not result:
                    raise AIProviderError(
                        "OpenAI returned empty content",
                        provider="openai",
                        status_code=200,
                    )

                usage = data.get("usage", {})
                logger.debug(
                    "openai_completion_success",
                    model=self.config.model,
                    prompt_length=len(prompt),
                    response_length=len(result),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )

                return result

            # Handle error responses
            error_data = response.json() if response.content else {}
            error_message = error_data.get("error", {}).get(
                "message", f"HTTP {response.status_code}"
            )

            logger.error(
                "openai_api_error",
                status_code=response.status_code,
                error=error_message,
            )

            raise AIProviderError(
                f"OpenAI API error: {error_message}",
                provider="openai",
                status_code=response.status_code,
                provider_error=error_message,
            )

        except httpx.TimeoutException as e:
            logger.error(
                "openai_timeout",
                timeout=self.config.timeout,
                error=str(e),
            )
            raise AIProviderError(
                f"OpenAI request timed out after {self.config.timeout}s",
                provider="openai",
                provider_error=str(e),
            )

        except httpx.HTTPError as e:
            logger.error(
                "openai_http_error",
                error=str(e),
            )
            raise AIProviderError(
                f"OpenAI HTTP error: {str(e)}",
                provider="openai",
                provider_error=str(e),
            )

    def _build_system_prompt(self, context: dict[str, Any] | None = None) -> str:
        """
        Build the system prompt with optional context.

        Args:
            context: Optional context dictionary with site, state, etc.

        Returns:
            Complete system prompt string.
        """
        base_prompt = VISA_BOOKING_SYSTEM_PROMPT

        if context:
            # Add context-specific instructions
            context_parts = []

            if "site" in context:
                context_parts.append(f"Current site: {context['site']}")

            if "state" in context:
                context_parts.append(f"Current state: {context['state']}")

            if "previous_decisions" in context:
                decisions = context["previous_decisions"][-5:]  # Last 5
                context_parts.append(f"Recent decisions: {decisions}")

            if context_parts:
                base_prompt += "\n\nCurrent Context:\n" + "\n".join(context_parts)

        return base_prompt

    async def health_check(self) -> bool:
        """
        Check if OpenAI API is reachable and credentials are valid.

        Returns:
            True if the API is accessible, False otherwise.
        """
        if self._closed:
            return False

        try:
            # Use the models endpoint to verify API access without tokens
            response = await self.client.get(
                "https://api.openai.com/v1/models",
            )

            is_healthy = response.status_code == 200

            logger.debug(
                "openai_health_check",
                healthy=is_healthy,
                status_code=response.status_code,
            )

            return is_healthy

        except Exception as e:
            logger.warning(
                "openai_health_check_failed",
                error=str(e),
            )
            return False

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if not self._closed:
            await self.client.aclose()
            self._closed = True
            logger.debug("openai_provider_closed")

    async def __aenter__(self) -> "OpenAIProvider":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "OpenAIProvider",
    "VISA_BOOKING_SYSTEM_PROMPT",
]
