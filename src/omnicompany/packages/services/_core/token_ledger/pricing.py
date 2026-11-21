"""Deterministic API-price estimates for one native session ledger row.

This is an estimate for customer-facing quoting. It uses official public API
rates and the token counts recorded by the native CLIs; it is not an invoice
reader and deliberately leaves unknown models unpriced.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
import json
from pathlib import Path
from typing import Any, Iterable


OPENAI_PRICING_URL = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC_PRICING_URL = "https://docs.anthropic.com/en/docs/about-claude/pricing"
DEFAULT_FX_RATE_CNY_PER_USD = Decimal("7.0")
PRICING_AS_OF = "2026-10-13"
_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class ModelPrice:
    provider_family: str
    input_usd_per_mtok: Decimal
    cached_input_usd_per_mtok: Decimal
    cache_write_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    source_url: str
    cache_write_assumption: str | None = None


def _openai(model: str, input_rate: str, cached: str, write: str, output: str) -> tuple[str, ModelPrice]:
    return model, ModelPrice(
        "openai", Decimal(input_rate), Decimal(cached), Decimal(write), Decimal(output), OPENAI_PRICING_URL
    )


def _anthropic(model: str, input_rate: str, cached: str, write: str, output: str) -> tuple[str, ModelPrice]:
    return model, ModelPrice(
        "anthropic", Decimal(input_rate), Decimal(cached), Decimal(write), Decimal(output),
        ANTHROPIC_PRICING_URL, "Claude cache creation uses the documented 5-minute write multiplier.",
    )


# Rates are USD per million tokens. OpenAI rows use the standard/short-context
# table. Claude rows use standard <=200K context rates; long-context requests
# need a future request-level context marker to price at the higher tier.
MODEL_PRICES: dict[str, ModelPrice] = dict([
    _openai("gpt-6-astra", "10", "1", "12.5", "50"),
    _openai("gpt-6.1-sol", "2", "0.1", "2.5", "10"),
    _openai("gpt-6-luna", "0.1", "0.01", "0.125", "0.5"),
    _anthropic("claude-opus-5-5", "4", "0.2", "5", "20"),
    _anthropic("claude-sonnet-5-5", "2", "0.1", "2.5", "10"),
    _anthropic("claude-haiku-5-5", "0.1", "0.01", "0.125", "0.5"),
    _anthropic("claude-fable-5-1", "10", "0.25", "12.5", "50"),
    _anthropic("claude-sonnet-4-6", "3", "0.3", "3.75", "15"),
    _anthropic("claude-haiku-4-5", "1", "0.1", "1.25", "5"),
    _anthropic("claude-opus-4-6", "5", "0.5", "6.25", "25"),
])


def _normal_model(value: Any) -> str:
    return str(value or "unknown").strip().lower()


def lookup_model_price(model: Any) -> ModelPrice | None:
    model_id = _normal_model(model)
    direct = MODEL_PRICES.get(model_id)
    if direct is not None:
        return direct
    # Native logs sometimes carry a provider prefix. Do not use a fuzzy model
    # family match: a near miss must remain visibly unpriced.
    if "/" in model_id:
        return MODEL_PRICES.get(model_id.rsplit("/", 1)[-1])
    return None


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=ROUND_DOWN), "f")


def _tokens(bucket: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(bucket.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def price_model_bucket(model: str, bucket: dict[str, Any], provider: str) -> dict[str, Any]:
    price = lookup_model_price(model)
    input_tokens = _tokens(bucket, "input_tokens")
    cache_read_tokens = _tokens(bucket, "cache_read_tokens")
    cache_creation_tokens = _tokens(bucket, "cache_creation_tokens")
    output_tokens = _tokens(bucket, "output_tokens")
    # Codex's native input count includes its cached subset. Claude's
    # input_tokens is already the non-cached input count.
    billable_input = max(0, input_tokens - cache_read_tokens) if provider == "codex" else input_tokens
    row: dict[str, Any] = {
        "model": model,
        "provider": provider,
        "input_tokens": input_tokens,
        "billable_input_tokens": billable_input,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "output_tokens": output_tokens,
        "priced": price is not None,
        "context_pricing": "standard_assumption" if price is not None else None,
    }
    if price is None:
        row["cost_usd"] = None
        row["cost_cny"] = None
        row["pricing_source_url"] = None
        return row
    cost = (
        Decimal(billable_input) * price.input_usd_per_mtok
        + Decimal(cache_read_tokens) * price.cached_input_usd_per_mtok
        + Decimal(cache_creation_tokens) * price.cache_write_usd_per_mtok
        + Decimal(output_tokens) * price.output_usd_per_mtok
    ) / _MILLION
    row["cost_usd"] = _money(cost)
    row["pricing_source_url"] = price.source_url
    if price.cache_write_assumption:
        row["cache_write_assumption"] = price.cache_write_assumption
    return row


def price_session_row(
    row: dict[str, Any],
    *,
    fx_rate_cny_per_usd: Decimal = DEFAULT_FX_RATE_CNY_PER_USD,
) -> dict[str, Any]:
    session_id = str(row.get("session_id") or "")
    provider = str(row.get("provider") or "unknown")
    session_ref = f"{provider}:{session_id[-8:]}" if session_id else f"{provider}:unknown"
    model_rows = [
        price_model_bucket(str(model), bucket if isinstance(bucket, dict) else {}, provider)
        for model, bucket in sorted((row.get("by_model") or {}).items())
    ]
    for item in model_rows:
        if item["cost_usd"] is not None:
            item["cost_cny"] = format(
                (Decimal(item["cost_usd"]) * fx_rate_cny_per_usd).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                ),
                "f",
            )
    priced = bool(model_rows) and all(item["priced"] for item in model_rows)
    usd = sum((Decimal(item["cost_usd"]) for item in model_rows if item["cost_usd"] is not None), Decimal("0"))
    cny = (usd * fx_rate_cny_per_usd).quantize(Decimal("0.01"), rounding=ROUND_DOWN) if priced else None
    return {
        "kind": "session_cost",
        "session_ref": session_ref,
        "provider": provider,
        "calls": int(row.get("calls") or 0),
        "priced": priced,
        "models": model_rows,
        "total_usd": _money(usd) if priced else None,
        "total_cny": format(cny, "f") if cny is not None else None,
        "unpriced_models": [item["model"] for item in model_rows if not item["priced"]],
        "fx_rate_cny_per_usd": _money(fx_rate_cny_per_usd),
        "fx_rounding": "ROUND_DOWN",
        "pricing_as_of": PRICING_AS_OF,
        "pricing_sources": [OPENAI_PRICING_URL, ANTHROPIC_PRICING_URL],
        "note": "API price estimate from native token counts; not an invoice amount.",
    }


def export_session_cost(
    rows: Iterable[dict[str, Any]],
    *,
    out_path: Path,
    fx_rate_cny_per_usd: Decimal = DEFAULT_FX_RATE_CNY_PER_USD,
) -> dict[str, Any]:
    priced_rows = [price_session_row(row, fx_rate_cny_per_usd=fx_rate_cny_per_usd) for row in rows]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for item in priced_rows:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {
        "path": str(out_path.resolve()),
        "rows": len(priced_rows),
        "priced": bool(priced_rows) and all(item["priced"] for item in priced_rows),
    }


__all__ = [
    "ANTHROPIC_PRICING_URL",
    "DEFAULT_FX_RATE_CNY_PER_USD",
    "MODEL_PRICES",
    "OPENAI_PRICING_URL",
    "export_session_cost",
    "lookup_model_price",
    "price_model_bucket",
    "price_session_row",
]
