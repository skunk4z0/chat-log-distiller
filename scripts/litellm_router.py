"""LiteLLM Router configuration from api_limits.json.

This module provides a Router initialized from api_limits.json,
delegating rate limit management to litellm.Router.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import litellm
from litellm import Router

# Enable detailed logging for debugging
litellm.suppress_debug_info = False
litellm.set_verbose = True


def create_model_list(limits_path: str | Path = "api_limits.json") -> list[dict[str, Any]]:
    """Generate LiteLLM model_list from api_limits.json.

    Args:
        limits_path: Path to api_limits.json

    Returns:
        List of model configurations for LiteLLM Router
    """
    with open(limits_path, "r", encoding="utf-8") as f:
        limits_data = json.load(f)

    model_list = []

    for provider, models in limits_data.items():
        for model_name, config in models.items():
            # Skip special keys like 'auto' or 'default'
            if not isinstance(config, dict) or "priority" not in config:
                continue

            # Build LiteLLM model configuration
            # Provider + model name combined for litellm format
            model_config = {
                "model_name": model_name,
                "provider": provider,  # Explicit provider key for main.py
                "model": model_name,  # Explicit model key for main.py
                "litellm_params": {
                    "model": f"{provider}/{model_name}",
                    "api_key": os.getenv(f"{provider.upper()}_API_KEY"),
                    "api_base": _get_api_base(provider),
                    "tpm_limit": config.get("tpm"),
                    "rpm_limit": config.get("rpm"),
                },
                "priority": config.get("priority", 999),
            }
            model_list.append(model_config)

    return model_list


def _get_api_base(provider: str) -> str | None:
    """Get API base URL for a provider from environment variables."""
    base_urls = {
        "gemini": os.environ.get("GEMINI_BASE_URL"),
        "groq": os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        "mistral": os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
        "openrouter": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api"),
    }
    return base_urls.get(provider)


def create_router(
    limits_path: str | Path = "api_limits.json",
    num_retries: int = 3,
    timeout: int = 60,
    routing_strategy: str = "latency-based-routing",
) -> Router:
    """Create a LiteLLM Router from api_limits.json.

    Args:
        limits_path: Path to api_limits.json
        num_retries: Number of retries on rate limit errors
        timeout: Request timeout in seconds
        routing_strategy: Routing strategy (latency-based-routing, priority-based-routing)

    Returns:
        Initialized LiteLLM Router
    """
    model_list = create_model_list(limits_path)

    router = Router(
        model_list=model_list,
        routing_strategy=routing_strategy,
        num_retries=num_retries,
        timeout=timeout,
        # Fallback settings
        allowed_fails=5,
        # Disable litellm's default retry on specific errors
    )

    return router


async def acompletion(
    router: Router,
    messages: list[dict[str, Any]],
    **kwargs,
) -> Any:
    """Async completion via LiteLLM Router.

    Args:
        router: LiteLLM Router instance
        messages: Chat messages
        **kwargs: Additional parameters passed to litellm

    Returns:
        LiteLLM response
    """
    return await router.acompletion(messages=messages, **kwargs)


def completion(
    router: Router,
    messages: list[dict[str, Any]],
    **kwargs,
) -> Any:
    """Sync completion via LiteLLM Router.

    Args:
        router: LiteLLM Router instance
        messages: Chat messages
        **kwargs: Additional parameters passed to litellm

    Returns:
        LiteLLM response
    """
    return router.completion(messages=messages, **kwargs)


# Execution example
if __name__ == "__main__":
    import asyncio

    router = create_router()

    # Test with a simple completion
    test_messages = [{"role": "user", "content": "Say 'OK' in one word."}]

    print("Testing LiteLLM Router...")
    try:
        response = completion(router, test_messages)
        print(f"Response: {response.choices[0].message.content}")
    except Exception as e:
        print(f"Error: {e}")