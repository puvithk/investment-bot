"""The analysts and the decision maker.

Each analyst looks at one kind of evidence and returns a view. The portfolio
manager weighs those views and proposes one action per stock. Proposals then
go through the risk rules (risk.py), which can shrink or veto them.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .data.indicators import technical_score
from .llm import LLM

ACTIONS = ("BUY", "SELL", "HOLD")


@dataclass
class View:
    analyst: str
    stance: float          # -1 very bearish .. +1 very bullish
    summary: str


@dataclass
class Proposal:
    symbol: str
    action: str            # BUY | SELL | HOLD
    confidence: float      # 0..1
    size_pct: float        # fraction of equity the AI would like to put in
    stop_loss_pct: float | None
    reasoning: str
    views: list[View] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


TECH_SYSTEM = (
    "You are a technical analyst for Indian equities (NSE). You get indicator values "
    "and candlestick patterns for one stock. Judge the short-to-medium term trend "
    'and momentum. Return {"stance": number from -1 to 1, "summary": "2-3 sentences"}.'
)
NEWS_SYSTEM = (
    "You are a news analyst for Indian equities. You get last week's headlines about "
    "one company. Judge whether the news is material and which way it points. Ignore "
    "generic market noise and paid promotions. "
    'Return {"stance": number from -1 to 1, "summary": "2-3 sentences"}.'
)
FUND_SYSTEM = (
    "You are a fundamentals analyst for Indian equities. You get valuation and quality "
    "numbers for one company. Judge whether it is reasonably priced for its quality. "
    'Return {"stance": number from -1 to 1, "summary": "2-3 sentences"}.'
)
PM_SYSTEM = (
    "You are the portfolio manager of a small fund that must stay profitable every "
    "quarter or be shut down, so protecting capital matters more than chasing gains. "
    "You get analyst views on one NSE stock and the current position. Decide BUY, SELL "
    "(only if we hold it) or HOLD. Trading costs about 0.25% round trip, so only trade "
    "with a real edge. Return {\"action\": \"BUY|SELL|HOLD\", \"confidence\": 0..1, "
    "\"size_pct\": fraction of equity 0..0.15, \"stop_loss_pct\": 0.02..0.10, "
    "\"reasoning\": \"3-5 sentences citing the evidence\"}."
)


class Analysts:
    def __init__(self, llm: LLM | None):
        self.llm = llm

    def technical(self, snap: dict) -> View:
        rule = technical_score(snap)
        if not self.llm:
            return View("technical", rule, f"Rules score {rule:+.2f} from trend, MACD, RSI and patterns.")
        out = self.llm.chat_json(TECH_SYSTEM, json.dumps(snap))
        return View("technical", _clip(out.get("stance", rule)), str(out.get("summary", "")))

    def news(self, symbol: str, headlines: list[dict]) -> View:
        if not headlines:
            return View("news", 0.0, "No headlines in the last week.")
        if not self.llm:
            return View("news", 0.0, f"{len(headlines)} headlines; not scored without an AI model.")
        lines = "\n".join(f"- {h['title']} ({h['source']}, {h['published']})" for h in headlines)
        out = self.llm.chat_json(NEWS_SYSTEM, f"Company: {symbol}\nHeadlines:\n{lines}")
        return View("news", _clip(out.get("stance", 0)), str(out.get("summary", "")))

    def fundamentals(self, symbol: str, fundamentals: dict | None) -> View:
        if not fundamentals:
            return View("fundamentals", 0.0, "No fundamentals available.")
        if not self.llm:
            return View("fundamentals", 0.0, "Not scored without an AI model.")
        out = self.llm.chat_json(FUND_SYSTEM, f"Company: {symbol}\n{json.dumps(fundamentals)}")
        return View("fundamentals", _clip(out.get("stance", 0)), str(out.get("summary", "")))


class PortfolioManager:
    def __init__(self, llm: LLM | None, default_size_pct: float = 0.10):
        self.llm = llm
        self.default_size_pct = default_size_pct

    def decide(self, symbol: str, views: list[View], position_qty: int, snap: dict) -> Proposal:
        if not self.llm:
            return self._rules_decision(symbol, views, position_qty)
        payload = {
            "symbol": symbol,
            "price": snap.get("price"),
            "atr14_pct": snap.get("atr14_pct"),
            "currently_held_qty": position_qty,
            "views": [asdict(v) for v in views],
        }
        out = self.llm.chat_json(PM_SYSTEM, json.dumps(payload))
        action = str(out.get("action", "HOLD")).upper()
        if action not in ACTIONS:
            action = "HOLD"
        return Proposal(
            symbol=symbol,
            action=action,
            confidence=_clip(out.get("confidence", 0), 0, 1),
            size_pct=_clip(out.get("size_pct", self.default_size_pct), 0, 1),
            stop_loss_pct=_opt_float(out.get("stop_loss_pct")),
            reasoning=str(out.get("reasoning", "")),
            views=views,
        )

    def _rules_decision(self, symbol: str, views: list[View], position_qty: int) -> Proposal:
        tech = next((v.stance for v in views if v.analyst == "technical"), 0.0)
        if tech >= 0.5 and position_qty == 0:
            action = "BUY"
        elif tech <= -0.3 and position_qty > 0:
            action = "SELL"
        else:
            action = "HOLD"
        return Proposal(
            symbol=symbol,
            action=action,
            confidence=round(min(1.0, abs(tech) + 0.2), 3),
            size_pct=self.default_size_pct,
            stop_loss_pct=None,
            reasoning=f"Rules-only mode: technical score {tech:+.2f}.",
            views=views,
        )


def _clip(x, lo: float = -1.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _opt_float(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None
