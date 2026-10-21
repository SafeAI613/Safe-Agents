"""OpenRouter API: checking a key and reading model prices (for the cost display)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from .config import OPENROUTER_API
from .errors import InvalidApiKeyError, UpstreamError

log = logging.getLogger(__name__)

Pricing = dict[str, dict[str, float]]


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_pricing(payload: Any) -> Pricing:
    """``{model_id: {"prompt": usd_per_token, "completion": usd_per_token}}``."""
    prices: Pricing = {}
    for model in payload.get("data", []) if isinstance(payload, dict) else []:
        pricing = model.get("pricing") or {}
        prompt, completion = _number(pricing.get("prompt")), _number(pricing.get("completion"))
        if isinstance(model.get("id"), str) and prompt is not None and completion is not None:
            prices[model["id"]] = {"prompt": prompt, "completion": completion}
    return prices


class OpenRouterClient:
    def __init__(
        self,
        http: httpx.Client,
        cache_dir: Path,
        base_url: str = OPENROUTER_API,
        pricing_ttl_s: float = 24 * 3600,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._pricing_file = cache_dir / "openrouter-pricing.json"
        self._pricing_ttl_s = pricing_ttl_s

    def key_info(self, api_key: str) -> dict[str, Any]:
        try:
            response = self._http.get(
                f"{self._base_url}/key", headers={"Authorization": f"Bearer {api_key}"}
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"could not reach OpenRouter: {exc}") from exc
        if response.status_code in (401, 403):
            raise InvalidApiKeyError("OpenRouter rejected the API key")
        if response.status_code != 200:
            raise UpstreamError(f"OpenRouter answered HTTP {response.status_code}")
        data = response.json().get("data") or {}
        return {
            "label": data.get("label"),
            "usage_usd": _number(data.get("usage")),
            "limit_usd": _number(data.get("limit")),
            "limit_remaining_usd": _number(data.get("limit_remaining")),
            "is_free_tier": bool(data.get("is_free_tier", False)),
        }

    def pricing(self, *, refresh: bool = False) -> Pricing:
        cached = self._read_cache()
        if cached and not refresh and time.time() - cached[0] < self._pricing_ttl_s:
            return cached[1]
        try:
            response = self._http.get(f"{self._base_url}/models")
            response.raise_for_status()
            prices = parse_pricing(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            if cached:
                log.warning("using cached OpenRouter prices: %s", exc)
                return cached[1]
            raise UpstreamError(f"could not load OpenRouter prices: {exc}") from exc
        self._pricing_file.parent.mkdir(parents=True, exist_ok=True)
        self._pricing_file.write_text(
            json.dumps({"fetched": time.time(), "prices": prices}), encoding="utf-8"
        )
        return prices

    def _read_cache(self) -> tuple[float, Pricing] | None:
        try:
            data = json.loads(self._pricing_file.read_text(encoding="utf-8"))
            return float(data["fetched"]), data["prices"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
