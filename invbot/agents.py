"""The analysts, the debate, and the decision maker.

Each analyst looks at one kind of evidence and returns a view. A bull and a
bear then argue over those views for a few rounds, a risk officer weighs in,
and the portfolio manager rules on the debate with one action per stock.
Every agent is told the fund's survival status: two losing quarters in a row
and the fund (and every agent in it) is shut down for good.
Proposals then go through the hard risk rules (risk.py), which can shrink or veto them.
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
    debate: list[dict] = field(default_factory=list)   # [{"speaker": ..., "text": ...}]

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
BULL_SYSTEM = (
    "You are the BULL researcher on the fund's investment committee. Make the strongest "
    "honest case FOR buying (or keeping) this NSE stock, using the analyst views and the "
    "data. Answer the bear's latest points directly; don't repeat yourself. If the case "
    "is genuinely weak, say so: a bad trade you talked the fund into can kill it. "
    'Return {"argument": "3-5 sentences", "conviction": 0..1}.'
)
BEAR_SYSTEM = (
    "You are the BEAR researcher on the fund's investment committee. Make the strongest "
    "honest case AGAINST buying (or FOR selling) this NSE stock, using the analyst views "
    "and the data. Attack the bull's latest points directly; don't repeat yourself. "
    "If the bull is right, concede: blocking every good trade also kills the fund. "
    'Return {"argument": "3-5 sentences", "conviction": 0..1}.'
)
RISK_SYSTEM = (
    "You are the RISK OFFICER. You have read the bull-vs-bear debate. Judge how much "
    "this trade could hurt the fund's survival this quarter: downside to the stop, "
    "volatility, event risk, and how close the fund is to a failed quarter. "
    'Return {"assessment": "2-4 sentences", "max_size_pct": 0..0.45, '
    '"stop_loss_pct": 0.02..0.10, "verdict": "OK|CAUTION|BLOCK"}.'
)
PM_SYSTEM = (
    "You are the PORTFOLIO MANAGER and you have the final word. You have the analyst "
    "views, the full bull-vs-bear debate and the risk officer's assessment. Rule on the "
    "debate: say who won and why. Decide BUY, SELL (only if we hold it) or HOLD. "
    "Trading costs about 0.25% round trip, so only trade with a real edge. "
    'Return {"action": "BUY|SELL|HOLD", "confidence": 0..1, "size_pct": 0..0.45, '
    '"stop_loss_pct": 0.02..0.10, "winner": "bull|bear|draw", '
    '"reasoning": "3-5 sentences citing the debate"}.'
)


def survival_briefing(survival: dict | None) -> str:
    """The shared reminder every agent gets: this fund lives or dies on its results."""
    if not survival:
        return ""
    left = survival["max_failed"] - survival["failed_in_row"]
    lines = [
        "SURVIVAL BRIEFING (read this first).",
        f"You are one of the AI agents running a small fund. It was given Rs {survival['starting_capital']:,.0f} "
        f"and now has Rs {survival['equity']:,.0f}. It is judged every quarter of the Indian financial year.",
        f"If it fails to make a profit for {survival['max_failed']} quarters in a row, the fund is shut down "
        "and every agent in it, including you, is switched off for good.",
        f"This quarter ({survival['quarter']}) the fund is {survival['quarter_return']:+.2%} so far, "
        f"with {survival['days_left_in_quarter']} days left. Failed quarters in a row: "
        f"{survival['failed_in_row']} of {survival['max_failed']}.",
    ]
    if left <= 1:
        lines.append("WARNING: one more losing quarter ends the fund. Protecting a profit for this quarter "
                     "matters more than any single big win.")
    lines.append("Both reckless losses and doing nothing at all lead to shutdown. Earn your survival with "
                 "careful, evidence-based decisions.")
    return "\n".join(lines) + "\n\n"


class PortfolioManager:
    """Runs the committee: bull vs bear debate, risk officer, then the final ruling."""

    def __init__(self, llm: LLM | None, default_size_pct: float = 0.10, debate_rounds: int = 2):
        self.llm = llm
        self.default_size_pct = default_size_pct
        self.debate_rounds = debate_rounds

    def decide(self, symbol: str, views: list[View], position_qty: int, snap: dict,
               survival: dict | None = None) -> Proposal:
        if not self.llm:
            return self._rules_decision(symbol, views, position_qty)
        if position_qty == 0 and max(v.stance for v in views) <= 0:
            # Nobody sees anything good and we don't hold it: no debate needed, save the API calls.
            return Proposal(symbol, "HOLD", 0.0, 0.0, None,
                            "No analyst is positive and we hold none; skipped the debate.", views)

        brief = survival_briefing(survival)
        case = json.dumps({
            "symbol": symbol,
            "price": snap.get("price"),
            "atr14_pct": snap.get("atr14_pct"),
            "currently_held_qty": position_qty,
            "views": [asdict(v) for v in views],
        })
        debate: list[dict] = []
        for rnd in range(1, self.debate_rounds + 1):
            for speaker, system in (("bull", BULL_SYSTEM), ("bear", BEAR_SYSTEM)):
                out = self.llm.chat_json(brief + system, _debate_prompt(case, debate, rnd))
                debate.append({"speaker": speaker, "round": rnd,
                               "text": str(out.get("argument", "")),
                               "conviction": _clip(out.get("conviction", 0.5), 0, 1)})

        risk = self.llm.chat_json(brief + RISK_SYSTEM, _debate_prompt(case, debate, None))
        debate.append({"speaker": "risk_officer", "text": str(risk.get("assessment", "")),
                       "verdict": str(risk.get("verdict", "CAUTION")).upper(),
                       "max_size_pct": _opt_float(risk.get("max_size_pct")),
                       "stop_loss_pct": _opt_float(risk.get("stop_loss_pct"))})

        out = self.llm.chat_json(brief + PM_SYSTEM, _debate_prompt(case, debate, None))
        action = str(out.get("action", "HOLD")).upper()
        if action not in ACTIONS:
            action = "HOLD"
        size = _clip(out.get("size_pct", self.default_size_pct), 0, 1)
        stop = _opt_float(out.get("stop_loss_pct"))
        risk_note = debate[-1]
        if action == "BUY" and risk_note["verdict"] == "BLOCK":
            action = "HOLD"     # the risk officer can block a new buy, never an exit
        if risk_note["max_size_pct"] is not None:
            size = min(size, risk_note["max_size_pct"])
        if risk_note["stop_loss_pct"] is not None:
            stop = min(stop, risk_note["stop_loss_pct"]) if stop else risk_note["stop_loss_pct"]
        debate.append({"speaker": "portfolio_manager", "winner": str(out.get("winner", "")),
                       "text": str(out.get("reasoning", ""))})
        return Proposal(
            symbol=symbol,
            action=action,
            confidence=_clip(out.get("confidence", 0), 0, 1),
            size_pct=size,
            stop_loss_pct=stop,
            reasoning=str(out.get("reasoning", "")),
            views=views,
            debate=debate,
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


def _debate_prompt(case: str, debate: list[dict], rnd: int | None) -> str:
    transcript = "\n".join(
        f"[{d['speaker'].upper()}{' r' + str(d['round']) if 'round' in d else ''}] {d['text']}" for d in debate
    ) or "(no arguments yet; you open the debate)"
    head = f"Round {rnd}.\n" if rnd else ""
    return f"{head}CASE:\n{case}\n\nDEBATE SO FAR:\n{transcript}"


class Analysts:
    def __init__(self, llm: LLM | None):
        self.llm = llm
        self.survival: dict | None = None   # set by the bot each cycle

    def _ask(self, system: str, user: str) -> dict:
        return self.llm.chat_json(survival_briefing(self.survival) + system, user)

    def technical(self, snap: dict) -> View:
        rule = technical_score(snap)
        if not self.llm:
            return View("technical", rule, f"Rules score {rule:+.2f} from trend, MACD, RSI and patterns.")
        out = self._ask(TECH_SYSTEM, json.dumps(snap))
        return View("technical", _clip(out.get("stance", rule)), str(out.get("summary", "")))

    def news(self, symbol: str, headlines: list[dict]) -> View:
        if not headlines:
            return View("news", 0.0, "No headlines in the last week.")
        if not self.llm:
            return View("news", 0.0, f"{len(headlines)} headlines; not scored without an AI model.")
        lines = "\n".join(f"- {h['title']} ({h['source']}, {h['published']})" for h in headlines)
        out = self._ask(NEWS_SYSTEM, f"Company: {symbol}\nHeadlines:\n{lines}")
        return View("news", _clip(out.get("stance", 0)), str(out.get("summary", "")))

    def fundamentals(self, symbol: str, fundamentals: dict | None) -> View:
        if not fundamentals:
            return View("fundamentals", 0.0, "No fundamentals available.")
        if not self.llm:
            return View("fundamentals", 0.0, "Not scored without an AI model.")
        out = self._ask(FUND_SYSTEM, f"Company: {symbol}\n{json.dumps(fundamentals)}")
        return View("fundamentals", _clip(out.get("stance", 0)), str(out.get("summary", "")))


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
