"""
VISE OS AI Providers Module.

LLM provider clients for the AI Decision Engine supporting
Anthropic (Claude) and OpenAI (GPT-4) integrations.

Features:
- Async HTTP clients using httpx
- Configurable models, tokens, and temperature
- Consistent system prompts for visa booking decisions
- Provider factory for easy instantiation
- Health checking and error handling

Supported Providers:
- Anthropic: Claude 3.5 Sonnet, Claude 3 Opus
- OpenAI: GPT-4 Turbo, GPT-4o

Usage:
    from src.ai.providers import (
        LLMProvider,
        LLMConfig,
        LLMClientFactory,
        AnthropicProvider,
        OpenAIProvider,
    )

    # Using factory
    config = LLMConfig(
        provider=LLMProvider.CLAUDE,
        api_key="sk-...",
        model="claude-3-5-sonnet-20241022",
    )
    client = LLMClientFactory.create(config)
    response = await client.complete("Analyze this error...")

    # Direct instantiation
    from src.ai.providers.anthropic import AnthropicProvider
    anthropic = AnthropicProvider(config)
    response = await anthropic.complete("Suggest recovery steps...")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any


# =============================================================================
# Enums
# =============================================================================


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    CLAUDE = "claude"
    GPT4 = "gpt4"
    LOCAL = "local"  # Ollama/local models (future)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class LLMConfig:
    """
    LLM provider configuration.

    Holds all necessary configuration for connecting to an LLM provider
    including API credentials, model selection, and inference parameters.

    Attributes:
        provider: The LLM provider to use.
        api_key: API key for authentication.
        model: Model identifier (e.g., "claude-3-5-sonnet-20241022").
        max_tokens: Maximum tokens in response (default: 2000).
        temperature: Sampling temperature (default: 0.3 for deterministic).
        timeout: Request timeout in seconds (default: 30).
        base_url: Optional custom API endpoint.

    Example:
        config = LLMConfig(
            provider=LLMProvider.CLAUDE,
            api_key=os.environ["ANTHROPIC_API_KEY"],
            model="claude-3-5-sonnet-20241022",
            temperature=0.2,
        )
    """

    provider: LLMProvider
    api_key: str
    model: str
    max_tokens: int = 2000
    temperature: float = 0.3  # Lower for more deterministic decisions
    timeout: int = 30
    base_url: str | None = None


# =============================================================================
# Abstract Base Client
# =============================================================================


class LLMClient(ABC):
    """
    Abstract base class for LLM clients.

    Defines the interface that all LLM provider implementations must follow.
    This allows the AI Decision Engine to work with any supported provider
    through a consistent interface.

    Methods:
        complete: Generate a completion for the given prompt.
        close: Clean up resources (HTTP clients, etc.).
        health_check: Verify provider is reachable.
    """

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """
        Generate a completion for the given prompt.

        Args:
            prompt: The user prompt to complete.
            context: Optional context dictionary for system prompt building.

        Returns:
            The model's response text.

        Raises:
            AIProviderError: If the API call fails.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the provider is reachable and credentials are valid.

        Returns:
            True if provider is healthy, False otherwise.
        """
        pass


# =============================================================================
# Factory
# =============================================================================


class LLMClientFactory:
    """
    Factory for creating LLM clients.

    Creates the appropriate LLM client based on the provider specified
    in the configuration. Supports all registered providers.

    Usage:
        config = LLMConfig(provider=LLMProvider.CLAUDE, ...)
        client = LLMClientFactory.create(config)
    """

    @staticmethod
    def create(config: LLMConfig) -> LLMClient:
        """
        Create an LLM client based on configuration.

        Args:
            config: LLM configuration specifying provider and settings.

        Returns:
            Configured LLM client instance.

        Raises:
            ValueError: If the provider is not supported.

        Example:
            config = LLMConfig(
                provider=LLMProvider.CLAUDE,
                api_key="sk-ant-...",
                model="claude-3-5-sonnet-20241022",
            )
            client = LLMClientFactory.create(config)
        """
        # Import here to avoid circular imports
        from src.ai.providers.anthropic import AnthropicProvider
        from src.ai.providers.openai import OpenAIProvider

        if config.provider == LLMProvider.CLAUDE:
            return AnthropicProvider(config)
        elif config.provider == LLMProvider.GPT4:
            return OpenAIProvider(config)
        else:
            raise ValueError(f"Unsupported LLM provider: {config.provider}")


# =============================================================================
# Lazy imports for convenience
# =============================================================================

if TYPE_CHECKING:
    from src.ai.providers.anthropic import AnthropicProvider
    from src.ai.providers.openai import OpenAIProvider


def __getattr__(name: str) -> Any:
    """Lazy import providers to avoid circular imports."""
    if name == "AnthropicProvider":
        from src.ai.providers.anthropic import AnthropicProvider

        return AnthropicProvider
    elif name == "OpenAIProvider":
        from src.ai.providers.openai import OpenAIProvider

        return OpenAIProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Enums
    "LLMProvider",
    # Configuration
    "LLMConfig",
    # Abstract base
    "LLMClient",
    # Factory
    "LLMClientFactory",
    # Providers (lazy loaded)
    "AnthropicProvider",
    "OpenAIProvider",
]
