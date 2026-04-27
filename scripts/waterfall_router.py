"""Waterfall router with LiteLLM integration.

This module provides a router that delegates rate limit management to litellm.Router.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import litellm

from litellm_router import create_router

logger = logging.getLogger("pipeline.router")


class WaterfallRouter:
    """Waterfall router using LiteLLM Router for rate limit management.

    This class wraps litellm.Router and provides the same interface as the
    previous TokenTracker for backward compatibility.
    """

    def __init__(self, limits_file: Path | str = "api_limits.json"):
        self.limits_file = Path(limits_file)
        self.router: litellm.Router | None = None
        self._initialize_router()

    def _initialize_router(self):
        """Initialize LiteLLM Router from api_limits.json."""
        try:
            self.router = create_router(str(self.limits_file))
            logger.info("LiteLLM Router initialized from %s", self.limits_file)
        except Exception as e:
            logger.error("Failed to initialize LiteLLM Router: %s", e)
            raise

    def completion(self, messages: list[dict[str, Any]], **kwargs) -> Any:
        """Synchronous completion via LiteLLM Router.

        Args:
            messages: Chat messages
            **kwargs: Additional parameters passed to litellm

        Returns:
            LiteLLM response
        """
        if self.router is None:
            raise RuntimeError("Router not initialized")
        return self.router.completion(messages=messages, **kwargs)

    async def acompletion(self, messages: list[dict[str, Any]], **kwargs) -> Any:
        """Async completion via LiteLLM Router.

        Args:
            messages: Chat messages
            **kwargs: Additional parameters passed to litellm

        Returns:
            LiteLLM response
        """
        if self.router is None:
            raise RuntimeError("Router not initialized")
        return await self.router.acompletion(messages=messages, **kwargs)

    def get_available_model(self) -> str | None:
        """Get the currently available model from the router.

        Returns:
            Model name or None if no model is available
        """
        if self.router is None:
            return None
        # LiteLLM Router handles model selection internally
        return self.router.get_available_model()

    def get_model_list(self) -> list[dict]:
        """Get the list of configured models.

        Returns:
            List of model configurations
        """
        if self.router is None:
            return []
        return self.router.model_list


# Backward compatibility: keep TokenTracker as a thin wrapper
# for code that still references it directly
class TokenTracker:
    """Backward compatibility wrapper.

    This class is deprecated. Use WaterfallRouter instead.
    Rate limit management is now delegated to litellm.Router.
    """

    def __init__(self, limits_file: Path):
        logger.warning(
            "TokenTracker is deprecated. Use WaterfallRouter instead. "
            "Rate limit management is now delegated to litellm.Router."
        )
        self.limits_file = Path(limits_file)
        self._limits = self._load_limits()
        self._router = WaterfallRouter(limits_file)

    def _load_limits(self) -> dict:
        """Load limits from api_limits.json."""
        if not self.limits_file.exists():
            logger.warning("api_limits.json not found at %s. Using default high limits.", self.limits_file)
            return {}
        with open(self.limits_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _get_limit(self, provider: str, model: str, key: str, default: int) -> int:
        """Get limit value for a provider/model/key combination.

        Args:
            provider: Provider name (e.g., 'gemini', 'groq')
            model: Model name (e.g., 'gemini-2.5-flash-lite')
            key: Limit key ('rpd', 'rpm', 'tpm')
            default: Default value if not found

        Returns:
            Limit value or default
        """
        prov_limits = self._limits.get(provider, {})
        model_limits = prov_limits.get(model, {})
        if key in model_limits:
            return model_limits[key]
        auto_limits = prov_limits.get("auto", {})
        if isinstance(auto_limits, dict) and key in auto_limits:
            return auto_limits[key]
        default_limits = prov_limits.get("default", {})
        if isinstance(default_limits, dict) and key in default_limits:
            return default_limits[key]
        if key in prov_limits and isinstance(prov_limits[key], int):
            return prov_limits[key]
        return default

    def get_current_usage(self, provider: str, model: str, now: float) -> tuple[int, int, int]:
        """Get current usage for a provider/model.

        Since LiteLLM manages rate limits internally, this returns dummy values
        that indicate the limits are not exhausted.

        Args:
            provider: Provider name (ignored)
            model: Model name (ignored)
            now: Current time (ignored)

        Returns:
            Tuple of (rpd, rpm, tpm) - all zeros indicating available capacity
        """
        # LiteLLM manages usage internally, so we report as available
        return 0, 0, 0

    def estimate_tokens(self, text: str, max_output_tokens: int | None = None) -> int:
        """Estimate token count (approximate).

        Args:
            text: Input text
            max_output_tokens: Expected output tokens

        Returns:
            Estimated token count
        """
        # Approximate: 1 token ≈ 4 characters
        input_tokens = len(text) // 4
        output_tokens = max_output_tokens if max_output_tokens is not None else 0
        return input_tokens + output_tokens

    def get_sorted_model_configs(self) -> list[dict]:
        """Get models sorted by priority.

        Returns:
            List of model configurations sorted by priority
        """
        configs = []
        for p_name, models in self._limits.items():
            if not isinstance(models, dict):
                continue
            for m_name, lits in models.items():
                if not isinstance(lits, dict):
                    continue
                prio = lits.get("priority", 999)
                configs.append(
                    {
                        "provider": p_name,
                        "model": m_name,
                        "priority": prio,
                    }
                )
        return sorted(configs, key=lambda x: x["priority"])

    def can_accept(self, provider: str, model: str, tokens: int, now: float) -> bool:
        """Check if a request can be accepted (always true with LiteLLM).

        LiteLLM handles rate limiting internally, so this always returns True.
        The router will retry or fall back automatically on rate limit errors.

        Args:
            provider: Provider name (ignored)
            model: Model name (ignored)
            tokens: Token count (ignored)
            now: Current time (ignored)

        Returns:
            Always True - LiteLLM manages rate limits internally
        """
        return True

    def commit_usage(self, provider: str, model: str, tokens: int, now: float):
        """Commit usage (no-op with LiteLLM).

        LiteLLM tracks usage internally, so this is a no-op.

        Args:
            provider: Provider name (ignored)
            model: Model name (ignored)
            tokens: Token count (ignored)
            now: Current time (ignored)
        """
        pass

    def time_until_available(self, provider: str, model: str, tokens: int, now: float) -> float:
        """Time until available (always 0 with LiteLLM).

        LiteLLM handles rate limiting internally, so this always returns 0.

        Args:
            provider: Provider name (ignored)
            model: Model name (ignored)
            tokens: Token count (ignored)
            now: Current time (ignored)

        Returns:
            0 - LiteLLM manages rate limits internally
        """
        return 0.0
