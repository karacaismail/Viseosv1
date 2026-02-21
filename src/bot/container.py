"""
VISE OS Bot Service Container.

Dependency injection container that wires all bot services together.
Used by both FastAPI (API layer) and Celery workers (queue layer).

Usage:
    from src.bot.container import BotServiceContainer

    container = BotServiceContainer.get_instance()
    await container.initialize()

    # Get a configured adapter
    adapter = container.get_adapter("vfs")

    # Shutdown
    await container.shutdown()
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from src.api.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class BotServiceContainer:
    """
    Singleton container wiring all bot services in dependency order.

    Services:
        proxy_manager: ProxyPoolManager for multi-provider proxy rotation
        captcha_solver: CaptchaSolverChain with CapSolver/2Captcha failover
        account_manager: AccountPoolManager with health scoring
        stealth_engine: StealthEngine with multi-tier browser fallback
        session_manager: SessionManager wrapping stealth engine sessions
    """

    _instance: BotServiceContainer | None = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._initialized = False

        # Services (lazy-initialized in initialize())
        self.proxy_manager: Any = None
        self.captcha_solver: Any = None
        self.account_manager: Any = None
        self.stealth_engine: Any = None
        self.session_manager: Any = None

    @classmethod
    def get_instance(cls, settings: Settings | None = None) -> BotServiceContainer:
        """Get or create the singleton container instance."""
        if cls._instance is None:
            if settings is None:
                settings = get_settings()
            cls._instance = cls(settings)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def initialize(self) -> None:
        """
        Initialize all services in dependency order.

        Order:
        1. ProxyPoolManager (no deps)
        2. CaptchaSolverChain (no deps)
        3. AccountPoolManager (needs proxy_manager for pairing)
        4. StealthEngine (standalone)
        5. SessionManager (wraps stealth engine, uses tier fallback)
        """
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            logger.info("bot_container_initializing")

            # 1. Proxy Manager
            from src.bot.services.proxy import ProxyPoolManager

            self.proxy_manager = ProxyPoolManager()
            proxy_configs = self._build_proxy_configs()
            if proxy_configs:
                await self.proxy_manager.initialize(proxy_configs)

            # 2. CAPTCHA Solver Chain
            from src.bot.services.captcha import CaptchaSolverChain

            self.captcha_solver = CaptchaSolverChain()
            captcha_config = self._build_captcha_config()
            if captcha_config:
                await self.captcha_solver.initialize(captcha_config)

            # 3. Account Pool Manager
            from src.bot.services.account import AccountPoolManager

            self.account_manager = AccountPoolManager()
            # Accounts are loaded from database when needed via load_from_database()

            # 4. Stealth Engine
            from src.bot.engine.stealth import StealthEngine

            self.stealth_engine = StealthEngine(
                max_sessions=self.settings.MAX_BROWSER_SESSIONS
                if hasattr(self.settings, "MAX_BROWSER_SESSIONS")
                else 20,
                enable_pool=False,  # SessionManager handles pooling
            )
            await self.stealth_engine.initialize()

            # 5. Session Manager (uses stealth engine for tier-aware creation)
            from src.bot.engine.session import SessionManager

            self.session_manager = SessionManager(
                stealth_engine=self.stealth_engine,
            )
            await self.session_manager.initialize()

            self._initialized = True
            logger.info(
                "bot_container_initialized",
                proxy_providers=len(proxy_configs),
                captcha_providers=len(
                    [k for k, v in captcha_config.items() if v]
                )
                if captcha_config
                else 0,
            )

    def get_adapter(self, site_code: str, **kwargs: Any) -> Any:
        """
        Create a configured site adapter.

        Args:
            site_code: Site identifier (vfs, idata, bls, kkosmos).
            **kwargs: Additional adapter-specific config overrides.

        Returns:
            Configured adapter instance.

        Raises:
            RuntimeError: If container not initialized.
            ValueError: If site_code unknown.
        """
        if not self._initialized:
            raise RuntimeError("BotServiceContainer not initialized. Call initialize() first.")

        from src.bot.adapters import get_adapter_class

        adapter_cls = get_adapter_class(site_code)
        return adapter_cls(
            stealth_engine=self.stealth_engine,
            proxy_manager=self.proxy_manager,
            captcha_solver=self.captcha_solver,
            **kwargs,
        )

    async def shutdown(self) -> None:
        """Gracefully shut down all services."""
        logger.info("bot_container_shutting_down")

        if self.session_manager:
            try:
                await self.session_manager.shutdown()
            except Exception as e:
                logger.warning("session_manager_shutdown_error", error=str(e))

        if self.stealth_engine:
            try:
                await self.stealth_engine.close()
            except Exception as e:
                logger.warning("stealth_engine_shutdown_error", error=str(e))

        self._initialized = False
        logger.info("bot_container_shut_down")

    def _build_proxy_configs(self) -> dict[str, Any]:
        """Build proxy provider configs from application settings."""
        configs: dict[str, Any] = {}

        if self.settings.BRIGHTDATA_USERNAME:
            configs["brightdata"] = {
                "username": self.settings.BRIGHTDATA_USERNAME,
                "password": self.settings.BRIGHTDATA_PASSWORD.get_secret_value(),
                "countries": ["tr"],
                "pool_size": 10,
            }

        if self.settings.OXYLABS_USERNAME:
            configs["oxylabs"] = {
                "username": self.settings.OXYLABS_USERNAME,
                "password": self.settings.OXYLABS_PASSWORD.get_secret_value(),
                "countries": ["tr"],
                "pool_size": 5,
            }

        return configs

    def _build_captcha_config(self) -> dict[str, str]:
        """Build CAPTCHA provider config from application settings."""
        config: dict[str, str] = {}

        capsolver_key = self.settings.CAPSOLVER_API_KEY.get_secret_value()
        if capsolver_key:
            config["capsolver_api_key"] = capsolver_key

        twocaptcha_key = self.settings.TWOCAPTCHA_API_KEY.get_secret_value()
        if twocaptcha_key:
            config["2captcha_api_key"] = twocaptcha_key

        return config
