"""Command line: python -m invbot {run-once|status|scorecard} [--config config.yaml]"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date

from .agents import Analysts, PortfolioManager
from .bot import Bot, LiveData
from .config import load_config
from .ledger import Ledger
from .llm import make_llm
from .scorecard import quarterly_results, survival_check


def build_bot(cfg: dict) -> Bot:
    ledger = Ledger(cfg["storage"]["db_path"])
    mode = cfg["broker"]["mode"]
    if mode == "paper":
        from .broker.paper import PaperBroker
        broker = PaperBroker(ledger, cfg["starting_capital"], cfg["costs"])
    elif mode == "openalgo":
        from .broker.openalgo import OpenAlgoBroker
        broker = OpenAlgoBroker(ledger, cfg["broker"]["openalgo_host"], cfg["broker"]["openalgo_strategy"])
    else:
        raise SystemExit(f"Unknown broker mode {mode!r}")
    llm = make_llm(cfg)
    logging.info("LLM: %s", f"{llm.provider}/{llm.model}" if llm else "none (rules only)")
    rounds = (cfg.get("llm") or {}).get("debate_rounds", 2)
    return Bot(cfg, ledger, broker, LiveData(), Analysts(llm), PortfolioManager(llm, debate_rounds=rounds))


def main() -> None:
    ap = argparse.ArgumentParser(prog="invbot")
    ap.add_argument("command", choices=["run-once", "status", "scorecard", "backtest"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--start", default="2024-04-01", help="backtest: first simulated day")
    ap.add_argument("--end", default=None, help="backtest: last simulated day")
    ap.add_argument("--every", type=int, default=5, help="backtest: decide every N trading days")
    ap.add_argument("--ai", action="store_true", help="backtest: use the AI model (slow, costs API calls)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)

    if args.command == "run-once":
        report = build_bot(cfg).run_cycle()
        print(json.dumps(report.__dict__, default=str, indent=2))
        return

    if args.command == "backtest":
        run_backtest_cli(cfg, args)
        return

    ledger = Ledger(cfg["storage"]["db_path"])
    if args.command == "status":
        print(json.dumps({
            "cash": ledger.get("cash"),
            "shutdown": ledger.get("shutdown"),
            "positions": {s: dict(r) for s, r in ledger.positions().items()},
            "last_equity": (ledger.equity_curve() or [[None, None]])[-1],
        }, default=str, indent=2))
    else:
        sv = cfg["survival"]
        results = quarterly_results(ledger.equity_curve(), cfg["starting_capital"], date.today(),
                                    sv["fiscal_year_start_month"])
        for r in results:
            print(f"{r.label}: {r.start_equity:,.0f} -> {r.end_equity:,.0f} ({r.ret:+.2%})"
                  f"{'' if r.complete else ' [in progress]'}")
        print(survival_check(results, sv["min_quarter_return"], sv["max_consecutive_failed_quarters"])[1])


def run_backtest_cli(cfg: dict, args) -> None:
    from .backtest import run_backtest
    from .data.market import fetch_candles

    frames = {s: fetch_candles(s, period="10y") for s in cfg["watchlist"]}
    bench = fetch_candles(cfg["benchmark"], period="10y")["close"]
    llm = make_llm(cfg) if args.ai else None
    res = run_backtest(cfg, frames, args.start, args.end, args.every, llm, bench)
    print(json.dumps(res.summary(), indent=2))
    for q in res.quarters:
        print(f"{q.label}: {q.ret:+.2%}{'' if q.complete else ' [partial]'}")


if __name__ == "__main__":
    main()
