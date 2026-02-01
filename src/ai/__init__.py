"""
VISE OS AI Module.

LLM-powered decision engine for dynamic scenario handling,
CSS selector healing, and anomaly detection.

Components:
- providers: Anthropic (Claude), OpenAI (GPT-4) integrations
- decision_engine: AI decision making service with strategy selection
- selector_healer: Automatic CSS selector recovery
- anomaly_detector: Bot detection evasion

Usage:
    from src.ai import (
        LLMProvider,
        LLMConfig,
        LLMClientFactory,
        AIDecisionEngine,
        DecisionType,
        StrategyType,
        BrowserContext,
        FlowContext,
    )

    # Create an AI Decision Engine
    config = LLMConfig(
        provider=LLMProvider.CLAUDE,
        api_key="sk-ant-...",
        model="claude-3-5-sonnet-20241022",
    )
    engine = AIDecisionEngine(config)

    # Make decisions
    decision = await engine.make_decision(
        decision_request="evaluate strategy",
        flow_context=flow_ctx,
        browser_context=browser_ctx,
        additional_data={"errors": ["timeout"]},
    )

    await engine.close()
"""

from src.ai.providers import (
    LLMProvider,
    LLMConfig,
    LLMClient,
    LLMClientFactory,
    AnthropicProvider,
    OpenAIProvider,
)
from src.ai.decision_engine import (
    DecisionType,
    StrategyType,
    AIDecision,
    BrowserContext,
    FlowContext,
    SelectorHealingEngine,
    StrategySwitchingEngine,
    AnomalyDetectionEngine,
    ErrorRecoveryEngine,
    AIDecisionEngine,
)
from src.ai.selector_healer import (
    SelectorType,
    HealingStrategy,
    SelectorHealingResult,
    SelectorHistoryEntry,
    SelectorContext,
    SelectorAnalyzer,
    SelectorHealer,
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
    # Decision Engine enums
    "DecisionType",
    "StrategyType",
    # Decision Engine data classes
    "AIDecision",
    "BrowserContext",
    "FlowContext",
    # Decision Engine components
    "SelectorHealingEngine",
    "StrategySwitchingEngine",
    "AnomalyDetectionEngine",
    "ErrorRecoveryEngine",
    "AIDecisionEngine",
    # Selector Healer enums
    "SelectorType",
    "HealingStrategy",
    # Selector Healer data classes
    "SelectorHealingResult",
    "SelectorHistoryEntry",
    "SelectorContext",
    # Selector Healer utilities
    "SelectorAnalyzer",
    # Selector Healer main class
    "SelectorHealer",
]
