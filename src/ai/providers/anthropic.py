"""
VISE OS Anthropic (Claude) LLM Provider.

Async client for Anthropic's Claude API with visa booking-specific
system prompts and structured JSON response handling.

Features:
- Async HTTP client using httpx
- Claude 3.5 Sonnet / Claude 3 Opus support
- Booking automation system prompts
- JSON response parsing
- Health checking
- Proper error handling with custom exceptions

Supported Models:
- claude-3-5-sonnet-20241022 (recommended)
- claude-3-opus-20240229
- claude-3-sonnet-20240229
- claude-3-haiku-20240307

Usage:
    from src.ai.providers.anthropic import AnthropicProvider
    from src.ai.providers import LLMConfig, LLMProvider

    config = LLMConfig(
        provider=LLMProvider.CLAUDE,
        api_key="sk-ant-...",
        model="claude-3-5-sonnet-20241022",
    )

    provider = AnthropicProvider(config)
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
# Anthropic Provider
# =============================================================================


class AnthropicProvider:
    """
    Anthropic Claude LLM provider.

    Async client for Claude API with optimized settings for
    visa booking automation decision making.

    Attributes:
        config: LLM configuration.
        client: Async HTTP client.
        _closed: Whether the client has been closed.

    Example:
        provider = AnthropicProvider(config)
        try:
            response = await provider.complete("Analyze this scenario...")
        finally:
            await provider.close()
    """

    BASE_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2024-01-01"

    def __init__(self, config: LLMConfig) -> None:
        """
        Initialize Anthropic provider.

        Args:
            config: LLM configuration with API key and model settings.
        """
        self.config = config
        self._closed = False

        # Use custom base URL if provided
        base_url = config.base_url or self.BASE_URL

        self.client = httpx.AsyncClient(
            base_url=base_url if config.base_url else None,
            headers={
                "x-api-key": config.api_key,
                "anthropic-version": self.ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(config.timeout, connect=10.0),
        )

        logger.debug(
            "anthropic_provider_initialized",
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
        Generate a completion using Claude.

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
                "Anthropic client has been closed",
                provider="anthropic",
            )

        system_prompt = self._build_system_prompt(context)

        try:
            response = await self.client.post(
                self.BASE_URL,
                json={
                    "model": self.config.model,
                    "max_tokens": self.config.max_tokens,
                    "temperature": self.config.temperature,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                },
            )

            if response.status_code == 200:
                data = response.json()
                content = data.get("content", [])

                if not content:
                    raise AIProviderError(
                        "Anthropic returned empty content",
                        provider="anthropic",
                        status_code=200,
                    )

                # Extract text from content blocks
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if block.get("type") == "text"
                ]
                result = "".join(text_parts)

                logger.debug(
                    "anthropic_completion_success",
                    model=self.config.model,
                    prompt_length=len(prompt),
                    response_length=len(result),
                    input_tokens=data.get("usage", {}).get("input_tokens"),
                    output_tokens=data.get("usage", {}).get("output_tokens"),
                )

                return result

            # Handle error responses
            error_data = response.json() if response.content else {}
            error_message = error_data.get("error", {}).get(
                "message", f"HTTP {response.status_code}"
            )

            logger.error(
                "anthropic_api_error",
                status_code=response.status_code,
                error=error_message,
            )

            raise AIProviderError(
                f"Anthropic API error: {error_message}",
                provider="anthropic",
                status_code=response.status_code,
                provider_error=error_message,
            )

        except httpx.TimeoutException as e:
            logger.error(
                "anthropic_timeout",
                timeout=self.config.timeout,
                error=str(e),
            )
            raise AIProviderError(
                f"Anthropic request timed out after {self.config.timeout}s",
                provider="anthropic",
                provider_error=str(e),
            )

        except httpx.HTTPError as e:
            logger.error(
                "anthropic_http_error",
                error=str(e),
            )
            raise AIProviderError(
                f"Anthropic HTTP error: {str(e)}",
                provider="anthropic",
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
        Check if Anthropic API is reachable and credentials are valid.

        Returns:
            True if the API is accessible, False otherwise.
        """
        if self._closed:
            return False

        try:
            # Simple completion to verify API access
            response = await self.client.post(
                self.BASE_URL,
                json={
                    "model": self.config.model,
                    "max_tokens": 10,
                    "messages": [
                        {"role": "user", "content": "ping"},
                    ],
                },
            )

            is_healthy = response.status_code == 200

            logger.debug(
                "anthropic_health_check",
                healthy=is_healthy,
                status_code=response.status_code,
            )

            return is_healthy

        except Exception as e:
            logger.warning(
                "anthropic_health_check_failed",
                error=str(e),
            )
            return False

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if not self._closed:
            await self.client.aclose()
            self._closed = True
            logger.debug("anthropic_provider_closed")

    async def __aenter__(self) -> "AnthropicProvider":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "AnthropicProvider",
    "VISA_BOOKING_SYSTEM_PROMPT",
]
