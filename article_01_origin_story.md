# What Building a Bitcoin Trading Bot Actually Teaches You

Most people think the hard part of algorithmic trading is the algorithm. It isn't. The hard part is finding out whether you have an edge at all — and most ways of looking for one will give you the wrong answer.

I found this out the slow way.

---

## The Starting Point

I want to buy a motorcycle. That is the honest reason this exists.

Not passive income, not financial freedom, not retiring early. A bike. The kind you sit on and go vroom vroom and suddenly every Tuesday feels like it has a point.

To buy the bike I needed money I did not have. So I started thinking about how to make it. My background is US sales tax compliance — two years of multi-state registrations, notice handling, escalation management. Useful work. Steady work. Not the kind of work that gets you a motorcycle any faster than saving $200 a month does.

I had a Python file on my computer. A trading strategy system someone had built for Indian equity markets — NIFTY, BANKNIFTY, Zerodha Kite API. I had been sitting on it for months, mostly ignoring it. The bike changed the calculus. I opened it.

That decision is where things get interesting.

---

## The Wrong Foundation

The file was sophisticated. A 12-factor confluence system. It checked trend direction, pullback quality, VWAP alignment, candlestick patterns, volume, hidden divergence, supply and demand zones. Trades only triggered when at least four of those factors agreed. For Indian equity indices, it was apparently well-designed.

For Bitcoin, it was wrong in almost every way that mattered.

The main data function, `_fetch_hist()`, was a placeholder. It raised `NotImplementedError`. The entire trading logic called it constantly. There was also `gap_open_ok()` — called throughout the signal code, never defined. And `load_token()`. Same issue.

Beyond the missing pieces, the logic itself was built for a market open at 9:30am IST that closes at 3:30pm, pauses for lunch, and expires options on specific weekdays. BTC trades continuously. It does not care about Indian Standard Time.

The position sizing used `LOT_SIZE_OPT = 65` — a NIFTY options lot size, hardcoded, completely meaningless for fractional Bitcoin trading. The round number filter was calibrated to 500-point increments, appropriate for an index at 22,000 points, not for BTC at $77,000.

Everything printed in ₹.

Looking at this list now, the obvious move was to start over. At the time, I kept going. In retrospect I think the right instinct was there, buried under a lot of wrong ones: the framework was worth preserving. The implementation needed to be rebuilt.

---

## What Got Built

Over several sessions the system was reconstructed for Bitcoin on Binance. The 12-factor confluence concept stayed. Everything else changed.

The final signal engine evaluates these factors on every candle:

| Factor | What It Checks |
|---|---|
| TREND | Higher timeframe directional bias |
| PB_WEAK | Momentum fading into support/resistance |
| WVWAP | Weekly VWAP alignment |
| KEY_LVL | Key price level confluence |
| IMB (p1–p4) | Order imbalance at four levels |
| VOL | Volume via Z-score vs recent average |
| MARUBOZU | Candlestick body confirmation |
| DBL_POI | Double point of interest |
| HDIV | Hidden RSI divergence |
| IC | Internal confluence |
| ROUND_NO | Round number levels ($1,000 increments for BTC) |
| SD_ZONE | Supply/demand zone alignment |

Minimum score to trigger: 5 out of 12. London and New York sessions require 5. Asian session requires 6, because liquidity is thinner and false signals are more common.

Risk parameters: stop loss at 5× ATR(14), take profit at 12.5× ATR(14), 1% of working capital at risk per trade. Trading hours 06:00–21:00 UTC. ADX must be above 15 — the bot skips sideways markets entirely.

The break-even win rate with a 2.5:1 reward-to-risk ratio works out to 28.6%. That number matters. Everything else gets evaluated against it.

---

## The Bug That Would Have Fooled Me

The most consequential bug was not in the trading logic. It was in the backtest engine.

The original code had a weekday filter — a sensible feature for equity markets that close on weekends. The filter had never been removed during the adaptation. So when the backtest ran on BTC data, it quietly skipped every Saturday and Sunday.

Bitcoin trades 24/7. A January-to-March run covers about 90 days. With weekends excluded, roughly 26 of those days simply vanished from the analysis. That is more than 28% of all trading days, gone, with no error message, no warning, no indication anything was wrong.

The backtest would have completed. The statistics would have looked plausible. The conclusions would have been wrong.

The reason this kind of bug is dangerous is not that it crashes your program. It is that it doesn't. It silently produces results that feel trustworthy because they are formatted correctly and the numbers are in reasonable ranges. You would never know unless you specifically went looking for it.

This one was caught. Which raises the question of how many like it weren't.

---

## The Results That Should Make You Nervous

After the bugs were fixed, a full one-year backtest ran on BTC/USDT 5-minute bars. Two strategies tested simultaneously.

**S5 — MACD Zero-Cross:**
181 trades. 32.6% win rate. +15.4% annual return. Sharpe ratio: **15.16**.

**S1 — Liquidity Sweep Reversal:**
149 trades. 32.2% win rate. +10.4% annual return. Sharpe ratio: **13.05**.

Those Sharpe ratios are the problem.

A Sharpe above 1.0 is considered good. Above 2.0 is exceptional. Most hedge funds — the ones managing billions, with teams of quants — operate somewhere between 1.0 and 2.0 in live trading. A retail algo on intraday crypto posting 15.16 is not a green flag. It is a red one.

The most likely explanation: the backtest period was April 2023 to April 2024. Bitcoin went from roughly $28,000 to $65,000 during that window. A 130% bull run. The long-side signals caught that tailwind. In a different regime, they might not.

Then the synthetic stress test confirmed it. The strategies were run on artificially generated BTC-like price data — same volatility, same fat-tailed distribution, different path. Win rates fell below break-even. S5 returned -4.8% annually. S1 returned -12.0%.

In other words: the edge appears to exist in the data we tested on, and disappears when the path changes. That is the definition of curve-fitting.

The profit factors, 1.13× and 1.11×, are thin. They mean 11 to 13 cents of profit for every dollar lost. One bad month can erase several good ones.

The backtest says this might work. The synthetic test says be very careful about believing the backtest.

---

## Going Live on AWS

The bot was deployed to an AWS EC2 instance — a small Ubuntu server, always on, SSH accessible, running as a background process. The working capital was capped at $1,000 (testnet, fake money) to validate the signal logic without any real financial exposure.

The first signal fired within hours.

The position size calculated to 0.018 BTC. The Binance testnet minimum lot size for BTCUSDT is 0.1 BTC. The code rounded 0.018 to the nearest valid step, got zero, and skipped the trade.

The fix was a minimum quantity fallback: if the calculated size rounds to zero but the signal is valid, use the minimum executable size instead of skipping. On testnet with fake money, this is the right call. The signal logic is what needs validation — not the position sizing math, which will be recalculated when moving to real capital with a properly sized account.

After that fix, and one more bug where the step size parser was returning `1e-05` instead of `0.001`, the orders started going through.

---

## The First Trade

May 1, 2026. 17:50 UTC.

S1 Liquidity Sweep signal. LONG. Entry at $77,115.39. Stop loss at $76,402.69. Take profit at $78,897.15.

BTC moved up. The take profit hit.

Result: WIN. +2.311%. Profit in USD: **$1.78**.

That is not a lot of money. It is not supposed to be. The testnet position size is the minimum executable lot — 0.001 BTC, worth about $77 at entry. The point was never to make money in the first week. The point was to confirm that the signal fires correctly, the order gets placed, and the OCO (simultaneous stop loss and take profit) works as intended.

All three did.

The trade log on the server:

```
2026-05-01 17:50 UTC, BTCUSDT, S1, LONG, 77115.39, 76402.69, 78897.15, 78897.15, TP, 2.311, 88316.92
```

That is the entire live track record. One line.

---

## What One Trade Proves

Almost nothing, statistically. A 100% win rate from a single trade is meaningless. You need 30 minimum, preferably 50, before the win rate numbers carry any real weight.

But here is what that one trade did prove: the system works end to end. Signal detection, order placement, stop loss, take profit, trade logging. Every piece functioned. That is more than it sounds like if you have ever tried to get multiple API systems to talk to each other correctly on the first real attempt.

The next milestone is 30 trades. If the live win rate comes in at or above 28.6% — the mathematical break-even — there is evidence of a real edge. Not proof. Evidence.

If it comes in below 28.6%, the synthetic stress test was right and the backtest was flattering. That finding will be published in full, with the same detail as this one.

The strategy either works or it doesn't. The only way to find out is to keep running it and watch what happens.

That is the experiment. The lab is open.

---

*Every trade logged here is real testnet data. Every number is exact. The next report publishes after 30 completed trades — whatever the results show.*

**Next:** Month 2 Paper Trade Report — Does the live win rate hold above the 28.6% break-even threshold?

---

**SEO Title:** What Building a Bitcoin Trading Bot Actually Teaches You (Real Data, Real Bugs, First Trade)
**Meta Description:** The honest origin story of Profit Pilot Lab — a 12-factor confluence bot, a Sharpe ratio that should make you nervous, a $1.78 first profit, and what one trade actually proves.
**Tags:** algo trading, bitcoin bot, backtest, build in public, quantitative trading
