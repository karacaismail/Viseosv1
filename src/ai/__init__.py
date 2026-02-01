"""
VISE OS AI Module.

LLM-powered decision engine for dynamic scenario handling,
CSS selector healing, and anomaly detection.

Components:
- providers: Anthropic (Claude), OpenAI (GPT-4) integrations
- decision_engine: AI decision making service (future)
- selector_healer: Automatic CSS selector recovery (future)
- anomaly_detector: Bot detection evasion (future)

Usage:
    from src.ai import (
        LLMProvider,
        LLMConfig,
        LLMClientFactory,
        AnthropicProvider,
        OpenAIProvider,
    )

    # Create a Claude client
    config = LLMConfig(
        provider=LLMProvider.CLAUDE,
        api_key="sk-ant-...",
        model="claude-3-5-sonnet-20241022",
    )
    client = LLMClientFactory.create(config)

    # Use the client
    response = await client.complete("Analyze this error...")
    await client.close()
"""

from src.ai.providers import (
    LLMProvider,
    LLMConfig,
    LLMClient,
    LLMClientFactory,
    AnthropicProvider,
    OpenAIProvider,
)


__all__ = [
    # Provider enums
    "LLMProvider",
    # Configuration
    "LLMConfig",
    # Abstract base
    "LLMClient",
    # Factory
    "LLMClientFactory",
    # Provider implementations
    "AnthropicProvider",
    "OpenAIProvider",
]
