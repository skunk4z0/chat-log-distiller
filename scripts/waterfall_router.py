import json
import logging
import time
from collections import deque
from pathlib import Path

import tiktoken

logger = logging.getLogger("pipeline.tracker")


class TokenTracker:
    def __init__(self, limits_file: Path):
        self.limits_file = limits_file
        self.limits = self._load_limits()
        # provider -> model -> { "rpd": count, "rpm_q": deque[(time, count)], "tpm_q": deque[(time, count)] }
        self.usage: dict[str, dict[str, dict]] = {}
        try:
            self.enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.enc = tiktoken.get_encoding("gpt2")

    def _load_limits(self) -> dict:
        if not self.limits_file.exists():
            logger.warning("api_limits.json not found at %s. Using default high limits.", self.limits_file)
            return {}
        with open(self.limits_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _get_limit(self, provider: str, model: str, key: str, default: int) -> int:
        prov_limits = self.limits.get(provider, {})
        model_limits = prov_limits.get(model, {})
        # Fallback to provider-level if model specific is not found, else default
        if key in model_limits:
            return model_limits[key]
        if key in prov_limits and isinstance(prov_limits[key], int):
            return prov_limits[key]
        return default

    def estimate_tokens(self, text: str, max_output_tokens: int | None = None) -> int:
        input_tokens = len(self.enc.encode(text))
        output_tokens = max_output_tokens if max_output_tokens is not None else 0
        return input_tokens + output_tokens

    def _cleanup_queues(self, provider: str, model: str, now: float):
        if provider not in self.usage:
            self.usage[provider] = {}
        if model not in self.usage[provider]:
            self.usage[provider][model] = {
                "rpd": 0,
                "rpm_q": deque(),
                "tpm_q": deque()
            }
        
        usage = self.usage[provider][model]
        # Remove items older than 60 seconds
        cutoff = now - 60.0
        
        while usage["rpm_q"] and usage["rpm_q"][0][0] < cutoff:
            usage["rpm_q"].popleft()
            
        while usage["tpm_q"] and usage["tpm_q"][0][0] < cutoff:
            usage["tpm_q"].popleft()

    def get_current_usage(self, provider: str, model: str, now: float) -> tuple[int, int, int]:
        self._cleanup_queues(provider, model, now)
        usage = self.usage[provider][model]
        
        rpd = usage["rpd"]
        rpm = sum(count for _, count in usage["rpm_q"])
        tpm = sum(count for _, count in usage["tpm_q"])
        return rpd, rpm, tpm

    def can_accept(self, provider: str, model: str, tokens: int, now: float) -> bool:
        self._cleanup_queues(provider, model, now)
        usage = self.usage[provider][model]
        
        limit_rpd = self._get_limit(provider, model, "rpd", 100000)
        limit_rpm = self._get_limit(provider, model, "rpm", 1000)
        limit_tpm = self._get_limit(provider, model, "tpm", 1000000)
        
        rpd, rpm, tpm = self.get_current_usage(provider, model, now)
        
        if rpd + 1 > limit_rpd:
            return False
        if rpm + 1 > limit_rpm:
            return False
        if tpm + tokens > limit_tpm:
            return False
            
        return True

    def commit_usage(self, provider: str, model: str, tokens: int, now: float):
        self._cleanup_queues(provider, model, now)
        usage = self.usage[provider][model]
        
        usage["rpd"] += 1
        usage["rpm_q"].append((now, 1))
        usage["tpm_q"].append((now, tokens))

    def time_until_available(self, provider: str, model: str, tokens: int, now: float) -> float:
        """Returns seconds to wait until the request can be accepted. Returns float('inf') if RPD is exhausted."""
        if self.can_accept(provider, model, tokens, now):
            return 0.0
            
        limit_rpd = self._get_limit(provider, model, "rpd", 100000)
        limit_rpm = self._get_limit(provider, model, "rpm", 1000)
        limit_tpm = self._get_limit(provider, model, "tpm", 1000000)
        
        rpd, rpm, tpm = self.get_current_usage(provider, model, now)
        usage = self.usage[provider][model]
        
        if rpd + 1 > limit_rpd:
            return float('inf')  # Daily limit reached
            
        wait_times = []
        
        # Need to wait until enough RPM clears
        if rpm + 1 > limit_rpm and usage["rpm_q"]:
            # Find the time when the oldest request drops out of the 60s window
            oldest_time = usage["rpm_q"][0][0]
            wait_times.append(oldest_time + 60.0 - now)
            
        # Need to wait until enough TPM clears
        if tpm + tokens > limit_tpm and usage["tpm_q"]:
            # We need to drop enough tokens to fit the new request
            tokens_to_drop = (tpm + tokens) - limit_tpm
            dropped = 0
            for t, count in usage["tpm_q"]:
                dropped += count
                if dropped >= tokens_to_drop:
                    wait_times.append(t + 60.0 - now)
                    break
                    
        if not wait_times:
            return 1.0 # fallback
            
        return max(0.1, max(wait_times))
