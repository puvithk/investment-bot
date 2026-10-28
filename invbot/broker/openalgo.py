"""Live broker through a self-hosted OpenAlgo server (https://docs.openalgo.in).

OpenAlgo talks to the real broker (Dhan, Upstox, Zerodha, ...). The API key is
read from the OPENALGO_API_KEY environment variable, never from config.
Check field names against the OpenAlgo docs for your version before going live.
"""
from __future__ import annotations

import requests

from ..config import secret
from ..ledger import Ledger
from ..risk import Order


class OpenAlgoBroker:
    name = "openalgo"

    def __init__(self, ledger: Ledger, host: str, strategy: str = "invbot", timeout: int = 15):
        self.ledger = ledger
        self.host = host.rstrip("/")
        self.strategy = strategy
        self.timeout = timeout
        self.api_key = secret("OPENALGO_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENALGO_API_KEY is not set")

    def _post(self, path: str, payload: dict | None = None) -> dict:
        body = {"apikey": self.api_key, **(payload or {})}
        resp = requests.post(f"{self.host}/api/v1/{path}", json=body, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"OpenAlgo {path} failed: {data}")
        return data.get("data", data)

    def cash(self) -> float:
        return float(self._post("funds")["availablecash"])

    def holdings(self) -> dict[str, int]:
        data = self._post("holdings")
        rows = data.get("holdings", []) if isinstance(data, dict) else data
        return {h["symbol"]: int(h["quantity"]) for h in rows if int(h.get("quantity", 0)) > 0}

    def place(self, order: Order, ts: str) -> dict:
        data = self._post("placeorder", {
            "strategy": self.strategy,
            "symbol": order.symbol,
            "exchange": "NSE",
            "action": order.side,
            "quantity": str(order.qty),
            "pricetype": "MARKET",
            "product": "CNC",
        })
        ref = str(data.get("orderid", ""))
        pos = self.ledger.positions().get(order.symbol)
        if order.side == "BUY":
            self.ledger.upsert_position(order.symbol, order.qty, order.price, order.stop_loss, ts)
        elif pos:
            self.ledger.upsert_position(order.symbol, 0, pos["avg_price"], None, ts)
        # Actual fill price and charges get reconciled from the broker's trade book later.
        self.ledger.log_fill(ts, order.symbol, order.side, order.qty, order.price, 0.0, 0.0, ref, order.reason)
        return {"status": "sent", "orderid": ref}
