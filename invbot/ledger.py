"""SQLite record of everything the bot sees, decides and does.

Every proposal is stored with the analyst views and the risk verdict, so any
trade can be traced back to the evidence behind it.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY, qty INTEGER, avg_price REAL, stop_loss REAL, opened_at TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, action TEXT,
    confidence REAL, approved INTEGER, risk_note TEXT, proposal_json TEXT, snapshot_json TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, side TEXT, qty INTEGER,
    price REAL, costs REAL, realized_pnl REAL, broker_ref TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS equity (day TEXT PRIMARY KEY, cash REAL, equity REAL);
"""


class Ledger:
    def __init__(self, path: str = "invbot.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # key/value state (cash, shutdown flag, ...)
    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    # positions
    def positions(self) -> dict[str, sqlite3.Row]:
        rows = self.db.execute("SELECT * FROM positions WHERE qty > 0").fetchall()
        return {r["symbol"]: r for r in rows}

    def upsert_position(self, symbol: str, qty: int, avg_price: float, stop: float | None, ts: str) -> None:
        if qty <= 0:
            self.db.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO positions VALUES (?, ?, ?, ?, ?)", (symbol, qty, avg_price, stop, ts)
            )
        self.db.commit()

    # records
    def log_decision(self, ts: str, proposal: dict, approved: bool, note: str, snapshot: dict) -> None:
        self.db.execute(
            "INSERT INTO decisions (ts, symbol, action, confidence, approved, risk_note, proposal_json, snapshot_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, proposal["symbol"], proposal["action"], proposal["confidence"], int(approved), note,
             json.dumps(proposal), json.dumps(snapshot)),
        )
        self.db.commit()

    def log_fill(self, ts: str, symbol: str, side: str, qty: int, price: float, costs: float,
                 realized_pnl: float, broker_ref: str, reason: str) -> None:
        self.db.execute(
            "INSERT INTO fills (ts, symbol, side, qty, price, costs, realized_pnl, broker_ref, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, symbol, side, qty, price, costs, realized_pnl, broker_ref, reason),
        )
        self.db.commit()

    def record_equity(self, day: date, cash: float, equity: float) -> None:
        self.db.execute("INSERT OR REPLACE INTO equity VALUES (?, ?, ?)", (day.isoformat(), cash, equity))
        self.db.commit()

    def equity_curve(self) -> list[tuple[date, float]]:
        rows = self.db.execute("SELECT day, equity FROM equity ORDER BY day").fetchall()
        return [(date.fromisoformat(r["day"]), r["equity"]) for r in rows]

    def trades_on(self, day: date) -> int:
        row = self.db.execute("SELECT COUNT(*) AS n FROM fills WHERE substr(ts, 1, 10)=?", (day.isoformat(),)).fetchone()
        return row["n"]


def now_iso(ts: datetime | None = None) -> str:
    return (ts or datetime.now()).isoformat(timespec="seconds")
