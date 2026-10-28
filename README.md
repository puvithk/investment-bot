# investment-bot

A bot that is given a fixed amount of money and has to keep itself alive by investing in Indian stocks (NSE).
It is judged every quarter of the Indian financial year (Q1 = Apr–Jun). After **2 losing quarters in a row**, it sells everything and shuts itself down.

## How a decision is made

```
candles + indicators + candlestick patterns ─┐
news headlines (Google News, last 7 days) ───┼─> analysts (AI or rules) ─> portfolio manager ─> risk rules ─> broker
fundamentals (P/E, ROE, debt, growth) ───────┘                              proposes             can veto      paper / OpenAlgo
```

- **Analysts** (`invbot/agents.py`): technical, news and fundamentals. Each one returns a stance from -1 to +1 and a short reason. They use an AI model when one is configured (Grok, Groq or OpenRouter) and simple rules when none is.
- **Portfolio manager**: weighs the analysts' views and proposes BUY, SELL or HOLD, with a confidence, a size and a stop-loss.
- **Risk rules** (`invbot/risk.py`): fixed code that the AI can't override. Defaults: at most 15% of equity in one stock, 6 open positions, 10% kept in cash, a stop-loss on every buy (10% at most), no new buys once the quarter is down 8%, and 4 trades a day.
- **Ledger** (`invbot.db`, SQLite): every proposal is saved with the analysts' views, the indicator snapshot and the risk verdict, along with every fill, its charges and the daily equity.
- **Costs** (`invbot/costs.py`): real delivery charges (STT, stamp duty, exchange, SEBI, GST, DP). Check `config.yaml` against your broker's rate card.

## Run it

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml      # edit watchlist, capital, risk limits
cp .env.example .env                    # add XAI_API_KEY (or GROQ_API_KEY); never commit .env

python -m pytest -q                     # tests
python -m invbot backtest --start 2024-04-01            # rules-only replay of past NSE data
python -m invbot backtest --start 2025-04-01 --ai       # same with the AI (uses API calls)
python -m invbot run-once               # one live cycle with fake money (broker.mode: paper)
python -m invbot status
python -m invbot scorecard
```

Run `run-once` once per trading day after the close, using cron or Cloud Scheduler on GCP.

## Going live (later, deliberately)

1. Self-host [OpenAlgo](https://github.com/marketcalls/openalgo) on the GCP machine, which needs a static IP to meet SEBI's retail algo rules, and connect your broker (Dhan, Upstox, Zerodha, …).
2. Put `OPENALGO_API_KEY` in GCP Secret Manager. Use a broker key that **can't withdraw funds**.
3. Set `broker.mode: openalgo` and start with a small `starting_capital`.

## Known limits

- Backtests use charts only, because free point-in-time news and fundamentals aren't available.
- An AI model may already know how past prices moved, so AI backtests look better than reality. The fake-money run is the real test.
- Daily candles mean stop-losses are checked at the close, not intraday.
