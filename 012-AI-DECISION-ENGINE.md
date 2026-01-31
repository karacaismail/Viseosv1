# 012 - AI Decision Engine Specification

## Amaç

LLM destekli karar mekanizması ile booking flow'larında dinamik strateji değişikliği, selector healing, anomaly detection ve adaptive behavior. Claude/GPT-4 entegrasyonu ile human-like decision making.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 004-STEALTH-ENGINE | Browser context, DOM access |
| 005-PROXY-MANAGER | Strategy-based proxy selection |
| 006-CAPTCHA-SOLVER | CAPTCHA strategy decisions |
| 007-ACCOUNT-POOL-MANAGER | Account rotation decisions |
| 013-STATE-MACHINE | State transition guidance |
| 008-011 | Site adapters - strategy injection |

---

## AI Engine Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AI DECISION ENGINE                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────┐    ┌────────────────┐                   │
│  │  Context       │    │   Decision     │                   │
│  │  Collector     │───►│   Analyzer     │                   │
│  └────────────────┘    └───────┬────────┘                   │
│         ▲                      │                             │
│         │                      ▼                             │
│  ┌──────┴───────┐     ┌────────────────┐                    │
│  │  Browser     │     │   LLM Provider │                    │
│  │  State       │     │  (Claude/GPT)  │                    │
│  └──────────────┘     └───────┬────────┘                    │
│                               │                              │
│                               ▼                              │
│  ┌────────────────────────────────────────┐                 │
│  │           Decision Outputs              │                 │
│  ├────────────────────────────────────────┤                 │
│  │  • Strategy Selection                   │                 │
│  │  • Selector Healing                     │                 │
│  │  • Error Recovery                       │                 │
│  │  • Anomaly Response                     │                 │
│  │  • Human Escalation                     │                 │
│  └────────────────────────────────────────┘                 │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## LLM Provider Configuration

```python
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import httpx
import json

class LLMProvider(Enum):
    CLAUDE = "claude"
    GPT4 = "gpt4"
    LOCAL = "local"  # Ollama/local models

@dataclass
class LLMConfig:
    """LLM provider configuration"""
    provider: LLMProvider
    api_key: str
    model: str
    max_tokens: int = 2000
    temperature: float = 0.3  # Lower for more deterministic decisions
    timeout: int = 30

class LLMClient(ABC):
    """Abstract LLM client"""
    
    @abstractmethod
    async def complete(self, prompt: str, context: Dict = None) -> str:
        pass

class ClaudeClient(LLMClient):
    """Anthropic Claude client"""
    
    BASE_URL = "https://api.anthropic.com/v1/messages"
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            headers={
                "x-api-key": config.api_key,
                "anthropic-version": "2024-01-01",
                "content-type": "application/json",
            },
            timeout=config.timeout,
        )
    
    async def complete(self, prompt: str, context: Dict = None) -> str:
        """Claude completion"""
        
        system_prompt = self._build_system_prompt(context)
        
        response = await self.client.post(
            self.BASE_URL,
            json={
                "model": self.config.model,  # claude-3-5-sonnet-20241022
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        
        raise LLMError(f"Claude API error: {response.status_code}")
    
    def _build_system_prompt(self, context: Dict = None) -> str:
        """System prompt oluştur"""
        
        return """You are an AI decision engine for a visa appointment booking automation system.

Your role is to analyze browser states, error conditions, and booking flows to make optimal decisions.

Key responsibilities:
1. Selector healing: When elements can't be found, suggest alternative selectors
2. Strategy switching: Recommend when to change approach (aggressive/conservative)
3. Error recovery: Classify errors and suggest recovery actions
4. Anomaly detection: Identify unusual patterns that might indicate detection
5. Human escalation: Know when to escalate to human operators

Always respond with structured JSON containing your decision and reasoning.
Format: {"decision": "...", "action": "...", "confidence": 0.0-1.0, "reasoning": "..."}

Be conservative with confidence scores. Below 0.7 means human review recommended."""


class GPT4Client(LLMClient):
    """OpenAI GPT-4 client"""
    
    BASE_URL = "https://api.openai.com/v1/chat/completions"
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout,
        )
    
    async def complete(self, prompt: str, context: Dict = None) -> str:
        """GPT-4 completion"""
        
        response = await self.client.post(
            self.BASE_URL,
            json={
                "model": self.config.model,  # gpt-4-turbo-preview
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "messages": [
                    {"role": "system", "content": self._build_system_prompt(context)},
                    {"role": "user", "content": prompt},
                ],
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        
        raise LLMError(f"GPT-4 API error: {response.status_code}")
    
    def _build_system_prompt(self, context: Dict = None) -> str:
        return ClaudeClient._build_system_prompt(None, context)


class LLMClientFactory:
    """LLM client factory"""
    
    @staticmethod
    def create(config: LLMConfig) -> LLMClient:
        if config.provider == LLMProvider.CLAUDE:
            return ClaudeClient(config)
        elif config.provider == LLMProvider.GPT4:
            return GPT4Client(config)
        else:
            raise ValueError(f"Unknown provider: {config.provider}")
```

---

## Decision Types

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any

class DecisionType(Enum):
    """AI decision tipleri"""
    STRATEGY_SWITCH = "strategy_switch"
    SELECTOR_HEAL = "selector_heal"
    ERROR_RECOVERY = "error_recovery"
    ANOMALY_RESPONSE = "anomaly_response"
    HUMAN_ESCALATION = "human_escalation"
    CONTINUE = "continue"
    ABORT = "abort"
    RETRY = "retry"
    WAIT = "wait"

class StrategyType(Enum):
    """Booking stratejileri"""
    AGGRESSIVE = "aggressive"      # Hızlı, risk tolerant
    CONSERVATIVE = "conservative"  # Yavaş, güvenli
    STEALTH = "stealth"           # Maximum evasion
    RECOVERY = "recovery"         # Ban recovery mode

@dataclass
class AIDecision:
    """AI karar çıktısı"""
    decision_type: DecisionType
    action: str
    confidence: float  # 0.0 - 1.0
    reasoning: str
    
    # Optional details
    new_selector: Optional[str] = None
    new_strategy: Optional[StrategyType] = None
    wait_seconds: Optional[int] = None
    recovery_steps: Optional[List[str]] = None
    escalation_reason: Optional[str] = None

@dataclass
class BrowserContext:
    """Browser durumu context'i"""
    current_url: str
    page_title: str
    visible_text: str
    error_messages: List[str]
    current_selector: str
    selector_found: bool
    page_html_snippet: str  # Relevant section only
    screenshot_description: str  # Vision model output
    
    # History
    previous_actions: List[str]
    failure_count: int
    time_elapsed_seconds: int

@dataclass
class FlowContext:
    """Booking flow context'i"""
    site: str  # vfs, idata, bls, kkosmos
    current_state: str
    target_action: str
    applicant_data_filled: Dict[str, bool]
    slot_selected: bool
    payment_started: bool
    
    # Metrics
    total_attempts: int
    success_rate_today: float
    average_duration_seconds: int
```

---

## Selector Healing Engine

```python
class SelectorHealingEngine:
    """Bozulan selector'ları AI ile düzelt"""
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        
        # Selector history (learning için)
        self.selector_history: Dict[str, List[Dict]] = {}
    
    async def heal_selector(
        self,
        original_selector: str,
        page_html: str,
        element_description: str,
        context: BrowserContext,
    ) -> AIDecision:
        """Çalışmayan selector için alternatif bul"""
        
        prompt = f"""A CSS/XPath selector is not finding the expected element.

Original selector: {original_selector}
Element description: {element_description}
Current URL: {context.current_url}

Relevant HTML snippet:
```html
{page_html[:3000]}  # Truncate for token limit
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
        
        response = await self.llm.complete(prompt, {"context": context.__dict__})
        
        try:
            result = json.loads(response)
            
            # Track for learning
            self._track_selector_attempt(original_selector, result)
            
            return AIDecision(
                decision_type=DecisionType.SELECTOR_HEAL,
                action="try_alternative_selector",
                confidence=result.get("confidence", 0.5),
                reasoning=result.get("reasoning", ""),
                new_selector=result.get("primary_selector"),
            )
            
        except json.JSONDecodeError:
            return AIDecision(
                decision_type=DecisionType.HUMAN_ESCALATION,
                action="manual_selector_update",
                confidence=0.0,
                reasoning="Failed to parse AI response",
                escalation_reason="Selector healing failed, manual update required",
            )
    
    def _track_selector_attempt(self, original: str, result: Dict):
        """Selector attempt'i kaydet (learning için)"""
        
        if original not in self.selector_history:
            self.selector_history[original] = []
        
        self.selector_history[original].append({
            "timestamp": datetime.utcnow().isoformat(),
            "suggested": result.get("primary_selector"),
            "confidence": result.get("confidence"),
        })
    
    async def get_selector_recommendations(
        self,
        site: str,
        element_type: str,
    ) -> List[str]:
        """Site ve element tipi için önerilen selector'lar"""
        
        # Use historical data for common patterns
        history_key = f"{site}_{element_type}"
        
        if history_key in self.selector_history:
            # Return most successful selectors
            successful = [
                h["suggested"] for h in self.selector_history[history_key]
                if h.get("confidence", 0) > 0.7
            ]
            return list(set(successful))[:5]
        
        return []
```

---

## Strategy Switching Engine

```python
class StrategySwitchingEngine:
    """Dinamik strateji değişikliği"""
    
    # Strategy profiles
    STRATEGIES = {
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
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.current_strategy = StrategyType.CONSERVATIVE
    
    async def evaluate_strategy(
        self,
        context: FlowContext,
        browser_context: BrowserContext,
        recent_errors: List[str],
    ) -> AIDecision:
        """Mevcut duruma göre strateji değerlendirmesi"""
        
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
        
        response = await self.llm.complete(prompt)
        
        try:
            result = json.loads(response)
            
            if result.get("should_switch", False):
                new_strategy = StrategyType(result["recommended_strategy"])
                
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
            
        except (json.JSONDecodeError, ValueError):
            return AIDecision(
                decision_type=DecisionType.CONTINUE,
                action="maintain_current_strategy",
                confidence=0.5,
                reasoning="Failed to parse AI response, continuing with current strategy",
            )
    
    def get_strategy_config(self, strategy: StrategyType = None) -> Dict:
        """Strateji configuration al"""
        strategy = strategy or self.current_strategy
        return self.STRATEGIES[strategy].copy()
    
    def apply_strategy(self, strategy: StrategyType):
        """Strateji uygula"""
        self.current_strategy = strategy
        return self.STRATEGIES[strategy]
```

---

## Anomaly Detection Engine

```python
class AnomalyDetectionEngine:
    """Bot detection ve anomaly tespit"""
    
    # Detection indicators
    DETECTION_INDICATORS = {
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
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        
        # Anomaly tracking
        self.anomaly_history: List[Dict] = []
    
    async def analyze_for_detection(
        self,
        browser_context: BrowserContext,
        network_timing: Dict[str, float],
        response_headers: Dict[str, str],
    ) -> AIDecision:
        """Detection olasılığını analiz et"""
        
        # Rule-based pre-check
        risk_level = self._quick_risk_assessment(browser_context)
        
        if risk_level == "high":
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
        
        response = await self.llm.complete(prompt)
        
        try:
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
        """Hızlı rule-based risk değerlendirmesi"""
        
        text_lower = (context.visible_text + " ".join(context.error_messages)).lower()
        
        for indicator in self.DETECTION_INDICATORS["high_risk"]:
            if indicator in text_lower:
                return "high"
        
        for indicator in self.DETECTION_INDICATORS["medium_risk"]:
            if indicator in text_lower:
                return "medium"
        
        return "low"
    
    def _track_anomaly(self, context: BrowserContext, analysis: Dict):
        """Anomaly'yi kaydet"""
        
        self.anomaly_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "url": context.current_url,
            "detection_type": analysis.get("detection_type"),
            "risk_level": analysis.get("risk_level"),
        })
        
        # Keep last 100 only
        if len(self.anomaly_history) > 100:
            self.anomaly_history = self.anomaly_history[-100:]
```

---

## Error Recovery Engine

```python
class ErrorRecoveryEngine:
    """Hata recovery stratejileri"""
    
    # Error classification
    ERROR_CATEGORIES = {
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
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
    
    async def classify_and_recover(
        self,
        error_message: str,
        context: FlowContext,
        browser_context: BrowserContext,
    ) -> AIDecision:
        """Hatayı sınıflandır ve recovery stratejisi belirle"""
        
        # Quick classification
        category = self._classify_error(error_message)
        
        if category:
            config = self.ERROR_CATEGORIES[category]
            
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
    
    def _classify_error(self, error_message: str) -> Optional[str]:
        """Rule-based error classification"""
        
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
        """AI-based error recovery for complex cases"""
        
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
        
        response = await self.llm.complete(prompt)
        
        try:
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
            return AIDecision(
                decision_type=DecisionType.RETRY,
                action="generic_retry",
                confidence=0.4,
                reasoning="Unable to parse AI response, attempting generic retry",
            )
```

---

## Main Decision Engine

```python
class AIDecisionEngine:
    """Ana AI karar motoru"""
    
    def __init__(self, llm_config: LLMConfig):
        self.llm = LLMClientFactory.create(llm_config)
        
        # Sub-engines
        self.selector_healer = SelectorHealingEngine(self.llm)
        self.strategy_switcher = StrategySwitchingEngine(self.llm)
        self.anomaly_detector = AnomalyDetectionEngine(self.llm)
        self.error_recovery = ErrorRecoveryEngine(self.llm)
        
        # Decision history
        self.decision_history: List[AIDecision] = []
    
    async def make_decision(
        self,
        decision_request: str,
        flow_context: FlowContext,
        browser_context: BrowserContext,
        additional_data: Dict = None,
    ) -> AIDecision:
        """Karar al"""
        
        # Route to appropriate engine
        if "selector" in decision_request.lower():
            decision = await self.selector_healer.heal_selector(
                original_selector=additional_data.get("selector", ""),
                page_html=additional_data.get("html", ""),
                element_description=additional_data.get("description", ""),
                context=browser_context,
            )
        
        elif "strategy" in decision_request.lower():
            decision = await self.strategy_switcher.evaluate_strategy(
                context=flow_context,
                browser_context=browser_context,
                recent_errors=additional_data.get("errors", []),
            )
        
        elif "anomaly" in decision_request.lower() or "detection" in decision_request.lower():
            decision = await self.anomaly_detector.analyze_for_detection(
                browser_context=browser_context,
                network_timing=additional_data.get("network_timing", {}),
                response_headers=additional_data.get("headers", {}),
            )
        
        elif "error" in decision_request.lower():
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
        if decision.confidence < 0.5:
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
        """Genel karar"""
        
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
        
        response = await self.llm.complete(prompt)
        
        try:
            result = json.loads(response)
            
            decision_map = {
                "continue": DecisionType.CONTINUE,
                "retry": DecisionType.RETRY,
                "wait": DecisionType.WAIT,
                "abort": DecisionType.ABORT,
                "escalate": DecisionType.HUMAN_ESCALATION,
            }
            
            return AIDecision(
                decision_type=decision_map.get(result.get("decision", "continue"), DecisionType.CONTINUE),
                action=result.get("action", "continue_flow"),
                confidence=result.get("confidence", 0.5),
                reasoning=result.get("reasoning", ""),
            )
            
        except json.JSONDecodeError:
            return AIDecision(
                decision_type=DecisionType.CONTINUE,
                action="default_continue",
                confidence=0.3,
                reasoning="Failed to parse AI response",
            )
    
    def _track_decision(self, decision: AIDecision):
        """Kararı kaydet"""
        
        self.decision_history.append(decision)
        
        # Keep last 500
        if len(self.decision_history) > 500:
            self.decision_history = self.decision_history[-500:]
    
    def get_decision_stats(self) -> Dict:
        """Karar istatistikleri"""
        
        if not self.decision_history:
            return {}
        
        type_counts = {}
        confidence_sum = 0
        
        for d in self.decision_history:
            type_counts[d.decision_type.value] = type_counts.get(d.decision_type.value, 0) + 1
            confidence_sum += d.confidence
        
        return {
            "total_decisions": len(self.decision_history),
            "by_type": type_counts,
            "average_confidence": confidence_sum / len(self.decision_history),
        }
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Selector healing %80+ başarı | Broken selector test |
| AC-002 | Strategy switching doğru trigger olmalı | Scenario test |
| AC-003 | Anomaly detection %90+ precision | Detection test |
| AC-004 | Error recovery appropriate action | Error injection test |
| AC-005 | LLM failover çalışmalı (Claude → GPT-4) | Provider failure test |
| AC-006 | Confidence threshold human escalation trigger | Threshold test |
| AC-007 | Decision latency <5s average | Performance test |

---

## Güvenlik ve Maliyet

**API Maliyet Optimizasyonu:**
- Rule-based pre-filtering (LLM call azaltma)
- Response caching (similar scenarios)
- Shorter prompts for simple decisions
- Batch decisions when possible

**Rate Limits:**
- Claude: 4000 RPM, 400K tokens/min
- GPT-4: 10000 RPM, 300K tokens/min
- Fallback queue for rate limit overflow

---

## Sonraki Adımlar

1. **013-STATE-MACHINE.md** - Booking state machine
2. LLM provider hesap ve key setup
3. Integration test ile confidence tuning
