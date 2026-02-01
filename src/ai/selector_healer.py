"""
VISE OS CSS Selector Healer.

AI-powered CSS/XPath selector healing for handling site structure changes
in visa booking automation. Automatically finds alternative selectors when
the original ones fail.

Features:
- LLM-based HTML analysis for selector recovery
- Multiple fallback selector generation
- Structure change detection
- Historical selector tracking for learning
- Confidence scoring for healing suggestions
- Site-specific selector recommendations

Usage:
    from src.ai.selector_healer import SelectorHealer, SelectorHealingResult

    healer = SelectorHealer(llm_client)

    result = await healer.heal(
        original_selector="#submit-btn",
        page_html="<button class='btn-submit'>Submit</button>",
        element_description="Submit button",
        url="https://vfs.com/book",
        previous_actions=["navigate", "fill_form"],
    )

    if result.success:
        new_selector = result.healed_selector
        fallbacks = result.fallback_selectors

    await healer.close()
"""

from __future__ import annotations

import json
import re
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


class SelectorType(str, Enum):
    """Types of CSS/XPath selectors."""

    CSS_ID = "css_id"
    CSS_CLASS = "css_class"
    CSS_ATTRIBUTE = "css_attribute"
    CSS_COMPLEX = "css_complex"
    XPATH_ABSOLUTE = "xpath_absolute"
    XPATH_RELATIVE = "xpath_relative"
    TEXT_BASED = "text_based"


class HealingStrategy(str, Enum):
    """Strategies for selector healing."""

    EXACT_MATCH = "exact_match"  # Find exact element with different selector
    SIMILAR_MATCH = "similar_match"  # Find similar element
    PARENT_CHILD = "parent_child"  # Navigate via parent/child
    SIBLING = "sibling"  # Navigate via sibling
    TEXT_CONTENT = "text_content"  # Match by text content
    ATTRIBUTE = "attribute"  # Match by other attributes


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SelectorHealingResult:
    """
    Result of a selector healing operation.

    Attributes:
        success: Whether healing was successful.
        healed_selector: The primary healed selector.
        fallback_selectors: Alternative selectors in order of preference.
        confidence: Confidence score (0.0 - 1.0).
        selector_type: Type of the healed selector.
        strategy_used: Healing strategy used.
        is_structure_change: Whether this indicates a site structure change.
        reasoning: Explanation of the healing decision.
        timestamp: When the healing was performed.

    Example:
        result = SelectorHealingResult(
            success=True,
            healed_selector="button.btn-submit",
            fallback_selectors=["button[type='submit']", "//button[@class='btn-submit']"],
            confidence=0.85,
            selector_type=SelectorType.CSS_CLASS,
            strategy_used=HealingStrategy.SIMILAR_MATCH,
            is_structure_change=False,
            reasoning="Found button with similar class name",
        )
    """

    success: bool
    healed_selector: str | None
    fallback_selectors: list[str]
    confidence: float
    selector_type: SelectorType | None
    strategy_used: HealingStrategy | None
    is_structure_change: bool
    reasoning: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "success": self.success,
            "healed_selector": self.healed_selector,
            "fallback_selectors": self.fallback_selectors,
            "confidence": self.confidence,
            "selector_type": self.selector_type.value if self.selector_type else None,
            "strategy_used": self.strategy_used.value if self.strategy_used else None,
            "is_structure_change": self.is_structure_change,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp,
        }


@dataclass
class SelectorHistoryEntry:
    """
    Historical entry for selector healing tracking.

    Attributes:
        original_selector: The original broken selector.
        healed_selector: The selector that was suggested.
        site: Site where healing occurred.
        element_type: Type of element (button, input, etc.).
        success: Whether the healing was verified successful.
        confidence: Confidence score at healing time.
        timestamp: When the healing occurred.
    """

    original_selector: str
    healed_selector: str
    site: str
    element_type: str
    success: bool
    confidence: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class SelectorContext:
    """
    Context for selector healing.

    Attributes:
        url: Current page URL.
        previous_actions: List of previous actions taken.
        failure_count: Number of consecutive failures.
        site: Site identifier (vfs, idata, bls, kkosmos).
        page_title: Current page title.
    """

    url: str
    previous_actions: list[str]
    failure_count: int = 0
    site: str = ""
    page_title: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary."""
        return {
            "url": self.url,
            "previous_actions": self.previous_actions[-10:],
            "failure_count": self.failure_count,
            "site": self.site,
            "page_title": self.page_title,
        }


# =============================================================================
# Selector Analyzer
# =============================================================================


class SelectorAnalyzer:
    """
    Utility class for analyzing CSS/XPath selectors.

    Provides methods to:
    - Determine selector type
    - Extract selector components
    - Validate selector syntax
    - Generate selector variants
    """

    # Common selector patterns
    ID_PATTERN = re.compile(r"^#[\w-]+$")
    CLASS_PATTERN = re.compile(r"^\.[\w-]+$")
    ATTRIBUTE_PATTERN = re.compile(r"\[[\w-]+[=~|^$*]?=['\"]?[^'\"]+['\"]?\]")
    XPATH_PATTERN = re.compile(r"^(/|\.\.?/)")

    @staticmethod
    def get_selector_type(selector: str) -> SelectorType:
        """
        Determine the type of a selector.

        Args:
            selector: CSS or XPath selector string.

        Returns:
            The determined selector type.
        """
        selector = selector.strip()

        if SelectorAnalyzer.XPATH_PATTERN.match(selector):
            if selector.startswith("//") or selector.startswith("./"):
                return SelectorType.XPATH_RELATIVE
            return SelectorType.XPATH_ABSOLUTE

        if selector.startswith("text=") or "text()" in selector:
            return SelectorType.TEXT_BASED

        if SelectorAnalyzer.ID_PATTERN.match(selector):
            return SelectorType.CSS_ID

        if SelectorAnalyzer.CLASS_PATTERN.match(selector):
            return SelectorType.CSS_CLASS

        if SelectorAnalyzer.ATTRIBUTE_PATTERN.search(selector):
            return SelectorType.CSS_ATTRIBUTE

        return SelectorType.CSS_COMPLEX

    @staticmethod
    def extract_element_info(selector: str) -> dict[str, Any]:
        """
        Extract information from a selector.

        Args:
            selector: CSS or XPath selector string.

        Returns:
            Dictionary with extracted information.
        """
        info: dict[str, Any] = {
            "tag": None,
            "id": None,
            "classes": [],
            "attributes": {},
        }

        selector = selector.strip()

        # Extract ID
        id_match = re.search(r"#([\w-]+)", selector)
        if id_match:
            info["id"] = id_match.group(1)

        # Extract classes
        class_matches = re.findall(r"\.([\w-]+)", selector)
        info["classes"] = class_matches

        # Extract tag
        tag_match = re.match(r"^(\w+)", selector)
        if tag_match and tag_match.group(1) not in {"text", "contains"}:
            info["tag"] = tag_match.group(1)

        # Extract attributes
        attr_matches = re.findall(r"\[([\w-]+)=['\"]?([^'\"]+)['\"]?\]", selector)
        info["attributes"] = dict(attr_matches)

        return info

    @staticmethod
    def generate_variants(selector: str) -> list[str]:
        """
        Generate selector variants for fallback.

        Args:
            selector: Original selector.

        Returns:
            List of variant selectors.
        """
        variants = []
        info = SelectorAnalyzer.extract_element_info(selector)

        # Variant: by ID only
        if info["id"]:
            variants.append(f"#{info['id']}")
            # With tag
            if info["tag"]:
                variants.append(f"{info['tag']}#{info['id']}")

        # Variant: by class
        for cls in info["classes"]:
            variants.append(f".{cls}")
            if info["tag"]:
                variants.append(f"{info['tag']}.{cls}")

        # Variant: by attributes
        for attr, value in info["attributes"].items():
            variants.append(f"[{attr}='{value}']")
            if info["tag"]:
                variants.append(f"{info['tag']}[{attr}='{value}']")

        # XPath variants
        if info["id"]:
            variants.append(f"//*[@id='{info['id']}']")
        for cls in info["classes"]:
            variants.append(f"//*[contains(@class, '{cls}')]")

        return list(set(variants))


# =============================================================================
# Selector Healer
# =============================================================================


class SelectorHealer:
    """
    AI-powered CSS/XPath selector healer.

    Automatically finds alternative selectors when original ones fail,
    using LLM analysis of page HTML and historical learning.

    Features:
    - LLM-based HTML analysis for smart healing
    - Multiple fallback selector generation
    - Structure change detection
    - Historical tracking for learning
    - Confidence scoring
    - Site-specific recommendations

    Attributes:
        llm: LLM client for AI analysis.
        history: Historical healing entries.
        max_history_per_selector: Maximum history entries per selector.
        max_total_history: Maximum total history entries.

    Example:
        healer = SelectorHealer(llm_client)

        result = await healer.heal(
            original_selector="#submit-btn",
            page_html="<button class='btn-submit'>Submit</button>",
            element_description="Submit button for form",
            url="https://vfs.com/book",
        )

        if result.success:
            print(f"Healed: {result.healed_selector}")
            print(f"Fallbacks: {result.fallback_selectors}")
    """

    # System prompt for selector healing
    SYSTEM_PROMPT = """You are an expert CSS/XPath selector specialist for web automation.

Your task is to analyze HTML and suggest alternative selectors when the original fails.

Guidelines:
1. Prefer stable selectors (data-testid, aria-label) over dynamic ones
2. Avoid selectors with auto-generated IDs or class names
3. Consider page context and element purpose
4. Generate multiple fallback options in order of reliability
5. Detect if this might be a site structure change

Always respond with valid JSON in the specified format."""

    def __init__(
        self,
        llm_client: LLMClient,
        max_history_per_selector: int = 20,
        max_total_history: int = 500,
    ) -> None:
        """
        Initialize selector healer.

        Args:
            llm_client: LLM client for AI analysis.
            max_history_per_selector: Maximum history per selector (default: 20).
            max_total_history: Maximum total history entries (default: 500).
        """
        self.llm = llm_client
        self.max_history_per_selector = max_history_per_selector
        self.max_total_history = max_total_history

        # Selector history: original -> list of healing entries
        self.history: dict[str, list[SelectorHistoryEntry]] = {}

        # Site-specific known selectors
        self.known_selectors: dict[str, dict[str, list[str]]] = {}

        logger.info("selector_healer_initialized")

    async def heal(
        self,
        original_selector: str,
        page_html: str,
        element_description: str,
        url: str,
        previous_actions: list[str] | None = None,
        site: str = "",
        failure_count: int = 0,
    ) -> SelectorHealingResult:
        """
        Heal a broken CSS/XPath selector.

        Args:
            original_selector: The selector that failed.
            page_html: HTML content of the page.
            element_description: Description of the expected element.
            url: Current page URL.
            previous_actions: List of previous actions (default: []).
            site: Site identifier (default: "").
            failure_count: Number of consecutive failures (default: 0).

        Returns:
            SelectorHealingResult with healing details.

        Example:
            result = await healer.heal(
                original_selector="#btn-submit",
                page_html="<button class='submit-button'>Submit</button>",
                element_description="Form submit button",
                url="https://vfs.com/form",
            )
        """
        previous_actions = previous_actions or []
        context = SelectorContext(
            url=url,
            previous_actions=previous_actions,
            failure_count=failure_count,
            site=site,
        )

        # Check historical recommendations first
        historical_selector = self._get_historical_recommendation(
            original_selector, site, element_description
        )
        if historical_selector:
            logger.info(
                "using_historical_selector",
                original=original_selector,
                healed=historical_selector,
            )
            return SelectorHealingResult(
                success=True,
                healed_selector=historical_selector,
                fallback_selectors=[],
                confidence=0.9,  # High confidence for verified historical
                selector_type=SelectorAnalyzer.get_selector_type(historical_selector),
                strategy_used=HealingStrategy.EXACT_MATCH,
                is_structure_change=False,
                reasoning="Using verified historical selector mapping",
            )

        # Generate rule-based variants first
        rule_based_variants = SelectorAnalyzer.generate_variants(original_selector)

        # Use LLM for intelligent healing
        try:
            result = await self._llm_heal(
                original_selector=original_selector,
                page_html=page_html,
                element_description=element_description,
                context=context,
                rule_based_variants=rule_based_variants,
            )

            # Track the healing attempt
            if result.success and result.healed_selector:
                self._track_healing(
                    original=original_selector,
                    healed=result.healed_selector,
                    site=site,
                    element_type=self._extract_element_type(element_description),
                    confidence=result.confidence,
                )

            return result

        except Exception as e:
            logger.error(
                "selector_healing_error",
                error=str(e),
                original=original_selector,
            )
            # Fall back to rule-based variants
            if rule_based_variants:
                return SelectorHealingResult(
                    success=True,
                    healed_selector=rule_based_variants[0],
                    fallback_selectors=rule_based_variants[1:5],
                    confidence=0.4,
                    selector_type=SelectorAnalyzer.get_selector_type(
                        rule_based_variants[0]
                    ),
                    strategy_used=HealingStrategy.SIMILAR_MATCH,
                    is_structure_change=False,
                    reasoning=f"LLM healing failed, using rule-based variants: {e}",
                )

            return SelectorHealingResult(
                success=False,
                healed_selector=None,
                fallback_selectors=[],
                confidence=0.0,
                selector_type=None,
                strategy_used=None,
                is_structure_change=True,
                reasoning=f"Selector healing failed: {e}",
            )

    async def _llm_heal(
        self,
        original_selector: str,
        page_html: str,
        element_description: str,
        context: SelectorContext,
        rule_based_variants: list[str],
    ) -> SelectorHealingResult:
        """
        Use LLM for intelligent selector healing.

        Args:
            original_selector: Original broken selector.
            page_html: Page HTML content.
            element_description: Element description.
            context: Selector context.
            rule_based_variants: Pre-generated rule-based variants.

        Returns:
            SelectorHealingResult from LLM analysis.
        """
        # Truncate HTML to fit token limits
        html_snippet = page_html[:3000] if len(page_html) > 3000 else page_html

        prompt = f"""A CSS/XPath selector is not finding the expected element.

Original selector: {original_selector}
Element description: {element_description}
Current URL: {context.url}
Previous actions: {context.previous_actions[-5:]}
Failure count: {context.failure_count}

Rule-based variants already generated:
{rule_based_variants[:5]}

Relevant HTML snippet:
```html
{html_snippet}
```

Please analyze the HTML and suggest:
1. The best alternative selector for this element
2. Multiple fallback selectors in order of preference
3. Whether this indicates a site structure change

Respond in JSON format:
{{
    "primary_selector": "...",
    "fallback_selectors": ["...", "...", "..."],
    "selector_type": "css_id/css_class/css_attribute/css_complex/xpath_relative/xpath_absolute/text_based",
    "strategy": "exact_match/similar_match/parent_child/sibling/text_content/attribute",
    "confidence": 0.0-1.0,
    "is_structure_change": true/false,
    "reasoning": "..."
}}"""

        response = await self.llm.complete(prompt, context={"site": context.site})

        try:
            result = self._parse_llm_response(response)

            logger.info(
                "llm_selector_healing_success",
                original=original_selector,
                healed=result.get("primary_selector"),
                confidence=result.get("confidence"),
            )

            return SelectorHealingResult(
                success=True,
                healed_selector=result.get("primary_selector"),
                fallback_selectors=result.get("fallback_selectors", []),
                confidence=result.get("confidence", 0.5),
                selector_type=self._parse_selector_type(result.get("selector_type")),
                strategy_used=self._parse_strategy(result.get("strategy")),
                is_structure_change=result.get("is_structure_change", False),
                reasoning=result.get("reasoning", ""),
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "llm_response_parse_error",
                error=str(e),
                original=original_selector,
            )
            return SelectorHealingResult(
                success=False,
                healed_selector=None,
                fallback_selectors=rule_based_variants[:5],
                confidence=0.3,
                selector_type=None,
                strategy_used=None,
                is_structure_change=True,
                reasoning=f"Failed to parse LLM response: {e}",
            )

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

    def _parse_selector_type(self, type_str: str | None) -> SelectorType | None:
        """Parse selector type from string."""
        if not type_str:
            return None
        try:
            return SelectorType(type_str)
        except ValueError:
            return SelectorType.CSS_COMPLEX

    def _parse_strategy(self, strategy_str: str | None) -> HealingStrategy | None:
        """Parse healing strategy from string."""
        if not strategy_str:
            return None
        try:
            return HealingStrategy(strategy_str)
        except ValueError:
            return HealingStrategy.SIMILAR_MATCH

    def _get_historical_recommendation(
        self,
        original_selector: str,
        site: str,
        element_description: str,
    ) -> str | None:
        """
        Get a historical recommendation if available.

        Args:
            original_selector: Original broken selector.
            site: Site identifier.
            element_description: Element description.

        Returns:
            Historical healed selector or None.
        """
        key = f"{site}:{original_selector}"
        if key not in self.history:
            return None

        # Find successful healings with high confidence
        successful = [
            entry
            for entry in self.history[key]
            if entry.success and entry.confidence >= 0.8
        ]

        if not successful:
            return None

        # Return most recent successful healing
        return successful[-1].healed_selector

    def _track_healing(
        self,
        original: str,
        healed: str,
        site: str,
        element_type: str,
        confidence: float,
        success: bool = True,
    ) -> None:
        """
        Track a healing attempt in history.

        Args:
            original: Original selector.
            healed: Healed selector.
            site: Site identifier.
            element_type: Type of element.
            confidence: Confidence score.
            success: Whether healing was successful.
        """
        key = f"{site}:{original}"

        if key not in self.history:
            self.history[key] = []

        entry = SelectorHistoryEntry(
            original_selector=original,
            healed_selector=healed,
            site=site,
            element_type=element_type,
            success=success,
            confidence=confidence,
        )

        self.history[key].append(entry)

        # Limit history per selector
        if len(self.history[key]) > self.max_history_per_selector:
            self.history[key] = self.history[key][-self.max_history_per_selector :]

        # Limit total history
        total_entries = sum(len(entries) for entries in self.history.values())
        if total_entries > self.max_total_history:
            self._prune_history()

        logger.debug(
            "healing_tracked",
            original=original,
            healed=healed,
            site=site,
            confidence=confidence,
        )

    def _prune_history(self) -> None:
        """Prune oldest entries from history."""
        all_entries: list[tuple[str, SelectorHistoryEntry]] = []

        for key, entries in self.history.items():
            for entry in entries:
                all_entries.append((key, entry))

        # Sort by timestamp and keep newest
        all_entries.sort(key=lambda x: x[1].timestamp)
        entries_to_remove = len(all_entries) - self.max_total_history

        if entries_to_remove <= 0:
            return

        # Remove oldest entries
        for key, entry in all_entries[:entries_to_remove]:
            if key in self.history:
                try:
                    self.history[key].remove(entry)
                except ValueError:
                    pass
                if not self.history[key]:
                    del self.history[key]

    def _extract_element_type(self, description: str) -> str:
        """
        Extract element type from description.

        Args:
            description: Element description.

        Returns:
            Extracted element type.
        """
        description_lower = description.lower()

        if "button" in description_lower:
            return "button"
        if "input" in description_lower or "field" in description_lower:
            return "input"
        if "link" in description_lower or "anchor" in description_lower:
            return "link"
        if "dropdown" in description_lower or "select" in description_lower:
            return "select"
        if "checkbox" in description_lower:
            return "checkbox"
        if "radio" in description_lower:
            return "radio"
        if "form" in description_lower:
            return "form"
        if "table" in description_lower:
            return "table"

        return "element"

    def verify_healing(
        self,
        original_selector: str,
        healed_selector: str,
        site: str,
        success: bool,
    ) -> None:
        """
        Verify and update healing success status.

        Call this after testing whether the healed selector worked.

        Args:
            original_selector: Original selector.
            healed_selector: Healed selector that was tried.
            site: Site identifier.
            success: Whether the healed selector worked.
        """
        key = f"{site}:{original_selector}"

        if key not in self.history:
            return

        # Update the most recent matching entry
        for entry in reversed(self.history[key]):
            if entry.healed_selector == healed_selector:
                entry.success = success
                logger.info(
                    "healing_verified",
                    original=original_selector,
                    healed=healed_selector,
                    success=success,
                )
                break

    def get_recommendations(
        self,
        site: str,
        element_type: str,
    ) -> list[str]:
        """
        Get recommended selectors for a site and element type.

        Args:
            site: Site identifier.
            element_type: Type of element.

        Returns:
            List of recommended selectors.
        """
        recommendations: list[str] = []

        for key, entries in self.history.items():
            if not key.startswith(f"{site}:"):
                continue

            for entry in entries:
                if (
                    entry.element_type == element_type
                    and entry.success
                    and entry.confidence >= 0.7
                ):
                    recommendations.append(entry.healed_selector)

        # Return unique recommendations
        return list(set(recommendations))[:10]

    def get_healing_stats(self) -> dict[str, Any]:
        """
        Get statistics about selector healing.

        Returns:
            Dictionary with healing statistics.
        """
        total_attempts = 0
        successful = 0
        confidence_sum = 0.0
        by_site: dict[str, int] = {}
        by_element_type: dict[str, int] = {}

        for key, entries in self.history.items():
            site = key.split(":")[0] if ":" in key else "unknown"

            for entry in entries:
                total_attempts += 1
                if entry.success:
                    successful += 1
                confidence_sum += entry.confidence

                by_site[site] = by_site.get(site, 0) + 1
                by_element_type[entry.element_type] = (
                    by_element_type.get(entry.element_type, 0) + 1
                )

        return {
            "total_attempts": total_attempts,
            "successful": successful,
            "success_rate": successful / total_attempts if total_attempts > 0 else 0,
            "average_confidence": (
                confidence_sum / total_attempts if total_attempts > 0 else 0
            ),
            "by_site": by_site,
            "by_element_type": by_element_type,
        }

    def register_known_selector(
        self,
        site: str,
        element_key: str,
        selectors: list[str],
    ) -> None:
        """
        Register known selectors for a site and element.

        Args:
            site: Site identifier.
            element_key: Element key (e.g., "login_button").
            selectors: List of known working selectors.
        """
        if site not in self.known_selectors:
            self.known_selectors[site] = {}

        self.known_selectors[site][element_key] = selectors

        logger.debug(
            "known_selectors_registered",
            site=site,
            element_key=element_key,
            count=len(selectors),
        )

    def get_known_selectors(
        self,
        site: str,
        element_key: str,
    ) -> list[str]:
        """
        Get known selectors for a site and element.

        Args:
            site: Site identifier.
            element_key: Element key.

        Returns:
            List of known selectors or empty list.
        """
        if site not in self.known_selectors:
            return []

        return self.known_selectors[site].get(element_key, [])

    async def close(self) -> None:
        """Close resources (placeholder for future cleanup)."""
        logger.info(
            "selector_healer_closed",
            total_history_entries=sum(
                len(entries) for entries in self.history.values()
            ),
        )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Enums
    "SelectorType",
    "HealingStrategy",
    # Data classes
    "SelectorHealingResult",
    "SelectorHistoryEntry",
    "SelectorContext",
    # Utilities
    "SelectorAnalyzer",
    # Main class
    "SelectorHealer",
]
