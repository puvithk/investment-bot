"""Indian equity delivery (CNC) trading costs."""
from __future__ import annotations


def trade_cost(side: str, value: float, c: dict) -> float:
    """Total charges in INR for one order of `value` rupees on `side` (BUY/SELL)."""
    brokerage = c.get("brokerage_per_order", 0.0)
    stt = value * c.get("stt_pct", 0.0)
    exchange = value * c.get("exchange_txn_pct", 0.0)
    sebi = value * c.get("sebi_fee_pct", 0.0)
    gst = (brokerage + exchange + sebi) * c.get("gst_pct", 0.0)
    stamp = value * c.get("stamp_duty_buy_pct", 0.0) if side == "BUY" else 0.0
    dp = c.get("dp_charge_per_sell", 0.0) if side == "SELL" else 0.0
    return round(brokerage + stt + exchange + sebi + gst + stamp + dp, 2)
