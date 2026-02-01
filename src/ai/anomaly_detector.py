"""
VISE OS Anomaly Detector for Bot Detection Evasion.

AI-powered anomaly detection system for identifying bot detection mechanisms
and recommending evasive actions to maintain automation stealth.

Features:
- Rule-based pre-filtering for common detection patterns
- LLM-powered deep analysis for complex scenarios
- Behavioral pattern tracking (timing, mouse, navigation)
- Network anomaly detection (timing, headers, redirects)
- Fingerprint check detection
- Evasion strategy recommendations
- Historical anomaly tracking for learning

Detection Types:
- Cloudflare: JS challenges, Turnstile, rate limits
- Custom: Site-specific bot detection
- Behavioral: Timing analysis, mouse tracking
- Fingerprint: Canvas, WebGL, audio fingerprinting
- Rate Limit: Request frequency based blocking

Usage:
    from src.ai.anomaly_detector import AnomalyDetector, AnomalyResult

    detector = AnomalyDetector(llm_client)

    result = await detector.analyze(
        browser_context=context,
        network_timing={"response_ms": 2000},
        response_headers={"cf-ray": "abc123"},
        behavioral_data={"typing_speed": 150},
    )

    if result.is_detected:
        print(f"Detection type: {result.detection_type}")
        print(f"Evasion strategy: {result.evasion_strategy}")

    await detector.close()
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

from src.ai.providers import LLMClient

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


# =============================================================================
# Enums
# =============================================================================


class DetectionType(str, Enum):
    """Types of bot detection mechanisms."""

    CLOUDFLARE = "cloudflare"
    CLOUDFLARE_TURNSTILE = "cloudflare_turnstile"
    AKAMAI = "akamai"
    IMPERVA = "imperva"
    DATADOME = "datadome"
    PERIMETERX = "perimeterx"
    RATE_LIMIT = "rate_limit"
    BEHAVIORAL = "behavioral"
    FINGERPRINT = "fingerprint"
    CUSTOM = "custom"
    NONE = "none"


class RiskLevel(str, Enum):
    """Risk level for detected anomalies."""

    CRITICAL = "critical"  # Immediate action required
    HIGH = "high"          # Significant detection risk
    MEDIUM = "medium"      # Elevated caution needed
    LOW = "low"            # Minor concern
    NONE = "none"          # No detection observed


class EvasionStrategy(str, Enum):
    """Evasion strategies for bot detection."""

    IMMEDIATE_ABORT = "immediate_abort"        # Stop and rotate everything
    FULL_ROTATION = "full_rotation"            # New proxy, account, profile
    PROXY_ROTATION = "proxy_rotation"          # Change proxy only
    PROFILE_ROTATION = "profile_rotation"      # New browser profile
    COOLDOWN = "cooldown"                      # Wait before continuing
    SLOW_DOWN = "slow_down"                    # Reduce request rate
    HUMANIZE = "humanize"                      # Add more human-like behavior
    CONTINUE = "continue"                      # No action needed
    CAPTCHA_SOLVE = "captcha_solve"            # Solve captcha and continue


class BehaviorCategory(str, Enum):
    """Categories of behavioral signals."""

    TIMING = "timing"
    MOUSE = "mouse"
    KEYBOARD = "keyboard"
    NAVIGATION = "navigation"
    SCROLL = "scroll"
    FOCUS = "focus"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class AnomalyResult:
    """
    Result of anomaly detection analysis.

    Attributes:
        is_detected: Whether bot detection was identified.
        detection_type: Type of detection mechanism.
        risk_level: Assessed risk level.
        evasion_strategy: Recommended evasion strategy.
        confidence: Confidence score (0.0 - 1.0).
        reasoning: Explanation of the analysis.
        indicators: List of detected indicators.
        wait_seconds: Recommended wait time before retry.
        recovery_steps: Ordered list of recovery actions.
        timestamp: When the analysis was performed.

    Example:
        result = AnomalyResult(
            is_detected=True,
            detection_type=DetectionType.CLOUDFLARE,
            risk_level=RiskLevel.HIGH,
            evasion_strategy=EvasionStrategy.FULL_ROTATION,
            confidence=0.85,
            reasoning="CF-Ray header present with JS challenge page",
            indicators=["cf-ray header", "challenge page detected"],
            wait_seconds=300,
            recovery_steps=["close_session", "rotate_proxy", "new_profile"],
        )
    """

    is_detected: bool
    detection_type: DetectionType
    risk_level: RiskLevel
    evasion_strategy: EvasionStrategy
    confidence: float
    reasoning: str
    indicators: list[str]
    wait_seconds: int = 0
    recovery_steps: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "is_detected": self.is_detected,
            "detection_type": self.detection_type.value,
            "risk_level": self.risk_level.value,
            "evasion_strategy": self.evasion_strategy.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "indicators": self.indicators,
            "wait_seconds": self.wait_seconds,
            "recovery_steps": self.recovery_steps,
            "timestamp": self.timestamp,
        }


@dataclass
class BrowserContext:
    """
    Browser state context for anomaly detection.

    Attributes:
        current_url: Current page URL.
        page_title: Current page title.
        visible_text: Visible text content excerpt.
        error_messages: List of detected error messages.
        page_html_snippet: Relevant HTML section.
        previous_actions: List of previous actions taken.
        failure_count: Number of consecutive failures.
        time_elapsed_seconds: Time spent on current operation.
    """

    current_url: str
    page_title: str
    visible_text: str
    error_messages: list[str]
    page_html_snippet: str
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
            "page_html_snippet": (
                self.page_html_snippet[:2000] if self.page_html_snippet else ""
            ),
            "previous_actions": self.previous_actions[-10:],
            "failure_count": self.failure_count,
            "time_elapsed_seconds": self.time_elapsed_seconds,
        }


@dataclass
class BehavioralData:
    """
    Behavioral data for pattern analysis.

    Attributes:
        typing_speeds_ms: List of typing intervals in milliseconds.
        mouse_movements: List of mouse movement patterns.
        click_delays_ms: List of click delay times.
        scroll_patterns: List of scroll patterns.
        navigation_timing_ms: List of navigation timing.
        idle_periods_seconds: List of idle periods.
        focus_changes: Number of focus changes.
    """

    typing_speeds_ms: list[int] = field(default_factory=list)
    mouse_movements: list[dict[str, Any]] = field(default_factory=list)
    click_delays_ms: list[int] = field(default_factory=list)
    scroll_patterns: list[dict[str, Any]] = field(default_factory=list)
    navigation_timing_ms: list[int] = field(default_factory=list)
    idle_periods_seconds: list[float] = field(default_factory=list)
    focus_changes: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert behavioral data to dictionary."""
        return {
            "typing_speeds_ms": self.typing_speeds_ms[-20:],
            "mouse_movements_count": len(self.mouse_movements),
            "click_delays_ms": self.click_delays_ms[-20:],
            "scroll_patterns_count": len(self.scroll_patterns),
            "navigation_timing_ms": self.navigation_timing_ms[-10:],
            "idle_periods_seconds": self.idle_periods_seconds[-10:],
            "focus_changes": self.focus_changes,
        }


@dataclass
class AnomalyHistoryEntry:
    """
    Historical entry for anomaly tracking.

    Attributes:
        detection_type: Type of detection observed.
        risk_level: Assessed risk level.
        url: URL where anomaly was detected.
        site: Site identifier.
        evasion_successful: Whether evasion was successful.
        timestamp: When the anomaly occurred.
    """

    detection_type: DetectionType
    risk_level: RiskLevel
    url: str
    site: str
    evasion_successful: bool | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# =============================================================================
# Detection Patterns
# =============================================================================


class DetectionPatterns:
    """
    Detection pattern definitions for rule-based analysis.

    Contains patterns for identifying various bot detection mechanisms
    through text, headers, and behavioral signals.
    """

    # Text-based detection indicators
    TEXT_INDICATORS: dict[str, list[str]] = {
        "critical": [
            "access denied",
            "bot detected",
            "automated access",
            "suspicious activity",
            "blocked",
            "banned",
            "forbidden",
        ],
        "high": [
            "unusual activity",
            "security check",
            "verify you are human",
            "please verify",
            "captcha required",
            "challenge page",
        ],
        "medium": [
            "too many requests",
            "try again later",
            "rate limit",
            "slow down",
            "wait before",
            "temporarily unavailable",
        ],
        "low": [
            "session expired",
            "timeout",
            "connection reset",
            "please refresh",
        ],
    }

    # Header-based detection indicators
    HEADER_INDICATORS: dict[str, list[str]] = {
        "cloudflare": ["cf-ray", "cf-request-id", "__cf_bm"],
        "akamai": ["akamai-grn", "x-akamai-session-info"],
        "imperva": ["x-iinfo", "incap_ses_"],
        "datadome": ["x-datadome", "datadome"],
        "perimeterx": ["_px", "x-px-"],
    }

    # JavaScript challenge patterns in HTML
    JS_CHALLENGE_PATTERNS: list[str] = [
        "challenge-platform",
        "_cf_chl",
        "cf-spinner",
        "jschl-answer",
        "turnstile",
        "recaptcha",
        "hcaptcha",
        "geetest",
        "funcaptcha",
    ]

    # Suspicious URL patterns
    SUSPICIOUS_URL_PATTERNS: list[str] = [
        "/cdn-cgi/",
        "/challenge",
        "/verify",
        "/blocked",
        "/captcha",
        "/security-check",
    ]

    # Normal timing thresholds (milliseconds)
    TIMING_THRESHOLDS: dict[str, dict[str, int]] = {
        "typing": {"min": 50, "max": 300, "human_avg": 150},
        "click": {"min": 100, "max": 2000, "human_avg": 500},
        "navigation": {"min": 500, "max": 30000, "human_avg": 5000},
        "response": {"min": 100, "max": 10000, "suspicious": 30000},
    }


# =============================================================================
# Behavioral Analyzer
# =============================================================================


class BehavioralAnalyzer:
    """
    Utility class for analyzing behavioral patterns.

    Detects anomalies in user behavior that might trigger bot detection.
    """

    @staticmethod
    def analyze_typing(speeds_ms: list[int]) -> dict[str, Any]:
        """
        Analyze typing speed patterns.

        Args:
            speeds_ms: List of typing intervals in milliseconds.

        Returns:
            Analysis results with bot-like indicators.
        """
        if not speeds_ms or len(speeds_ms) < 3:
            return {"suspicious": False, "reason": "insufficient_data"}

        avg = statistics.mean(speeds_ms)
        std_dev = statistics.stdev(speeds_ms) if len(speeds_ms) > 1 else 0

        thresholds = DetectionPatterns.TIMING_THRESHOLDS["typing"]

        # Check for bot-like patterns
        suspicious = False
        reasons = []

        # Too fast or too slow
        if avg < thresholds["min"]:
            suspicious = True
            reasons.append("typing_too_fast")
        elif avg > thresholds["max"]:
            suspicious = True
            reasons.append("typing_too_slow")

        # Too consistent (low variance = bot)
        if std_dev < 10 and len(speeds_ms) > 5:
            suspicious = True
            reasons.append("typing_too_consistent")

        # Check for uniform intervals (exact same delay)
        unique_speeds = len(set(speeds_ms))
        if unique_speeds < len(speeds_ms) * 0.3:
            suspicious = True
            reasons.append("repetitive_timing")

        return {
            "suspicious": suspicious,
            "average_ms": avg,
            "std_dev": std_dev,
            "reasons": reasons,
        }

    @staticmethod
    def analyze_mouse(movements: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Analyze mouse movement patterns.

        Args:
            movements: List of mouse movement data points.

        Returns:
            Analysis results with bot-like indicators.
        """
        if not movements or len(movements) < 5:
            return {"suspicious": False, "reason": "insufficient_data"}

        suspicious = False
        reasons = []

        # Check for linear movements (bot-like)
        linear_count = 0
        for i in range(len(movements) - 2):
            p1 = movements[i]
            p2 = movements[i + 1]
            p3 = movements[i + 2]

            if "x" in p1 and "y" in p1:
                # Check collinearity
                dx1 = p2.get("x", 0) - p1.get("x", 0)
                dy1 = p2.get("y", 0) - p1.get("y", 0)
                dx2 = p3.get("x", 0) - p2.get("x", 0)
                dy2 = p3.get("y", 0) - p2.get("y", 0)

                # Cross product check for collinearity
                cross = dx1 * dy2 - dy1 * dx2
                if abs(cross) < 10:  # Nearly collinear
                    linear_count += 1

        if linear_count > len(movements) * 0.5:
            suspicious = True
            reasons.append("linear_movements")

        # Check for lack of micro-movements
        small_movements = sum(
            1
            for m in movements
            if abs(m.get("dx", 0)) < 5 and abs(m.get("dy", 0)) < 5
        )
        if small_movements < len(movements) * 0.1:
            suspicious = True
            reasons.append("no_micro_movements")

        return {
            "suspicious": suspicious,
            "movement_count": len(movements),
            "linear_count": linear_count,
            "reasons": reasons,
        }

    @staticmethod
    def analyze_navigation(timing_ms: list[int]) -> dict[str, Any]:
        """
        Analyze navigation timing patterns.

        Args:
            timing_ms: List of navigation timing in milliseconds.

        Returns:
            Analysis results with bot-like indicators.
        """
        if not timing_ms or len(timing_ms) < 2:
            return {"suspicious": False, "reason": "insufficient_data"}

        avg = statistics.mean(timing_ms)
        suspicious = False
        reasons = []

        thresholds = DetectionPatterns.TIMING_THRESHOLDS["navigation"]

        # Too fast navigation
        if avg < thresholds["min"]:
            suspicious = True
            reasons.append("navigation_too_fast")

        # Check for exact same intervals (bot pattern)
        if len(set(timing_ms)) == 1 and len(timing_ms) > 3:
            suspicious = True
            reasons.append("identical_intervals")

        return {
            "suspicious": suspicious,
            "average_ms": avg,
            "reasons": reasons,
        }


# =============================================================================
# Anomaly Detector
# =============================================================================


class AnomalyDetector:
    """
    AI-powered anomaly detector for bot detection evasion.

    Combines rule-based pattern matching with LLM analysis to detect
    bot detection mechanisms and recommend evasion strategies.

    Features:
    - Multi-layer detection (text, headers, behavior, network)
    - LLM-powered complex scenario analysis
    - Historical tracking for pattern learning
    - Confidence-based recommendations
    - Evasion strategy generation

    Attributes:
        llm: LLM client for AI analysis.
        history: Historical anomaly entries.
        behavioral_analyzer: Behavioral pattern analyzer.
        max_history: Maximum history entries.

    Example:
        detector = AnomalyDetector(llm_client)

        result = await detector.analyze(
            browser_context=context,
            network_timing={"response_ms": 5000},
            response_headers={"cf-ray": "abc123"},
        )

        if result.is_detected:
            apply_evasion(result.evasion_strategy)
    """

    # System prompt for LLM analysis
    SYSTEM_PROMPT = """You are an expert bot detection analyst for web automation.

Your task is to analyze browser states, network responses, and behavioral data
to identify potential bot detection mechanisms and recommend evasion strategies.

Key detection systems to identify:
1. Cloudflare (JS challenges, Turnstile, rate limits)
2. Akamai Bot Manager
3. Imperva/Incapsula
4. DataDome
5. PerimeterX
6. Custom site-specific detection

Analysis focus:
1. Page content for challenge/block indicators
2. Response headers for WAF signatures
3. Behavioral anomalies that trigger detection
4. Network timing anomalies
5. Fingerprint collection attempts

Always respond with structured JSON for consistent parsing."""

    def __init__(
        self,
        llm_client: LLMClient,
        max_history: int = 200,
    ) -> None:
        """
        Initialize anomaly detector.

        Args:
            llm_client: LLM client for AI analysis.
            max_history: Maximum history entries (default: 200).
        """
        self.llm = llm_client
        self.max_history = max_history
        self.behavioral_analyzer = BehavioralAnalyzer()
        self.history: list[AnomalyHistoryEntry] = []

        logger.info("anomaly_detector_initialized")

    async def analyze(
        self,
        browser_context: BrowserContext,
        network_timing: dict[str, float] | None = None,
        response_headers: dict[str, str] | None = None,
        behavioral_data: BehavioralData | None = None,
        site: str = "",
    ) -> AnomalyResult:
        """
        Analyze for potential bot detection.

        Performs multi-layer analysis including rule-based pattern matching
        and LLM-powered deep analysis for complex scenarios.

        Args:
            browser_context: Current browser context.
            network_timing: Network timing metrics.
            response_headers: HTTP response headers.
            behavioral_data: Behavioral data for analysis.
            site: Site identifier.

        Returns:
            AnomalyResult with detection analysis.

        Example:
            result = await detector.analyze(
                browser_context=context,
                network_timing={"response_ms": 2000},
                response_headers={"cf-ray": "abc123"},
            )
        """
        network_timing = network_timing or {}
        response_headers = response_headers or {}
        behavioral_data = behavioral_data or BehavioralData()

        # Phase 1: Rule-based quick assessment
        quick_result = self._rule_based_analysis(
            browser_context=browser_context,
            response_headers=response_headers,
        )

        # If critical detection found, return immediately
        if quick_result.risk_level == RiskLevel.CRITICAL:
            logger.warning(
                "critical_detection_found",
                detection_type=quick_result.detection_type.value,
                url=browser_context.current_url,
            )
            self._track_anomaly(quick_result, browser_context.current_url, site)
            return quick_result

        # Phase 2: Behavioral analysis
        behavioral_result = self._analyze_behavior(behavioral_data)

        # Phase 3: Network timing analysis
        network_result = self._analyze_network_timing(network_timing)

        # Combine results for final assessment
        combined_risk = self._combine_risk_levels(
            quick_result.risk_level,
            behavioral_result.get("risk_level", RiskLevel.NONE),
            network_result.get("risk_level", RiskLevel.NONE),
        )

        # Phase 4: If elevated risk, use LLM for deep analysis
        if combined_risk in [RiskLevel.HIGH, RiskLevel.MEDIUM]:
            try:
                llm_result = await self._llm_analysis(
                    browser_context=browser_context,
                    network_timing=network_timing,
                    response_headers=response_headers,
                    behavioral_data=behavioral_data,
                    preliminary_findings={
                        "quick_result": quick_result.to_dict(),
                        "behavioral": behavioral_result,
                        "network": network_result,
                    },
                )
                self._track_anomaly(llm_result, browser_context.current_url, site)
                return llm_result
            except Exception as e:
                logger.warning("llm_analysis_failed", error=str(e))
                # Fall through to combined result

        # Construct final result from rule-based analysis
        final_result = self._construct_result(
            quick_result=quick_result,
            behavioral_result=behavioral_result,
            network_result=network_result,
            combined_risk=combined_risk,
        )

        self._track_anomaly(final_result, browser_context.current_url, site)
        return final_result

    def _rule_based_analysis(
        self,
        browser_context: BrowserContext,
        response_headers: dict[str, str],
    ) -> AnomalyResult:
        """
        Perform rule-based pattern matching.

        Args:
            browser_context: Browser context.
            response_headers: HTTP response headers.

        Returns:
            AnomalyResult from rule-based analysis.
        """
        indicators: list[str] = []
        detection_type = DetectionType.NONE
        risk_level = RiskLevel.NONE

        # Combine text for analysis
        text_content = (
            browser_context.visible_text.lower()
            + " "
            + " ".join(browser_context.error_messages).lower()
            + " "
            + browser_context.page_title.lower()
        )

        # Check text indicators
        for level, patterns in DetectionPatterns.TEXT_INDICATORS.items():
            for pattern in patterns:
                if pattern in text_content:
                    indicators.append(f"text:{pattern}")
                    if level == "critical":
                        risk_level = RiskLevel.CRITICAL
                    elif level == "high" and risk_level != RiskLevel.CRITICAL:
                        risk_level = RiskLevel.HIGH
                    elif level == "medium" and risk_level not in [
                        RiskLevel.CRITICAL,
                        RiskLevel.HIGH,
                    ]:
                        risk_level = RiskLevel.MEDIUM
                    elif level == "low" and risk_level == RiskLevel.NONE:
                        risk_level = RiskLevel.LOW

        # Check header indicators
        headers_lower = {k.lower(): v for k, v in response_headers.items()}
        for provider, patterns in DetectionPatterns.HEADER_INDICATORS.items():
            for pattern in patterns:
                if any(pattern.lower() in h for h in headers_lower):
                    indicators.append(f"header:{pattern}")
                    detection_type = DetectionType(provider)
                    if risk_level == RiskLevel.NONE:
                        risk_level = RiskLevel.MEDIUM

        # Check URL patterns
        url_lower = browser_context.current_url.lower()
        for pattern in DetectionPatterns.SUSPICIOUS_URL_PATTERNS:
            if pattern in url_lower:
                indicators.append(f"url:{pattern}")
                if risk_level == RiskLevel.NONE:
                    risk_level = RiskLevel.MEDIUM

        # Check HTML for JS challenges
        html_lower = browser_context.page_html_snippet.lower()
        for pattern in DetectionPatterns.JS_CHALLENGE_PATTERNS:
            if pattern in html_lower:
                indicators.append(f"js_challenge:{pattern}")
                if detection_type == DetectionType.NONE:
                    detection_type = DetectionType.CUSTOM
                if risk_level in [RiskLevel.NONE, RiskLevel.LOW]:
                    risk_level = RiskLevel.MEDIUM

        # Determine evasion strategy
        evasion_strategy = self._determine_evasion_strategy(risk_level, detection_type)

        return AnomalyResult(
            is_detected=risk_level != RiskLevel.NONE,
            detection_type=detection_type,
            risk_level=risk_level,
            evasion_strategy=evasion_strategy,
            confidence=self._calculate_confidence(indicators, risk_level),
            reasoning=f"Rule-based analysis found {len(indicators)} indicators",
            indicators=indicators,
            wait_seconds=self._calculate_wait_time(risk_level),
            recovery_steps=self._get_recovery_steps(evasion_strategy),
        )

    def _analyze_behavior(
        self, behavioral_data: BehavioralData
    ) -> dict[str, Any]:
        """
        Analyze behavioral patterns for anomalies.

        Args:
            behavioral_data: Behavioral data to analyze.

        Returns:
            Analysis results dictionary.
        """
        suspicious_categories: list[str] = []
        reasons: list[str] = []

        # Typing analysis
        typing_result = self.behavioral_analyzer.analyze_typing(
            behavioral_data.typing_speeds_ms
        )
        if typing_result.get("suspicious"):
            suspicious_categories.append(BehaviorCategory.TYPING.value)
            reasons.extend(typing_result.get("reasons", []))

        # Mouse analysis
        mouse_result = self.behavioral_analyzer.analyze_mouse(
            behavioral_data.mouse_movements
        )
        if mouse_result.get("suspicious"):
            suspicious_categories.append(BehaviorCategory.MOUSE.value)
            reasons.extend(mouse_result.get("reasons", []))

        # Navigation analysis
        nav_result = self.behavioral_analyzer.analyze_navigation(
            behavioral_data.navigation_timing_ms
        )
        if nav_result.get("suspicious"):
            suspicious_categories.append(BehaviorCategory.NAVIGATION.value)
            reasons.extend(nav_result.get("reasons", []))

        # Determine risk level
        risk_level = RiskLevel.NONE
        if len(suspicious_categories) >= 3:
            risk_level = RiskLevel.HIGH
        elif len(suspicious_categories) >= 2:
            risk_level = RiskLevel.MEDIUM
        elif len(suspicious_categories) == 1:
            risk_level = RiskLevel.LOW

        return {
            "suspicious": len(suspicious_categories) > 0,
            "suspicious_categories": suspicious_categories,
            "reasons": reasons,
            "risk_level": risk_level,
        }

    def _analyze_network_timing(
        self, network_timing: dict[str, float]
    ) -> dict[str, Any]:
        """
        Analyze network timing for anomalies.

        Args:
            network_timing: Network timing metrics.

        Returns:
            Analysis results dictionary.
        """
        suspicious = False
        reasons: list[str] = []
        risk_level = RiskLevel.NONE

        response_ms = network_timing.get("response_ms", 0)
        thresholds = DetectionPatterns.TIMING_THRESHOLDS["response"]

        # Check for suspiciously slow response (might indicate challenge)
        if response_ms > thresholds["suspicious"]:
            suspicious = True
            reasons.append("very_slow_response")
            risk_level = RiskLevel.MEDIUM

        # Check for suspiciously fast response (might indicate block page)
        if response_ms > 0 and response_ms < 50:
            suspicious = True
            reasons.append("suspiciously_fast_response")
            risk_level = RiskLevel.LOW

        return {
            "suspicious": suspicious,
            "response_ms": response_ms,
            "reasons": reasons,
            "risk_level": risk_level,
        }

    def _combine_risk_levels(self, *levels: RiskLevel) -> RiskLevel:
        """
        Combine multiple risk levels into a final assessment.

        Args:
            levels: Risk levels to combine.

        Returns:
            Combined risk level.
        """
        priority = {
            RiskLevel.CRITICAL: 5,
            RiskLevel.HIGH: 4,
            RiskLevel.MEDIUM: 3,
            RiskLevel.LOW: 2,
            RiskLevel.NONE: 1,
        }

        max_level = RiskLevel.NONE
        max_priority = 1

        for level in levels:
            if priority.get(level, 1) > max_priority:
                max_priority = priority[level]
                max_level = level

        return max_level

    async def _llm_analysis(
        self,
        browser_context: BrowserContext,
        network_timing: dict[str, float],
        response_headers: dict[str, str],
        behavioral_data: BehavioralData,
        preliminary_findings: dict[str, Any],
    ) -> AnomalyResult:
        """
        Use LLM for deep anomaly analysis.

        Args:
            browser_context: Browser context.
            network_timing: Network timing metrics.
            response_headers: HTTP response headers.
            behavioral_data: Behavioral data.
            preliminary_findings: Results from rule-based analysis.

        Returns:
            AnomalyResult from LLM analysis.
        """
        prompt = f"""Analyze this automation scenario for bot detection:

URL: {browser_context.current_url}
Page Title: {browser_context.page_title}
Error Messages: {browser_context.error_messages}
Visible Text (excerpt): {browser_context.visible_text[:500]}

Network Timing:
- Response: {network_timing.get('response_ms', 'N/A')}ms
- DNS: {network_timing.get('dns_ms', 'N/A')}ms
- Connect: {network_timing.get('connect_ms', 'N/A')}ms

Key Response Headers:
{json.dumps(dict(list(response_headers.items())[:10]), indent=2)}

Behavioral Summary:
{json.dumps(behavioral_data.to_dict(), indent=2)}

Preliminary Findings:
- Indicators: {preliminary_findings['quick_result'].get('indicators', [])}
- Behavioral concerns: {preliminary_findings['behavioral'].get('reasons', [])}

Previous Actions: {browser_context.previous_actions[-5:]}
Failure Count: {browser_context.failure_count}

Analyze and respond in JSON:
{{
    "is_detection": true/false,
    "detection_type": "cloudflare/akamai/imperva/datadome/perimeterx/rate_limit/behavioral/fingerprint/custom/none",
    "risk_level": "critical/high/medium/low/none",
    "evasion_strategy": "immediate_abort/full_rotation/proxy_rotation/profile_rotation/cooldown/slow_down/humanize/continue/captcha_solve",
    "confidence": 0.0-1.0,
    "reasoning": "...",
    "wait_seconds": 0,
    "recovery_steps": ["step1", "step2"]
}}"""

        response = await self.llm.complete(prompt)

        try:
            result = self._parse_llm_response(response)

            logger.info(
                "llm_anomaly_analysis_complete",
                is_detection=result.get("is_detection"),
                detection_type=result.get("detection_type"),
                risk_level=result.get("risk_level"),
            )

            return AnomalyResult(
                is_detected=result.get("is_detection", False),
                detection_type=self._parse_detection_type(
                    result.get("detection_type", "none")
                ),
                risk_level=self._parse_risk_level(result.get("risk_level", "none")),
                evasion_strategy=self._parse_evasion_strategy(
                    result.get("evasion_strategy", "continue")
                ),
                confidence=result.get("confidence", 0.5),
                reasoning=result.get("reasoning", ""),
                indicators=preliminary_findings["quick_result"].get("indicators", []),
                wait_seconds=result.get("wait_seconds", 0),
                recovery_steps=result.get("recovery_steps", []),
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("llm_response_parse_error", error=str(e))
            raise

    def _construct_result(
        self,
        quick_result: AnomalyResult,
        behavioral_result: dict[str, Any],
        network_result: dict[str, Any],
        combined_risk: RiskLevel,
    ) -> AnomalyResult:
        """
        Construct final result from component analyses.

        Args:
            quick_result: Rule-based analysis result.
            behavioral_result: Behavioral analysis result.
            network_result: Network analysis result.
            combined_risk: Combined risk level.

        Returns:
            Final AnomalyResult.
        """
        # Merge indicators
        indicators = list(quick_result.indicators)
        indicators.extend(
            [f"behavior:{r}" for r in behavioral_result.get("reasons", [])]
        )
        indicators.extend(
            [f"network:{r}" for r in network_result.get("reasons", [])]
        )

        # Determine evasion strategy based on combined risk
        evasion_strategy = self._determine_evasion_strategy(
            combined_risk, quick_result.detection_type
        )

        reasoning_parts = [quick_result.reasoning]
        if behavioral_result.get("suspicious"):
            reasoning_parts.append(
                f"Behavioral anomalies in: {behavioral_result.get('suspicious_categories')}"
            )
        if network_result.get("suspicious"):
            reasoning_parts.append(
                f"Network anomalies: {network_result.get('reasons')}"
            )

        return AnomalyResult(
            is_detected=combined_risk != RiskLevel.NONE,
            detection_type=quick_result.detection_type,
            risk_level=combined_risk,
            evasion_strategy=evasion_strategy,
            confidence=self._calculate_confidence(indicators, combined_risk),
            reasoning=" | ".join(reasoning_parts),
            indicators=indicators,
            wait_seconds=self._calculate_wait_time(combined_risk),
            recovery_steps=self._get_recovery_steps(evasion_strategy),
        )

    def _determine_evasion_strategy(
        self, risk_level: RiskLevel, detection_type: DetectionType
    ) -> EvasionStrategy:
        """
        Determine appropriate evasion strategy.

        Args:
            risk_level: Assessed risk level.
            detection_type: Type of detection identified.

        Returns:
            Recommended evasion strategy.
        """
        strategy_map = {
            RiskLevel.CRITICAL: EvasionStrategy.IMMEDIATE_ABORT,
            RiskLevel.HIGH: EvasionStrategy.FULL_ROTATION,
            RiskLevel.MEDIUM: EvasionStrategy.PROXY_ROTATION,
            RiskLevel.LOW: EvasionStrategy.SLOW_DOWN,
            RiskLevel.NONE: EvasionStrategy.CONTINUE,
        }

        base_strategy = strategy_map.get(risk_level, EvasionStrategy.CONTINUE)

        # Special handling for known detection types
        if detection_type == DetectionType.RATE_LIMIT:
            return EvasionStrategy.COOLDOWN
        elif detection_type in [
            DetectionType.CLOUDFLARE_TURNSTILE,
            DetectionType.CLOUDFLARE,
        ]:
            if risk_level in [RiskLevel.MEDIUM, RiskLevel.LOW]:
                return EvasionStrategy.CAPTCHA_SOLVE

        return base_strategy

    def _calculate_confidence(
        self, indicators: list[str], risk_level: RiskLevel
    ) -> float:
        """
        Calculate confidence score based on indicators and risk.

        Args:
            indicators: List of detected indicators.
            risk_level: Assessed risk level.

        Returns:
            Confidence score (0.0 - 1.0).
        """
        base_confidence = {
            RiskLevel.CRITICAL: 0.95,
            RiskLevel.HIGH: 0.85,
            RiskLevel.MEDIUM: 0.70,
            RiskLevel.LOW: 0.55,
            RiskLevel.NONE: 0.40,
        }

        confidence = base_confidence.get(risk_level, 0.5)

        # Adjust based on number of indicators
        if len(indicators) >= 5:
            confidence = min(confidence + 0.1, 0.98)
        elif len(indicators) >= 3:
            confidence = min(confidence + 0.05, 0.95)
        elif len(indicators) == 0:
            confidence = max(confidence - 0.15, 0.3)

        return round(confidence, 2)

    def _calculate_wait_time(self, risk_level: RiskLevel) -> int:
        """
        Calculate recommended wait time before retry.

        Args:
            risk_level: Assessed risk level.

        Returns:
            Wait time in seconds.
        """
        wait_times = {
            RiskLevel.CRITICAL: 1800,  # 30 minutes
            RiskLevel.HIGH: 600,       # 10 minutes
            RiskLevel.MEDIUM: 180,     # 3 minutes
            RiskLevel.LOW: 60,         # 1 minute
            RiskLevel.NONE: 0,
        }
        return wait_times.get(risk_level, 0)

    def _get_recovery_steps(self, strategy: EvasionStrategy) -> list[str]:
        """
        Get ordered recovery steps for a strategy.

        Args:
            strategy: Evasion strategy.

        Returns:
            List of recovery step descriptions.
        """
        steps_map = {
            EvasionStrategy.IMMEDIATE_ABORT: [
                "close_session_immediately",
                "mark_account_suspicious",
                "rotate_proxy",
                "rotate_account",
                "new_browser_profile",
                "wait_minimum_30_minutes",
                "alert_monitoring",
            ],
            EvasionStrategy.FULL_ROTATION: [
                "close_session",
                "rotate_proxy",
                "new_browser_profile",
                "rotate_account",
                "wait_10_minutes",
                "retry_with_stealth_mode",
            ],
            EvasionStrategy.PROXY_ROTATION: [
                "close_session",
                "rotate_proxy",
                "wait_3_minutes",
                "retry_same_account",
            ],
            EvasionStrategy.PROFILE_ROTATION: [
                "close_session",
                "new_browser_profile",
                "randomize_fingerprint",
                "retry",
            ],
            EvasionStrategy.COOLDOWN: [
                "pause_all_requests",
                "wait_5_minutes",
                "resume_with_slower_rate",
            ],
            EvasionStrategy.SLOW_DOWN: [
                "increase_request_delay",
                "add_random_pauses",
                "continue_with_caution",
            ],
            EvasionStrategy.HUMANIZE: [
                "add_mouse_movements",
                "randomize_timing",
                "add_scroll_behavior",
                "continue",
            ],
            EvasionStrategy.CAPTCHA_SOLVE: [
                "detect_captcha_type",
                "submit_to_solver",
                "wait_for_solution",
                "apply_solution",
                "continue",
            ],
            EvasionStrategy.CONTINUE: [],
        }
        return steps_map.get(strategy, [])

    def _parse_llm_response(self, response: str) -> dict[str, Any]:
        """
        Parse LLM response, handling potential JSON in markdown blocks.

        Args:
            response: Raw LLM response string.

        Returns:
            Parsed JSON dictionary.
        """
        # Try to extract JSON from markdown code blocks
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))

        # Try direct JSON parsing
        return json.loads(response)

    def _parse_detection_type(self, type_str: str) -> DetectionType:
        """Parse detection type from string."""
        try:
            return DetectionType(type_str.lower())
        except ValueError:
            return DetectionType.CUSTOM

    def _parse_risk_level(self, level_str: str) -> RiskLevel:
        """Parse risk level from string."""
        try:
            return RiskLevel(level_str.lower())
        except ValueError:
            return RiskLevel.MEDIUM

    def _parse_evasion_strategy(self, strategy_str: str) -> EvasionStrategy:
        """Parse evasion strategy from string."""
        try:
            return EvasionStrategy(strategy_str.lower())
        except ValueError:
            return EvasionStrategy.SLOW_DOWN

    def _track_anomaly(
        self,
        result: AnomalyResult,
        url: str,
        site: str,
    ) -> None:
        """
        Track anomaly in history.

        Args:
            result: Anomaly result to track.
            url: URL where anomaly was detected.
            site: Site identifier.
        """
        if not result.is_detected:
            return

        entry = AnomalyHistoryEntry(
            detection_type=result.detection_type,
            risk_level=result.risk_level,
            url=url,
            site=site,
        )
        self.history.append(entry)

        # Limit history size
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        logger.debug(
            "anomaly_tracked",
            detection_type=result.detection_type.value,
            risk_level=result.risk_level.value,
            site=site,
        )

    def verify_evasion(
        self,
        site: str,
        url: str,
        success: bool,
    ) -> None:
        """
        Verify and update evasion success status.

        Call this after attempting evasion to track success rate.

        Args:
            site: Site identifier.
            url: URL where evasion was attempted.
            success: Whether the evasion was successful.
        """
        # Find matching recent entry and update
        for entry in reversed(self.history):
            if entry.site == site and entry.url == url:
                entry.evasion_successful = success
                logger.info(
                    "evasion_verified",
                    site=site,
                    success=success,
                    detection_type=entry.detection_type.value,
                )
                break

    def get_detection_stats(self) -> dict[str, Any]:
        """
        Get detection statistics.

        Returns:
            Dictionary with detection statistics.
        """
        if not self.history:
            return {}

        by_type: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        by_site: dict[str, int] = {}
        successful_evasions = 0
        total_evasions = 0

        for entry in self.history:
            by_type[entry.detection_type.value] = (
                by_type.get(entry.detection_type.value, 0) + 1
            )
            by_risk[entry.risk_level.value] = (
                by_risk.get(entry.risk_level.value, 0) + 1
            )
            by_site[entry.site] = by_site.get(entry.site, 0) + 1

            if entry.evasion_successful is not None:
                total_evasions += 1
                if entry.evasion_successful:
                    successful_evasions += 1

        return {
            "total_detections": len(self.history),
            "by_type": by_type,
            "by_risk_level": by_risk,
            "by_site": by_site,
            "evasion_success_rate": (
                successful_evasions / total_evasions if total_evasions > 0 else None
            ),
        }

    def get_site_risk_profile(self, site: str) -> dict[str, Any]:
        """
        Get risk profile for a specific site.

        Args:
            site: Site identifier.

        Returns:
            Risk profile dictionary.
        """
        site_entries = [e for e in self.history if e.site == site]

        if not site_entries:
            return {"site": site, "risk_score": 0, "detection_count": 0}

        risk_scores = {
            RiskLevel.CRITICAL: 5,
            RiskLevel.HIGH: 4,
            RiskLevel.MEDIUM: 3,
            RiskLevel.LOW: 2,
            RiskLevel.NONE: 1,
        }

        total_score = sum(
            risk_scores.get(e.risk_level, 1) for e in site_entries
        )
        avg_score = total_score / len(site_entries)

        by_type = {}
        for entry in site_entries:
            by_type[entry.detection_type.value] = (
                by_type.get(entry.detection_type.value, 0) + 1
            )

        return {
            "site": site,
            "risk_score": round(avg_score, 2),
            "detection_count": len(site_entries),
            "by_detection_type": by_type,
            "most_common_detection": (
                max(by_type, key=by_type.get) if by_type else None
            ),
        }

    async def close(self) -> None:
        """Close resources (placeholder for future cleanup)."""
        logger.info(
            "anomaly_detector_closed",
            total_detections_tracked=len(self.history),
        )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Enums
    "DetectionType",
    "RiskLevel",
    "EvasionStrategy",
    "BehaviorCategory",
    # Data classes
    "AnomalyResult",
    "BrowserContext",
    "BehavioralData",
    "AnomalyHistoryEntry",
    # Utilities
    "DetectionPatterns",
    "BehavioralAnalyzer",
    # Main class
    "AnomalyDetector",
]
