# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T02:35:03+08:00 type=forecast-budget-ledger status=active
"""Append-only deterministic forecast budget ledger."""

from __future__ import annotations

from threading import Lock

from .models import ForecastBudgetState, ForecastReceipt, canonical_sha256


class ForecastBudgetLedger:
    """Meter one tournament run.

    A complete forecast always costs two raw operations: one immutable clone and
    one rollout.  The ledger deliberately keeps serving after the free quota and
    reports overage for the public scoring layer.
    """

    def __init__(
        self,
        ledger_id: str,
        *,
        raw_ops_limit: int = 200,
        forecast_limit: int = 100,
    ) -> None:
        if not ledger_id:
            raise ValueError("ledger_id must not be empty")
        if raw_ops_limit < 0 or forecast_limit < 0:
            raise ValueError("forecast limits must be non-negative")
        self.ledger_id = ledger_id
        self.raw_ops_limit = raw_ops_limit
        self.forecast_limit = forecast_limit
        self._raw_ops = 0
        self._forecasts = 0
        self._receipts: list[ForecastReceipt] = []
        self._lock = Lock()

    @property
    def total_raw_ops(self) -> int:
        with self._lock:
            return self._raw_ops

    @property
    def total_forecasts(self) -> int:
        with self._lock:
            return self._forecasts

    @property
    def receipts(self) -> tuple[ForecastReceipt, ...]:
        with self._lock:
            return tuple(self._receipts)

    def charge(self, request_hash: str) -> ForecastReceipt:
        with self._lock:
            self._raw_ops += 2
            self._forecasts += 1
            raw_overage = max(0, (self._raw_ops - self.raw_ops_limit + 1) // 2)
            forecast_overage = max(0, self._forecasts - self.forecast_limit)
            overage = max(raw_overage, forecast_overage)
            previous = self._receipts[-1].receipt_hash if self._receipts else None
            body = {
                "schema_id": ("omnicompany.arknights.tournament-sim.forecast-receipt.v1"),
                "ledger_id": self.ledger_id,
                "forecast_index": self._forecasts,
                "raw_ops_charged": 2,
                "total_raw_ops": self._raw_ops,
                "total_forecasts": self._forecasts,
                "overage_forecasts": overage,
                "over_limit": overage > 0,
                "request_hash": request_hash,
                "previous_receipt_hash": previous,
            }
            receipt = ForecastReceipt(**body, receipt_hash=canonical_sha256(body))
            self._receipts.append(receipt)
            return receipt

    def verify_chain(self) -> bool:
        with self._lock:
            previous: str | None = None
            for receipt in self._receipts:
                body = receipt.model_dump(mode="json", exclude={"receipt_hash"})
                if receipt.previous_receipt_hash != previous:
                    return False
                if receipt.receipt_hash != canonical_sha256(body):
                    return False
                previous = receipt.receipt_hash
            return True

    def export_state(self) -> ForecastBudgetState:
        with self._lock:
            return ForecastBudgetState(
                ledger_id=self.ledger_id,
                raw_ops_limit=self.raw_ops_limit,
                forecast_limit=self.forecast_limit,
                total_raw_ops=self._raw_ops,
                total_forecasts=self._forecasts,
                receipts=tuple(self._receipts),
            )

    @classmethod
    def from_state(cls, state: ForecastBudgetState) -> "ForecastBudgetLedger":
        if state.total_raw_ops != state.total_forecasts * 2:
            raise ValueError("budget totals are inconsistent")
        if len(state.receipts) != state.total_forecasts:
            raise ValueError("budget receipt count is inconsistent")
        ledger = cls(
            state.ledger_id,
            raw_ops_limit=state.raw_ops_limit,
            forecast_limit=state.forecast_limit,
        )
        ledger._raw_ops = state.total_raw_ops
        ledger._forecasts = state.total_forecasts
        ledger._receipts = list(state.receipts)
        if not ledger.verify_chain():
            raise ValueError("budget receipt chain is invalid")
        for index, receipt in enumerate(state.receipts, start=1):
            if (
                receipt.ledger_id != state.ledger_id
                or receipt.forecast_index != index
                or receipt.total_forecasts != index
                or receipt.total_raw_ops != index * 2
            ):
                raise ValueError("budget receipt fields are inconsistent")
        return ledger


__all__ = ["ForecastBudgetLedger"]
