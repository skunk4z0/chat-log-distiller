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
                    # LiteLLM uses "order" (lower = higher priority) in litellm_params
                    "order": config.get("priority", 999),
                },
                # Keep priority for reference but don't use at top level
                # "priority": config.get("priority", 999),
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
    routing_strategy: str = "simple-shuffle",  # Changed from latency-based-routing
) -> Router:
    """Create a LiteLLM Router from api_limits.json.

    Args:
        limits_path: Path to api_limits.json
        num_retries: Number of retries on rate limit errors
        timeout: Request timeout in seconds
        routing_strategy: Routing strategy (simple-shuffle recommended for free tier)

    Returns:
        Initialized LiteLLM Router
    """
    model_list = create_model_list(limits_path)

    # Define fallbacks for robust error handling
    # When a model hits rate limit (429), it will try fallback models
    fallbacks = [
        # Gemini models fallback to Groq -> OpenRouter
        {"gemini-2.5-flash-lite": ["groq/llama-3.3-70b-versatile", "openrouter/auto"]},
        {"gemini-2.5-flash": ["groq/llama-3.3-70b-versatile", "openrouter/auto"]},
        {"gemini-3.1-flash-lite-preview": ["groq/llama-3.3-70b-versatile", "openrouter/auto"]},
        # Groq models fallback to Gemini -> OpenRouter
        {"groq/llama-3.1-8b-instant": ["gemini-2.5-flash", "openrouter/auto"]},
        {"groq/llama-3.3-70b-versatile": ["gemini-2.5-flash", "openrouter/auto"]},
        {"groq/qwen/qwen3-32b": ["gemini-2.5-flash", "openrouter/auto"]},
        {"groq/mixtral-8x7b-32768": ["gemini-2.5-flash", "openrouter/auto"]},
        {"groq/meta-llama/llama-4-scout-17b-16e-instruct": ["gemini-2.5-flash", "openrouter/auto"]},
        {"groq/openai/gpt-oss-20b": ["gemini-2.5-flash", "openrouter/auto"]},
        # Mistral fallback to Groq -> OpenRouter
        {"mistral/mistral-small-latest": ["groq/llama-3.3-70b-versatile", "openrouter/auto"]},
        # OpenRouter as final fallback
        {"openrouter/auto": ["gemini-2.5-flash", "groq/llama-3.3-70b-versatile"]},
    ]

    router = Router(
        model_list=model_list,
        routing_strategy=routing_strategy,  # simple-shuffle for free tier stability
        num_retries=num_retries,
        timeout=timeout,
        # Fallback configuration
        fallbacks=fallbacks,
        # Allow more failures before disabling a model
        allowed_fails=5,
        # Enable pre-call checks for model availability
        enable_pre_call_checks=True,
        # Retry policy for 429 errors
        retry_policy={
            "429": {
                "num_retries": 3,
                "timeout": 30,
            },
        },
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
    print(f"Routing strategy: simple-shuffle (recommended for free tier)")
    print(f"Fallbacks configured: Yes")
    print(f"Pre-call checks: Enabled")
    print("-" * 50)

    # Test 1: Basic completion
    try:
        response = completion(router, test_messages)
        print(f"Test 1 Response: {response.choices[0].message.content}")
    except Exception as e:
        print(f"Test 1 Error: {e}")

    print("-" * 50)

    # Test 2: Multiple calls to verify shuffle and fallback
    for i in range(3):
        print(f"\nTest 2-{i+1}: Multiple call test (checking shuffle)")
        try:
            response = completion(router, test_messages)
            print(f"  Response: {response.choices[0].message.content}")
            print(f"  Model used: {response._hidden_params.get('model', 'unknown')}")
        except Exception as e:
            print(f"  Error: {e}")

    print("\n" + "=" * 50)
    print("Router configuration summary:")
    print(f"  - Models loaded: {len(router.model_list)}")
    print(f"  - Routing strategy: simple-shuffle")
    print(f"  - Fallbacks: {len(fallbacks)} configured")
    print(f"  - num_retries: {router.num_retries}")
    print(f"  - timeout: {router.timeout}s")
    print("=" * 50)