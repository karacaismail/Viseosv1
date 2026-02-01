"""
VISE OS AI Decision Engine.

LLM-powered decision making for dynamic strategy selection, selector healing,
anomaly detection, and adaptive behavior in visa booking automation.

Components:
- AIDecisionEngine: Main decision orchestrator
- SelectorHealingEngine: Automatic CSS selector recovery
- StrategySwitchingEngine: Dynamic strategy management
- AnomalyDetectionEngine: Bot detection and evasion
- ErrorRecoveryEngine: Intelligent error handling

Features:
- Claude/GPT-4 integration for human-like decisions
- Rule-based pre-filtering to reduce API costs
- Confidence-based human escalation
- Decision history tracking and analytics
- Adaptive strategy profiles

Usage:
    from src.ai.decision_engine import AIDecisionEngine
    from src.ai.providers import LLMConfig, LLMProvider

    config = LLMConfig(
        provider=LLMProvider.CLAUDE,
        api_key="sk-ant-...",
        model="claude-3-5-sonnet-20241022",
    )

    engine = AIDecisionEngine(config)

    decision = await engine.make_decision(
        decision_request="selector not found",
        flow_context=flow_context,
        browser_context=browser_context,
        additional_data={"selector": "#submit-btn"},
    )

    await engine.close()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

from src.ai.providers import LLMClient, LLMClientFactory, LLMConfig

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


# =============================================================================
# Decision Types and Enums
# =============================================================================


class DecisionType(str, Enum):
    """AI decision types for booking automation."""

    STRATEGY_SWITCH = "strategy_switch"
    SELECTOR_HEAL = "selector_heal"
    ERROR_RECOVERY = "error_recovery"
    ANOMALY_RESPONSE = "anomaly_response"
    HUMAN_ESCALATION = "human_escalation"
    CONTINUE = "continue"
    ABORT = "abort"
    RETRY = "retry"
    WAIT = "wait"


class StrategyType(str, Enum):
    """Booking automation strategies."""

    AGGRESSIVE = "aggressive"      # Fast, risk tolerant
    CONSERVATIVE = "conservative"  # Slow, safe
    STEALTH = "stealth"           # Maximum evasion
    RECOVERY = "recovery"         # Ban recovery mode


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class AIDecision:
    """
    AI decision output.

    Represents the result of an AI decision with confidence scoring
    and optional details for specific decision types.

    Attributes:
        decision_type: The type of decision made.
        action: Specific action to take.
        confidence: Confidence score (0.0 - 1.0).
        reasoning: Explanation for the decision.
        new_selector: Alternative selector for healing decisions.
        new_strategy: Recommended strategy change.
        wait_seconds: Seconds to wait before retry.
        recovery_steps: Ordered recovery actions.
        escalation_reason: Reason for human escalation.
        timestamp: When the decision was made.

    Example:
        decision = AIDecision(
            decision_type=DecisionType.SELECTOR_HEAL,
            action="try_alternative_selector",
            confidence=0.85,
            reasoning="Found similar element with different ID",
            new_selector="#btn-submit-form",
        )
    """

    decision_type: DecisionType
    action: str
    confidence: float
    reasoning: str

    # Optional details
    new_selector: str | None = None
    new_strategy: StrategyType | None = None
    wait_seconds: int | None = None
    recovery_steps: list[str] | None = None
    escalation_reason: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert decision to dictionary."""
        return {
            "decision_type": self.decision_type.value,
            "action": self.action,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "new_selector": self.new_selector,
            "new_strategy": self.new_strategy.value if self.new_strategy else None,
            "wait_seconds": self.wait_seconds,
            "recovery_steps": self.recovery_steps,
            "escalation_reason": self.escalation_reason,
            "timestamp": self.timestamp,
        }


@dataclass
class BrowserContext:
    """
    Browser state context for decision making.

    Captures the current browser state including URL, page content,
    error messages, and action history.

    Attributes:
        current_url: Current page URL.
        page_title: Current page title.
        visible_text: Visible text content excerpt.
        error_messages: List of detected error messages.
        current_selector: The selector being operated on.
        selector_found: Whether the selector was found.
        page_html_snippet: Relevant HTML section.
        screenshot_description: Vision model description of page.
        previous_actions: List of previous actions taken.
        failure_count: Number of consecutive failures.
        time_elapsed_seconds: Time spent on current operation.

    Example:
        context = BrowserContext(
            current_url="https://vfs.com/login",
            page_title="Login Page",
            visible_text="Please log in to continue...",
            error_messages=["Invalid selector"],
            current_selector="#login-btn",
            selector_found=False,
            page_html_snippet="<button id='btn-login'>Login</button>",
            screenshot_description="Login form with email and password fields",
            previous_actions=["navigate", "fill_email"],
            failure_count=2,
            time_elapsed_seconds=45,
        )
    """

    current_url: str
    page_title: str
    visible_text: str
    error_messages: list[str]
    current_selector: str
    selector_found: bool
    page_html_snippet: str
    screenshot_description: str
    previous_actions: list[str]
    failure_count: int
    time_elapsed_seconds: int

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary."""
        return {
            "current_url": self.current_url,
            "page_title": self.page_title,
            "visible_text": self.visible_text[:500] if self.visible_text else "",
            "error_messages": self.error_messages,
            "current_selector": self.current_selector,
            "selector_found": self.selector_found,
            "page_html_snippet": self.page_html_snippet[:2000]
            if self.page_html_snippet
            else "",
            "screenshot_description": self.screenshot_description,
            "previous_actions": self.previous_actions[-10:],
            "failure_count": self.failure_count,
            "time_elapsed_seconds": self.time_elapsed_seconds,
        }


@dataclass
class FlowContext:
    """
    Booking flow context for decision making.

    Captures the current booking flow state including site,
    progress, and performance metrics.

    Attributes:
        site: Site code (vfs, idata, bls, kkosmos).
        current_state: Current state in the booking flow.
        target_action: Target action being attempted.
        applicant_data_filled: Fields that have been filled.
        slot_selected: Whether a slot has been selected.
        payment_started: Whether payment has started.
        total_attempts: Total booking attempts.
        success_rate_today: Success rate for current day.
        average_duration_seconds: Average operation duration.

    Example:
        context = FlowContext(
            site="vfs",
            current_state="FILLING_FORM",
            target_action="select_appointment_date",
            applicant_data_filled={"name": True, "passport": True},
            slot_selected=False,
            payment_started=False,
            total_attempts=3,
            success_rate_today=0.75,
            average_duration_seconds=120,
        )
    """

    site: str
    current_state: str
    target_action: str
    applicant_data_filled: dict[str, bool]
    slot_selected: bool
    payment_started: bool
    total_attempts: int
    success_rate_today: float
    average_duration_seconds: int

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary."""
        return {
            "site": self.site,
            "current_state": self.current_state,
            "target_action": self.target_action,
            "applicant_data_filled": self.applicant_data_filled,
            "slot_selected": self.slot_selected,
            "payment_started": self.payment_started,
            "total_attempts": self.total_attempts,
            "success_rate_today": self.success_rate_today,
            "average_duration_seconds": self.average_duration_seconds,
        }


# =============================================================================
# Selector Healing Engine
# =============================================================================


class SelectorHealingEngine:
    """
    AI-powered CSS selector healing.

    Automatically suggests alternative selectors when the original
    selector fails to find the expected element on the page.

    Features:
    - LLM-based HTML analysis
    - Fallback selector generation
    - Historical selector tracking
    - Structure change detection

    Attributes:
        llm: LLM client for AI analysis.
        selector_history: History of selector attempts for learning.

    Example:
        engine = SelectorHealingEngine(llm_client)
        decision = await engine.heal_selector(
            original_selector="#submit",
            page_html="<button class='btn-submit'>Submit</button>",
            element_description="Submit button",
            context=browser_context,
        )
    """

    def __init__(self, llm_client: LLMClient) -> None:
        """
        Initialize selector healing engine.

        Args:
            llm_client: LLM client for AI analysis.
        """
        self.llm = llm_client
        self.selector_history: dict[str, list[dict[str, Any]]] = {}

    async def heal_selector(
        self,
        original_selector: str,
        page_html: str,
        element_description: str,
        context: BrowserContext,
    ) -> AIDecision:
        """
        Find alternative selector for a broken one.

        Args:
            original_selector: The selector that failed.
            page_html: HTML content of the page.
            element_description: Description of expected element.
            context: Current browser context.

        Returns:
            AIDecision with healing recommendation.
        """
        prompt = f"""A CSS/XPath selector is not finding the expected element.

Original selector: {original_selector}
Element description: {element_description}
Current URL: {context.current_url}

Relevant HTML snippet:
```html
{page_html[:3000]}
```

Previous actions: {context.previous_actions[-5:]}

Please analyze the HTML and suggest:
1. An alternative selector that would find this element
2. Multiple fallback selectors in order of preference
3. Whether this might indicate a site structure change

Respond in JSON:
{{
    "primary_selector": "...",
    "fallback_selectors": ["...", "..."],
    "confidence": 0.0-1.0,
    "is_structure_change": true/false,
    "reasoning": "..."
}}"""

        try:
            response = await self.llm.complete(
                prompt, context={"site": context.current_url}
            )
            result = json.loads(response)

            # Track for learning
            self._track_selector_attempt(original_selector, result)

            logger.info(
                "selector_healing_success",
                original=original_selector,
                healed=result.get("primary_selector"),
                confidence=result.get("confidence"),
            )

            return AIDecision(
                decision_type=DecisionType.SELECTOR_HEAL,
                action="try_alternative_selector",
                confidence=result.get("confidence", 0.5),
                reasoning=result.get("reasoning", ""),
                new_selector=result.get("primary_selector"),
            )

        except json.JSONDecodeError:
            logger.warning(
                "selector_healing_parse_error",
                original=original_selector,
            )
            return AIDecision(
                decision_type=DecisionType.HUMAN_ESCALATION,
                action="manual_selector_update",
                confidence=0.0,
                reasoning="Failed to parse AI response",
                escalation_reason="Selector healing failed, manual update required",
            )

    def _track_selector_attempt(
        self, original: str, result: dict[str, Any]
    ) -> None:
        """Track selector attempt for learning."""
        if original not in self.selector_history:
            self.selector_history[original] = []

        self.selector_history[original].append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "suggested": result.get("primary_selector"),
                "confidence": result.get("confidence"),
            }
        )

        # Keep last 20 per selector
        if len(self.selector_history[original]) > 20:
            self.selector_history[original] = self.selector_history[original][-20:]

    def get_selector_recommendations(
        self, site: str, element_type: str
    ) -> list[str]:
        """
        Get recommended selectors based on history.

        Args:
            site: Site code.
            element_type: Type of element.

        Returns:
            List of recommended selectors.
        """
        history_key = f"{site}_{element_type}"

        if history_key in self.selector_history:
            successful = [
                h["suggested"]
                for h in self.selector_history[history_key]
                if h.get("confidence", 0) > 0.7
            ]
            return list(set(successful))[:5]

        return []


# =============================================================================
# Strategy Switching Engine
# =============================================================================


class StrategySwitchingEngine:
    """
    Dynamic strategy management.

    Manages booking strategy selection and switching based on
    current conditions, error rates, and AI recommendations.

    Features:
    - Multiple strategy profiles
    - AI-driven strategy evaluation
    - Automatic strategy switching
    - Performance tracking

    Strategy Profiles:
    - AGGRESSIVE: Fast, risk tolerant for high-demand slots
    - CONSERVATIVE: Slow, safe, default approach
    - STEALTH: Maximum evasion when detection suspected
    - RECOVERY: Post-ban recovery mode

    Example:
        engine = StrategySwitchingEngine(llm_client)
        decision = await engine.evaluate_strategy(
            context=flow_context,
            browser_context=browser_context,
            recent_errors=["timeout", "rate_limit"],
        )
    """

    STRATEGIES: dict[StrategyType, dict[str, Any]] = {
        StrategyType.AGGRESSIVE: {
            "request_delay_ms": 500,
            "typing_speed": "fast",
            "retry_immediately": True,
            "max_retries": 5,
            "parallel_sessions": True,
        },
        StrategyType.CONSERVATIVE: {
            "request_delay_ms": 2000,
            "typing_speed": "human",
            "retry_immediately": False,
            "max_retries": 3,
            "parallel_sessions": False,
        },
        StrategyType.STEALTH: {
            "request_delay_ms": 5000,
            "typing_speed": "slow_human",
            "retry_immediately": False,
            "max_retries": 2,
            "parallel_sessions": False,
            "random_pauses": True,
            "fake_mouse_movements": True,
        },
        StrategyType.RECOVERY: {
            "request_delay_ms": 10000,
            "typing_speed": "very_slow",
            "retry_immediately": False,
            "max_retries": 1,
            "parallel_sessions": False,
            "new_browser_profile": True,
            "new_proxy": True,
        },
    }

    def __init__(self, llm_client: LLMClient) -> None:
        """
        Initialize strategy switching engine.

        Args:
            llm_client: LLM client for AI analysis.
        """
        self.llm = llm_client
        self.current_strategy = StrategyType.CONSERVATIVE

    async def evaluate_strategy(
        self,
        context: FlowContext,
        browser_context: BrowserContext,
        recent_errors: list[str],
    ) -> AIDecision:
        """
        Evaluate and recommend strategy changes.

        Args:
            context: Current flow context.
            browser_context: Current browser context.
            recent_errors: List of recent error messages.

        Returns:
            AIDecision with strategy recommendation.
        """
        prompt = f"""Evaluate the current booking strategy based on these metrics:

Site: {context.site}
Current Strategy: {self.current_strategy.value}
Current State: {context.current_state}

Recent errors (last 10): {recent_errors[-10:]}
Failure count: {browser_context.failure_count}
Success rate today: {context.success_rate_today}%
Time elapsed: {browser_context.time_elapsed_seconds}s

Strategy options:
- AGGRESSIVE: Fast, risk tolerant, for high-demand slots
- CONSERVATIVE: Slow, safe, default approach
- STEALTH: Maximum evasion, when detection suspected
- RECOVERY: Post-ban recovery mode

Should we switch strategy? If yes, to which one and why?

Respond in JSON:
{{
    "should_switch": true/false,
    "recommended_strategy": "aggressive/conservative/stealth/recovery",
    "confidence": 0.0-1.0,
    "triggers": ["reason1", "reason2"],
    "reasoning": "..."
}}"""

        try:
            response = await self.llm.complete(prompt)
            result = json.loads(response)

            if result.get("should_switch", False):
                new_strategy = StrategyType(result["recommended_strategy"])

                logger.info(
                    "strategy_switch_recommended",
                    current=self.current_strategy.value,
                    recommended=new_strategy.value,
                    confidence=result.get("confidence"),
                    triggers=result.get("triggers", []),
                )

                return AIDecision(
                    decision_type=DecisionType.STRATEGY_SWITCH,
                    action=f"switch_to_{new_strategy.value}",
                    confidence=result.get("confidence", 0.5),
                    reasoning=result.get("reasoning", ""),
                    new_strategy=new_strategy,
                )

            return AIDecision(
                decision_type=DecisionType.CONTINUE,
                action="maintain_current_strategy",
                confidence=result.get("confidence", 0.7),
                reasoning=result.get("reasoning", "Strategy is appropriate"),
            )

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "strategy_evaluation_error",
                error=str(e),
            )
            return AIDecision(
                decision_type=DecisionType.CONTINUE,
                action="maintain_current_strategy",
                confidence=0.5,
                reasoning="Failed to parse AI response, continuing with current strategy",
            )

    def get_strategy_config(
        self, strategy: StrategyType | None = None
    ) -> dict[str, Any]:
        """
        Get configuration for a strategy.

        Args:
            strategy: Strategy to get config for (default: current).

        Returns:
            Strategy configuration dictionary.
        """
        strategy = strategy or self.current_strategy
        return self.STRATEGIES[strategy].copy()

    def apply_strategy(self, strategy: StrategyType) -> dict[str, Any]:
        """
        Apply a new strategy.

        Args:
            strategy: Strategy to apply.

        Returns:
            New strategy configuration.
        """
        self.current_strategy = strategy
        logger.info("strategy_applied", strategy=strategy.value)
        return self.STRATEGIES[strategy]


# =============================================================================
# Anomaly Detection Engine
# =============================================================================


class AnomalyDetectionEngine:
    """
    Bot detection and anomaly analysis.

    Detects potential bot detection mechanisms and recommends
    evasive actions to avoid blocking.

    Features:
    - Rule-based pre-filtering
    - LLM-based deep analysis
    - Detection type classification
    - Anomaly history tracking

    Detection Indicators:
    - High Risk: access denied, bot detected, captcha loop
    - Medium Risk: rate limit, session expired, timeout
    - Behavioral: slow response, fingerprint check

    Example:
        engine = AnomalyDetectionEngine(llm_client)
        decision = await engine.analyze_for_detection(
            browser_context=context,
            network_timing={"response_ms": 5000},
            response_headers={"cf-ray": "abc123"},
        )
    """

    DETECTION_INDICATORS: dict[str, list[str]] = {
        "high_risk": [
            "access denied",
            "unusual activity",
            "security check",
            "automated",
            "bot detected",
            "please verify",
            "captcha loop",
        ],
        "medium_risk": [
            "too many requests",
            "try again later",
            "session expired",
            "timeout",
            "rate limit",
        ],
        "behavioral": [
            "slow response",
            "multiple redirects",
            "javascript challenge",
            "fingerprint check",
        ],
    }

    def __init__(self, llm_client: LLMClient) -> None:
        """
        Initialize anomaly detection engine.

        Args:
            llm_client: LLM client for AI analysis.
        """
        self.llm = llm_client
        self.anomaly_history: list[dict[str, Any]] = []

    async def analyze_for_detection(
        self,
        browser_context: BrowserContext,
        network_timing: dict[str, float],
        response_headers: dict[str, str],
    ) -> AIDecision:
        """
        Analyze for potential bot detection.

        Args:
            browser_context: Current browser context.
            network_timing: Network timing metrics.
            response_headers: HTTP response headers.

        Returns:
            AIDecision with detection analysis.
        """
        # Rule-based pre-check
        risk_level = self._quick_risk_assessment(browser_context)

        if risk_level == "high":
            logger.warning(
                "high_risk_detection_triggered",
                url=browser_context.current_url,
            )
            return AIDecision(
                decision_type=DecisionType.ANOMALY_RESPONSE,
                action="immediate_evasion",
                confidence=0.9,
                reasoning="High-risk detection indicators found",
                new_strategy=StrategyType.RECOVERY,
            )

        # AI-based deep analysis
        prompt = f"""Analyze this browser state for potential bot detection:

URL: {browser_context.current_url}
Page Title: {browser_context.page_title}
Error Messages: {browser_context.error_messages}
Visible Text (excerpt): {browser_context.visible_text[:500]}

Network Timing:
- DNS: {network_timing.get('dns_ms', 'N/A')}ms
- Connect: {network_timing.get('connect_ms', 'N/A')}ms
- Response: {network_timing.get('response_ms', 'N/A')}ms

Response Headers of Interest:
- Server: {response_headers.get('server', 'N/A')}
- CF-Ray: {response_headers.get('cf-ray', 'N/A')}
- X-Frame-Options: {response_headers.get('x-frame-options', 'N/A')}

Previous Actions: {browser_context.previous_actions[-5:]}
Failure Count: {browser_context.failure_count}

Analyze:
1. Is this likely a bot detection page?
2. What type of detection (Cloudflare, custom, rate-limit)?
3. Recommended response strategy?

Respond in JSON:
{{
    "is_detection": true/false,
    "detection_type": "cloudflare/custom/rate_limit/none",
    "risk_level": "high/medium/low",
    "recommended_action": "...",
    "confidence": 0.0-1.0,
    "reasoning": "..."
}}"""

        try:
            response = await self.llm.complete(prompt)
            result = json.loads(response)

            if result.get("is_detection", False):
                self._track_anomaly(browser_context, result)

                if result.get("risk_level") == "high":
                    return AIDecision(
                        decision_type=DecisionType.ANOMALY_RESPONSE,
                        action="full_evasion_protocol",
                        confidence=result.get("confidence", 0.7),
                        reasoning=result.get("reasoning", ""),
                        new_strategy=StrategyType.RECOVERY,
                        recovery_steps=[
                            "close_session",
                            "rotate_proxy",
                            "new_browser_profile",
                            "wait_5_minutes",
                            "retry_with_stealth",
                        ],
                    )
                else:
                    return AIDecision(
                        decision_type=DecisionType.ANOMALY_RESPONSE,
                        action="soft_evasion",
                        confidence=result.get("confidence", 0.6),
                        reasoning=result.get("reasoning", ""),
                        new_strategy=StrategyType.STEALTH,
                        wait_seconds=60,
                    )

            return AIDecision(
                decision_type=DecisionType.CONTINUE,
                action="no_detection_observed",
                confidence=result.get("confidence", 0.8),
                reasoning=result.get("reasoning", "No anomaly detected"),
            )

        except json.JSONDecodeError:
            logger.warning("anomaly_detection_parse_error")
            # Fail safe: assume medium risk
            return AIDecision(
                decision_type=DecisionType.ANOMALY_RESPONSE,
                action="precautionary_slowdown",
                confidence=0.5,
                reasoning="Unable to analyze, taking precaution",
                new_strategy=StrategyType.CONSERVATIVE,
                wait_seconds=30,
            )

    def _quick_risk_assessment(self, context: BrowserContext) -> str:
        """
        Quick rule-based risk assessment.

        Args:
            context: Browser context to assess.

        Returns:
            Risk level: "high", "medium", or "low".
        """
        text_lower = (
            context.visible_text + " ".join(context.error_messages)
        ).lower()

        for indicator in self.DETECTION_INDICATORS["high_risk"]:
            if indicator in text_lower:
                return "high"

        for indicator in self.DETECTION_INDICATORS["medium_risk"]:
            if indicator in text_lower:
                return "medium"

        return "low"

    def _track_anomaly(
        self, context: BrowserContext, analysis: dict[str, Any]
    ) -> None:
        """Track detected anomaly."""
        self.anomaly_history.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "url": context.current_url,
                "detection_type": analysis.get("detection_type"),
                "risk_level": analysis.get("risk_level"),
            }
        )

        # Keep last 100 only
        if len(self.anomaly_history) > 100:
            self.anomaly_history = self.anomaly_history[-100:]


# =============================================================================
# Error Recovery Engine
# =============================================================================


class ErrorRecoveryEngine:
    """
    Intelligent error recovery.

    Classifies errors and determines appropriate recovery actions
    based on error type and context.

    Features:
    - Rule-based error classification
    - AI-powered complex error handling
    - Recovery step generation
    - Human escalation for unrecoverable errors

    Error Categories:
    - Transient: Retry with backoff
    - Rate Limit: Cooldown and retry
    - Session: Re-authenticate
    - Blocked: Full rotation
    - Data: Fix and retry
    - Slot: Find alternative

    Example:
        engine = ErrorRecoveryEngine(llm_client)
        decision = await engine.classify_and_recover(
            error_message="Rate limit exceeded",
            context=flow_context,
            browser_context=browser_context,
        )
    """

    ERROR_CATEGORIES: dict[str, dict[str, Any]] = {
        "transient": {
            "patterns": ["timeout", "network", "503", "502", "connection"],
            "action": "retry_with_backoff",
            "max_retries": 3,
        },
        "rate_limit": {
            "patterns": ["429", "rate limit", "too many", "try later"],
            "action": "cooldown_and_retry",
            "cooldown_seconds": 300,
        },
        "session": {
            "patterns": ["session expired", "login required", "401", "unauthorized"],
            "action": "re_authenticate",
            "max_retries": 2,
        },
        "blocked": {
            "patterns": ["blocked", "banned", "denied", "suspended"],
            "action": "full_rotation",
            "cooldown_seconds": 1800,
        },
        "data": {
            "patterns": ["invalid", "required", "format", "validation"],
            "action": "fix_and_retry",
            "max_retries": 1,
        },
        "slot": {
            "patterns": ["slot taken", "not available", "no appointment"],
            "action": "find_alternative",
            "max_retries": 5,
        },
    }

    def __init__(self, llm_client: LLMClient) -> None:
        """
        Initialize error recovery engine.

        Args:
            llm_client: LLM client for AI analysis.
        """
        self.llm = llm_client

    async def classify_and_recover(
        self,
        error_message: str,
        context: FlowContext,
        browser_context: BrowserContext,
    ) -> AIDecision:
        """
        Classify error and determine recovery strategy.

        Args:
            error_message: The error message to analyze.
            context: Current flow context.
            browser_context: Current browser context.

        Returns:
            AIDecision with recovery recommendation.
        """
        # Quick classification
        category = self._classify_error(error_message)

        if category:
            config = self.ERROR_CATEGORIES[category]

            logger.info(
                "error_classified",
                category=category,
                action=config["action"],
                error=error_message[:100],
            )

            if category == "blocked":
                return AIDecision(
                    decision_type=DecisionType.ERROR_RECOVERY,
                    action="full_rotation_required",
                    confidence=0.9,
                    reasoning=f"Blocked error detected: {error_message}",
                    new_strategy=StrategyType.RECOVERY,
                    wait_seconds=config["cooldown_seconds"],
                    recovery_steps=[
                        "close_session",
                        "mark_account_banned",
                        "rotate_proxy",
                        "new_account",
                        "wait_30_minutes",
                    ],
                )

            if category == "slot":
                return AIDecision(
                    decision_type=DecisionType.RETRY,
                    action="find_alternative_slot",
                    confidence=0.8,
                    reasoning="Slot no longer available, searching for alternatives",
                    recovery_steps=[
                        "return_to_calendar",
                        "search_next_available",
                        "expand_date_range_if_needed",
                    ],
                )

        # AI-based complex error handling
        return await self._ai_error_recovery(error_message, context, browser_context)

    def _classify_error(self, error_message: str) -> str | None:
        """
        Rule-based error classification.

        Args:
            error_message: Error to classify.

        Returns:
            Error category or None if unrecognized.
        """
        error_lower = error_message.lower()

        for category, config in self.ERROR_CATEGORIES.items():
            for pattern in config["patterns"]:
                if pattern in error_lower:
                    return category

        return None

    async def _ai_error_recovery(
        self,
        error_message: str,
        context: FlowContext,
        browser_context: BrowserContext,
    ) -> AIDecision:
        """
        AI-based error recovery for complex cases.

        Args:
            error_message: The error message.
            context: Flow context.
            browser_context: Browser context.

        Returns:
            AIDecision with AI-generated recovery plan.
        """
        prompt = f"""An error occurred during visa booking automation. Analyze and suggest recovery.

Error Message: {error_message}

Context:
- Site: {context.site}
- Current State: {context.current_state}
- Target Action: {context.target_action}
- Total Attempts: {context.total_attempts}
- URL: {browser_context.current_url}
- Previous Actions: {browser_context.previous_actions[-5:]}

What type of error is this? How should we recover?

Consider:
1. Is this recoverable or should we abort?
2. What specific steps should be taken?
3. Should we escalate to human?

Respond in JSON:
{{
    "error_category": "...",
    "is_recoverable": true/false,
    "recovery_action": "...",
    "recovery_steps": ["step1", "step2"],
    "should_escalate": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "..."
}}"""

        try:
            response = await self.llm.complete(prompt)
            result = json.loads(response)

            if result.get("should_escalate", False):
                return AIDecision(
                    decision_type=DecisionType.HUMAN_ESCALATION,
                    action="escalate_to_operator",
                    confidence=result.get("confidence", 0.5),
                    reasoning=result.get("reasoning", ""),
                    escalation_reason=error_message,
                )

            if not result.get("is_recoverable", True):
                return AIDecision(
                    decision_type=DecisionType.ABORT,
                    action="abort_booking",
                    confidence=result.get("confidence", 0.7),
                    reasoning=result.get("reasoning", ""),
                )

            return AIDecision(
                decision_type=DecisionType.ERROR_RECOVERY,
                action=result.get("recovery_action", "retry"),
                confidence=result.get("confidence", 0.6),
                reasoning=result.get("reasoning", ""),
                recovery_steps=result.get("recovery_steps", []),
            )

        except json.JSONDecodeError:
            logger.warning("error_recovery_parse_error", error=error_message[:100])
            return AIDecision(
                decision_type=DecisionType.RETRY,
                action="generic_retry",
                confidence=0.4,
                reasoning="Unable to parse AI response, attempting generic retry",
            )


# =============================================================================
# Main AI Decision Engine
# =============================================================================


class AIDecisionEngine:
    """
    Main AI decision orchestrator.

    Coordinates all sub-engines to provide intelligent decision
    making for visa booking automation.

    Features:
    - Unified decision interface
    - Sub-engine routing
    - Decision history tracking
    - Confidence thresholds
    - Human escalation

    Sub-Engines:
    - SelectorHealingEngine: CSS selector recovery
    - StrategySwitchingEngine: Strategy management
    - AnomalyDetectionEngine: Bot detection
    - ErrorRecoveryEngine: Error handling

    Example:
        engine = AIDecisionEngine(llm_config)

        decision = await engine.make_decision(
            decision_request="evaluate strategy",
            flow_context=flow_ctx,
            browser_context=browser_ctx,
            additional_data={"errors": ["timeout", "rate_limit"]},
        )

        if decision.decision_type == DecisionType.STRATEGY_SWITCH:
            apply_new_strategy(decision.new_strategy)

        await engine.close()

    Attributes:
        llm: LLM client instance.
        selector_healer: Selector healing engine.
        strategy_switcher: Strategy switching engine.
        anomaly_detector: Anomaly detection engine.
        error_recovery: Error recovery engine.
        decision_history: List of past decisions.
        _closed: Whether the engine has been closed.
    """

    # Confidence threshold for human escalation
    CONFIDENCE_THRESHOLD = 0.5

    def __init__(self, llm_config: LLMConfig) -> None:
        """
        Initialize AI Decision Engine.

        Args:
            llm_config: Configuration for the LLM provider.
        """
        self.llm = LLMClientFactory.create(llm_config)
        self._closed = False

        # Sub-engines
        self.selector_healer = SelectorHealingEngine(self.llm)
        self.strategy_switcher = StrategySwitchingEngine(self.llm)
        self.anomaly_detector = AnomalyDetectionEngine(self.llm)
        self.error_recovery = ErrorRecoveryEngine(self.llm)

        # Decision history
        self.decision_history: list[AIDecision] = []

        logger.info(
            "ai_decision_engine_initialized",
            provider=llm_config.provider.value,
            model=llm_config.model,
        )

    async def make_decision(
        self,
        decision_request: str,
        flow_context: FlowContext,
        browser_context: BrowserContext,
        additional_data: dict[str, Any] | None = None,
    ) -> AIDecision:
        """
        Make a decision based on the request and context.

        Routes to the appropriate sub-engine based on the request type
        and applies confidence thresholds for human escalation.

        Args:
            decision_request: Description of decision needed.
            flow_context: Current booking flow context.
            browser_context: Current browser context.
            additional_data: Extra data for specific decision types.

        Returns:
            AIDecision with recommendation and confidence.

        Example:
            # Selector healing
            decision = await engine.make_decision(
                "selector not found",
                flow_ctx,
                browser_ctx,
                {"selector": "#submit", "html": "<div>...", "description": "Submit button"},
            )

            # Strategy evaluation
            decision = await engine.make_decision(
                "evaluate strategy",
                flow_ctx,
                browser_ctx,
                {"errors": ["timeout", "429"]},
            )
        """
        if self._closed:
            logger.error("decision_engine_closed")
            return AIDecision(
                decision_type=DecisionType.HUMAN_ESCALATION,
                action="engine_closed",
                confidence=0.0,
                reasoning="Decision engine has been closed",
                escalation_reason="Engine is not available",
            )

        additional_data = additional_data or {}

        # Route to appropriate engine
        request_lower = decision_request.lower()

        if "selector" in request_lower:
            decision = await self.selector_healer.heal_selector(
                original_selector=additional_data.get("selector", ""),
                page_html=additional_data.get("html", ""),
                element_description=additional_data.get("description", ""),
                context=browser_context,
            )

        elif "strategy" in request_lower:
            decision = await self.strategy_switcher.evaluate_strategy(
                context=flow_context,
                browser_context=browser_context,
                recent_errors=additional_data.get("errors", []),
            )

        elif "anomaly" in request_lower or "detection" in request_lower:
            decision = await self.anomaly_detector.analyze_for_detection(
                browser_context=browser_context,
                network_timing=additional_data.get("network_timing", {}),
                response_headers=additional_data.get("headers", {}),
            )

        elif "error" in request_lower:
            decision = await self.error_recovery.classify_and_recover(
                error_message=additional_data.get("error", ""),
                context=flow_context,
                browser_context=browser_context,
            )

        else:
            # General decision
            decision = await self._general_decision(
                request=decision_request,
                flow_context=flow_context,
                browser_context=browser_context,
            )

        # Track decision
        self._track_decision(decision)

        # Apply confidence threshold
        if decision.confidence < self.CONFIDENCE_THRESHOLD:
            logger.warning(
                "low_confidence_escalation",
                original_type=decision.decision_type.value,
                confidence=decision.confidence,
            )
            decision = AIDecision(
                decision_type=DecisionType.HUMAN_ESCALATION,
                action="low_confidence_escalation",
                confidence=decision.confidence,
                reasoning=f"Original decision had low confidence: {decision.reasoning}",
                escalation_reason=decision_request,
            )

        return decision

    async def _general_decision(
        self,
        request: str,
        flow_context: FlowContext,
        browser_context: BrowserContext,
    ) -> AIDecision:
        """
        Make a general decision for unclassified requests.

        Args:
            request: Decision request description.
            flow_context: Flow context.
            browser_context: Browser context.

        Returns:
            AIDecision with general recommendation.
        """
        prompt = f"""Make a decision for this automation scenario:

Request: {request}

Flow Context:
- Site: {flow_context.site}
- State: {flow_context.current_state}
- Target: {flow_context.target_action}
- Attempts: {flow_context.total_attempts}

Browser Context:
- URL: {browser_context.current_url}
- Errors: {browser_context.error_messages}
- Failures: {browser_context.failure_count}

What should we do?

Respond in JSON:
{{
    "decision": "continue/retry/wait/abort/escalate",
    "action": "specific action to take",
    "confidence": 0.0-1.0,
    "reasoning": "..."
}}"""

        try:
            response = await self.llm.complete(prompt)
            result = json.loads(response)

            decision_map = {
                "continue": DecisionType.CONTINUE,
                "retry": DecisionType.RETRY,
                "wait": DecisionType.WAIT,
                "abort": DecisionType.ABORT,
                "escalate": DecisionType.HUMAN_ESCALATION,
            }

            return AIDecision(
                decision_type=decision_map.get(
                    result.get("decision", "continue"), DecisionType.CONTINUE
                ),
                action=result.get("action", "continue_flow"),
                confidence=result.get("confidence", 0.5),
                reasoning=result.get("reasoning", ""),
            )

        except json.JSONDecodeError:
            logger.warning("general_decision_parse_error")
            return AIDecision(
                decision_type=DecisionType.CONTINUE,
                action="default_continue",
                confidence=0.3,
                reasoning="Failed to parse AI response",
            )

    def _track_decision(self, decision: AIDecision) -> None:
        """Track decision in history."""
        self.decision_history.append(decision)

        # Keep last 500
        if len(self.decision_history) > 500:
            self.decision_history = self.decision_history[-500:]

        logger.debug(
            "decision_tracked",
            type=decision.decision_type.value,
            action=decision.action,
            confidence=decision.confidence,
        )

    def get_decision_stats(self) -> dict[str, Any]:
        """
        Get decision statistics.

        Returns:
            Dictionary with decision statistics.
        """
        if not self.decision_history:
            return {}

        type_counts: dict[str, int] = {}
        confidence_sum = 0.0

        for d in self.decision_history:
            type_counts[d.decision_type.value] = (
                type_counts.get(d.decision_type.value, 0) + 1
            )
            confidence_sum += d.confidence

        return {
            "total_decisions": len(self.decision_history),
            "by_type": type_counts,
            "average_confidence": confidence_sum / len(self.decision_history),
        }

    def get_current_strategy(self) -> StrategyType:
        """Get current strategy from the strategy switcher."""
        return self.strategy_switcher.current_strategy

    def get_strategy_config(
        self, strategy: StrategyType | None = None
    ) -> dict[str, Any]:
        """
        Get configuration for a strategy.

        Args:
            strategy: Strategy to get config for (default: current).

        Returns:
            Strategy configuration dictionary.
        """
        return self.strategy_switcher.get_strategy_config(strategy)

    def apply_strategy(self, strategy: StrategyType) -> dict[str, Any]:
        """
        Apply a new strategy.

        Args:
            strategy: Strategy to apply.

        Returns:
            New strategy configuration.
        """
        return self.strategy_switcher.apply_strategy(strategy)

    async def close(self) -> None:
        """Close the engine and release resources."""
        if not self._closed:
            await self.llm.close()
            self._closed = True
            logger.info("ai_decision_engine_closed")

    async def __aenter__(self) -> "AIDecisionEngine":
        """Async context manager entry."""
        return self

    async def __aexit__(
        self, exc_type: Any, exc_val: Any, exc_tb: Any
    ) -> None:
        """Async context manager exit."""
        await self.close()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Enums
    "DecisionType",
    "StrategyType",
    # Data classes
    "AIDecision",
    "BrowserContext",
    "FlowContext",
    # Engines
    "SelectorHealingEngine",
    "StrategySwitchingEngine",
    "AnomalyDetectionEngine",
    "ErrorRecoveryEngine",
    "AIDecisionEngine",
]
