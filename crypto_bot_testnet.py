"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         CRYPTO TRADING BOT — BINANCE TESTNET LIVE EXECUTION                ║
║                                                                              ║
║  Strategies  : S5 MACD Momentum  +  S1 Liquidity Sweep Reversal             ║
║  Mode        : TESTNET (fake money, real BTC prices)                        ║
║  Settings    : SL=5×ATR  TP=12.5×ATR  R:R=2.5  Risk=1%/trade               ║
║                                                                              ║
║  USAGE                                                                       ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  Run bot         :  python crypto_bot_testnet.py                             ║
║  Run on ETHUSDT  :  python crypto_bot_testnet.py ETHUSDT                    ║
║  Press Ctrl+C to stop at any time                                            ║
║                                                                              ║
║  Logs saved to   :  testnet_trades_btcusdt.csv                               ║
║  State saved to  :  testnet_state_btcusdt.json  (survives restarts)          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, time, json, hmac, hashlib, warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

# ── TESTNET CREDENTIALS ───────────────────────────────────────────────────────
API_KEY    = "fyAj0uzEB1o0Is0L48kvdVB22euydieFvvOC4uJpGD0CFLKHLuA0wMnWD73ATGtT"
API_SECRET = "yXhAtopF5vh0nbQDd8Vkmw72OnDtdjZNfTYvHKiaut7nP5Owg8a45StEe79tJq8Y"

BASE_URL   = "https://testnet.binance.vision"   # ← testnet, not real Binance

# ── CONFIG ────────────────────────────────────────────────────────────────────
ATR_SL    = 5.0
RR        = 2.5
RISK_PCT  = 0.01       # 1% of equity per trade
SLIP      = 0.001      # 0.1% slippage estimate (testnet fills at market)
SESSION   = (6, 21)    # UTC hours to trade
MIN_VR    = 1.2
MIN_ADX   = 15
POLL_SECS = 300        # check every 5 minutes

MAX_CAPITAL = 1000.0   # ← hard cap: bot will only ever risk from this amount

HERE = Path(__file__).resolve().parent

# ── BINANCE TESTNET API HELPERS ───────────────────────────────────────────────
def _sign(params: dict) -> str:
    query = urlencode(params)
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def _headers():
    return {"X-MBX-APIKEY": API_KEY}

def get_account():
    params = {"timestamp": int(time.time() * 1000)}
    params["signature"] = _sign(params)
    r = requests.get(f"{BASE_URL}/api/v3/account", headers=_headers(), params=params, timeout=10)
    return r.json()

def get_price(symbol):
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
    return float(r.json()["price"])

def get_balance(asset="USDT"):
    acct = get_account()
    for b in acct.get("balances", []):
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0

def get_symbol_info(symbol):
    r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=10)
    for s in r.json()["symbols"]:
        if s["symbol"] == symbol:
            return s
    return None

def round_step(qty, step):
    """Round quantity down to allowed step size."""
    precision = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
    return round(float(int(qty / step)) * step, precision)

def place_market_order(symbol, side, quantity):
    """Place a market order. side = 'BUY' or 'SELL'."""
    params = {
        "symbol":    symbol,
        "side":      side,
        "type":      "MARKET",
        "quantity":  quantity,
        "timestamp": int(time.time() * 1000)
    }
    params["signature"] = _sign(params)
    r = requests.post(f"{BASE_URL}/api/v3/order", headers=_headers(), params=params, timeout=10)
    return r.json()

def place_oco_order(symbol, side, quantity, stop_price, limit_price, take_profit_price):
    """
    Place OCO (One-Cancels-Other) order for SL + TP together.
    side = 'SELL' for long exit, 'BUY' for short exit
    """
    params = {
        "symbol":               symbol,
        "side":                 side,
        "quantity":             quantity,
        "price":                f"{take_profit_price:.2f}",    # limit (TP)
        "stopPrice":            f"{stop_price:.2f}",           # stop trigger
        "stopLimitPrice":       f"{limit_price:.2f}",          # stop limit price (just below stop)
        "stopLimitTimeInForce": "GTC",
        "timestamp":            int(time.time() * 1000)
    }
    params["signature"] = _sign(params)
    r = requests.post(f"{BASE_URL}/api/v3/order/oco", headers=_headers(), params=params, timeout=10)
    return r.json()

def cancel_all_orders(symbol):
    params = {"symbol": symbol, "timestamp": int(time.time() * 1000)}
    params["signature"] = _sign(params)
    r = requests.delete(f"{BASE_URL}/api/v3/openOrders", headers=_headers(), params=params, timeout=10)
    return r.json()

def get_open_orders(symbol):
    params = {"symbol": symbol, "timestamp": int(time.time() * 1000)}
    params["signature"] = _sign(params)
    r = requests.get(f"{BASE_URL}/api/v3/openOrders", headers=_headers(), params=params, timeout=10)
    return r.json()

# ── DATA + INDICATORS ────────────────────────────────────────────────────────
def fetch_klines(symbol, interval='5m', days=3):
    url   = f"{BASE_URL}/api/v3/klines"
    end   = int(time.time() * 1000)
    start = end - days * 86_400 * 1000
    rows  = []
    while start < end:
        resp = requests.get(url, params={
            'symbol': symbol, 'interval': interval,
            'startTime': start, 'limit': 1000
        }, timeout=20)
        data = resp.json()
        if not data or isinstance(data, dict): break
        rows.extend(data)
        start = data[-1][0] + 1
        if len(data) < 1000: break
    if not rows:
        raise ValueError(f"No data for {symbol}")
    df = pd.DataFrame(rows, columns=[
        'ts','open','high','low','close','vol',
        'cts','quote_vol','trades','tbb','tbq','ign'
    ])
    df = df[['ts','open','high','low','close','vol','quote_vol','trades']].astype(float)
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df.set_index('ts', inplace=True)
    df.rename(columns={'vol':'volume'}, inplace=True)
    return df

def compute_indicators(d):
    d = d.copy()
    tr = np.maximum(d.high-d.low, np.maximum(abs(d.high-d.close.shift(1)), abs(d.low-d.close.shift(1))))
    d['atr'] = tr.ewm(14, adjust=False).mean()
    up, dn = d.high.diff(), -d.low.diff()
    pdm = np.where((up>dn)&(up>0), up, 0.)
    ndm = np.where((dn>up)&(dn>0), dn, 0.)
    pdi = pd.Series(pdm, index=d.index).ewm(14, adjust=False).mean() / d['atr'] * 100
    ndi = pd.Series(ndm, index=d.index).ewm(14, adjust=False).mean() / d['atr'] * 100
    dx  = abs(pdi-ndi) / (pdi+ndi+1e-9) * 100
    d['adx'] = dx.ewm(14, adjust=False).mean()
    for p in [9,21,50,200]: d[f'ema{p}'] = d.close.ewm(p, adjust=False).mean()
    dlt = d.close.diff()
    d['rsi'] = 100 - 100/(1 + dlt.clip(lower=0).ewm(14, adjust=False).mean()
                            / (-dlt.clip(upper=0)+1e-9).ewm(14, adjust=False).mean())
    d['macd']   = d.close.ewm(12, adjust=False).mean() - d.close.ewm(26, adjust=False).mean()
    d['macd_h'] = d['macd'] - d['macd'].ewm(9, adjust=False).mean()
    d['vs']  = d.volume.rolling(20).mean()
    d['vz']  = (d.volume - d['vs']) / (d.volume.rolling(20).std() + 1e-9)
    d['vr']  = d.volume / (d['vs'] + 1e-9)
    d['hi10'] = d.high.rolling(10).max().shift(1)
    d['lo10'] = d.low.rolling(10).min().shift(1)
    d['lwi']  = (np.minimum(d.close, d.open)-d.low) / (d.high-d.low+1e-9)
    d['uwi']  = (d.high-np.maximum(d.close, d.open)) / (d.high-d.low+1e-9)
    d['hour'] = d.index.hour
    d['ret3'] = d.close.pct_change(3) * 100
    d['quote_vol'] = d.get('quote_vol', d.volume * d.close)
    d['vwap'] = d.quote_vol.rolling(288).sum() / d.volume.rolling(288).sum()
    return d.dropna()

def add_trend(df5, df15):
    df15 = df15.copy()
    df15['trend'] = np.sign(df15['ema50'] - df15['ema50'].shift(3))
    df5 = df5.join(df15[['trend']].resample('5min').ffill(), how='left')
    df5['trend'] = df5['trend'].ffill().fillna(0)
    return df5

# ── SIGNAL LOGIC ──────────────────────────────────────────────────────────────
def check_signals(symbol):
    df5  = fetch_klines(symbol, '5m', days=3)
    df15 = df5.resample('15min').agg({
        'open':'first','high':'max','low':'min','close':'last',
        'volume':'sum','quote_vol':'sum','trades':'sum'
    }).dropna()
    df5  = compute_indicators(df5)
    df15 = compute_indicators(df15)
    df5  = add_trend(df5, df15)

    bar  = df5.iloc[-1]
    prev = df5.iloc[-2]

    # Gates
    in_session = SESSION[0] <= int(bar.hour) <= SESSION[1]
    vol_ok     = bar.vr >= MIN_VR
    atr_ok     = (bar.atr / bar.close) >= 0.001
    adx_ok     = bar.adx >= MIN_ADX

    signals = []

    # S5 — MACD Momentum
    if in_session and vol_ok and atr_ok and adx_ok:
        if prev.macd_h <= 0 and bar.macd_h > 0 and bar.close > bar.ema50 and bar.ret3 > 0.1 and bar.vz > 0.5:
            if bar.trend >= 0:
                signals.append(('S5', 1, bar))
        elif prev.macd_h >= 0 and bar.macd_h < 0 and bar.close < bar.ema50 and bar.ret3 < -0.1 and bar.vz > 0.5:
            if bar.trend <= 0:
                signals.append(('S5', -1, bar))

    # S1 — Liquidity Sweep
    if in_session and vol_ok and atr_ok and adx_ok:
        if bar.low < bar.lo10 and bar.close > bar.lo10 and bar.lwi > 0.35 and bar.vz > 1.5 and bar.rsi < 60:
            signals.append(('S1', 1, bar))
        elif bar.high > bar.hi10 and bar.close < bar.hi10 and bar.uwi > 0.35 and bar.vz > 1.5 and bar.rsi > 40:
            signals.append(('S1', -1, bar))

    return signals, bar

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def run(symbol='BTCUSDT'):
    csv_path   = HERE / f'testnet_trades_{symbol.lower()}.csv'
    state_path = HERE / f'testnet_state_{symbol.lower()}.json'

    # Load state
    if state_path.exists():
        state     = json.loads(state_path.read_text())
        in_trade  = state.get('in_trade', False)
        trade_info= state.get('trade_info', {})
        total     = state.get('total', 0)
        wins      = state.get('wins', 0)
    else:
        in_trade = False; trade_info = {}; total = 0; wins = 0

    def save_state():
        state_path.write_text(json.dumps({
            'in_trade': in_trade, 'trade_info': trade_info,
            'total': total, 'wins': wins
        }, indent=2))

    def log_trade(row):
        header = not csv_path.exists()
        with open(csv_path, 'a') as f:
            if header:
                f.write('time,symbol,strategy,direction,entry,sl,tp,exit,result,pnl_pct,balance_usdt\n')
            f.write(','.join(str(v) for v in row.values()) + '\n')

    # Get symbol precision info
    info      = get_symbol_info(symbol)
    lot_filter= next((f for f in info['filters'] if f['filterType']=='LOT_SIZE'), None)
    step_size = float(lot_filter['stepSize']) if lot_filter else 0.001
    min_qty   = float(lot_filter['minQty'])   if lot_filter else 0.001

    print(f"\n{'═'*66}")
    print(f"  BINANCE TESTNET BOT — {symbol}")
    print(f"  SL={ATR_SL}×ATR  TP={ATR_SL*RR:.0f}×ATR  R:R={RR}  Risk={RISK_PCT*100:.0f}%/trade")
    print(f"  Session: {SESSION[0]}:00–{SESSION[1]}:00 UTC  |  Poll: every {POLL_SECS}s")
    print(f"  Logs → {csv_path.name}")
    print(f"  Press Ctrl+C to stop")
    print(f"{'═'*66}\n")

    # Show starting balance
    try:
        bal = get_balance('USDT')
        print(f"  Testnet USDT balance : ${bal:,.2f}")
        print(f"  Working capital (cap): ${min(bal, MAX_CAPITAL):,.2f}")
        print(f"  Max risk per trade   : ${min(bal, MAX_CAPITAL) * RISK_PCT:.2f}\n")
    except Exception as e:
        print(f"  Could not fetch balance: {e}\n")

    while True:
        now = datetime.now(timezone.utc)
        ts  = now.strftime('%Y-%m-%d %H:%M UTC')
        print(f"[{ts}]", end='  ')

        try:
            price = get_price(symbol)
            bal   = get_balance('USDT')
            # Cap the working capital at MAX_CAPITAL
            working_bal = min(bal, MAX_CAPITAL)
            print(f"{symbol}=${price:,.2f}  balance=${bal:,.2f}  working=${working_bal:,.2f} (capped)", end='')

            # ── CHECK IF EXISTING OCO WAS HIT ─────────────────────────────
            if in_trade:
                open_orders = get_open_orders(symbol)
                direction   = trade_info['direction']
                entry_p     = trade_info['entry']
                sl_p        = trade_info['sl']
                tp_p        = trade_info['tp']
                qty         = trade_info['qty']
                strat       = trade_info['strategy']

                # If no open orders → OCO was filled (SL or TP hit)
                if len(open_orders) == 0:
                    # Determine result from current price vs entry
                    if direction == 1:
                        result = 'TP' if price >= (entry_p + tp_p) / 2 else 'SL'
                        exit_p = tp_p if result == 'TP' else sl_p
                        pnl    = (exit_p - entry_p) / entry_p * 100
                    else:
                        result = 'TP' if price <= (entry_p + tp_p) / 2 else 'SL'
                        exit_p = tp_p if result == 'TP' else sl_p
                        pnl    = (entry_p - exit_p) / entry_p * 100

                    total += 1
                    if pnl > 0: wins += 1
                    wr = wins / total * 100

                    print(f"\n  {'✅ TP HIT' if result=='TP' else '❌ SL HIT'}  "
                          f"{strat} {'LONG' if direction==1 else 'SHORT'}  "
                          f"entry={entry_p:.2f} → exit≈{exit_p:.2f}  "
                          f"P&L≈{pnl:+.2f}%  trades={total}  WR={wr:.1f}%")

                    log_trade({'time': ts, 'symbol': symbol, 'strategy': strat,
                        'direction': 'LONG' if direction==1 else 'SHORT',
                        'entry': round(entry_p,2), 'sl': round(sl_p,2), 'tp': round(tp_p,2),
                        'exit': round(exit_p,2), 'result': result,
                        'pnl_pct': round(pnl,3), 'balance_usdt': round(bal,2)})

                    in_trade = False; trade_info = {}
                    save_state()

                else:
                    dir_str = 'LONG' if direction==1 else 'SHORT'
                    pnl_now = ((price-entry_p)/entry_p*direction)*100
                    print(f"  IN {dir_str} {strat}  "
                          f"entry={entry_p:.2f}  SL={sl_p:.2f}  TP={tp_p:.2f}  "
                          f"now={pnl_now:+.2f}%  ({len(open_orders)} open orders)")

            # ── LOOK FOR NEW SIGNAL ────────────────────────────────────────
            if not in_trade:
                signals, bar = check_signals(symbol)

                if not signals:
                    print(f"  No signal  ADX={bar.adx:.1f}  VolZ={bar.vz:.2f}  "
                          f"Hour={int(bar.hour)}UTC")
                else:
                    strat, direction, bar = signals[0]
                    entry_p  = price * (1 + SLIP * direction)
                    sl_dist  = bar.atr * ATR_SL
                    sl_p     = entry_p - direction * sl_dist
                    tp_p     = entry_p + direction * sl_dist * RR
                    raw_qty  = (working_bal * RISK_PCT) / sl_dist
                    qty      = round_step(raw_qty, step_size)

                    # BTCUSDT testnet minimum viable quantity is 0.001 BTC
                    BTCUSDT_MIN_QTY = 0.001
                    if qty < BTCUSDT_MIN_QTY:
                        qty = BTCUSDT_MIN_QTY
                        print(f"     ℹ Qty set to minimum {BTCUSDT_MIN_QTY} BTC (${BTCUSDT_MIN_QTY*entry_p:.2f} notional)")

                    print(f"\n  🔔 SIGNAL  {strat}  {'LONG' if direction==1 else 'SHORT'}")
                    print(f"     Entry≈${entry_p:.2f}  SL=${sl_p:.2f}  TP=${tp_p:.2f}")
                    print(f"     ATR={bar.atr:.2f}  ADX={bar.adx:.1f}  Qty={qty}")

                    if qty < min_qty:
                        print(f"     ⚠ Qty {qty} below min {min_qty} — skipping (balance too low?)")
                    else:
                        # Place market entry
                        side = 'BUY' if direction == 1 else 'SELL'
                        order = place_market_order(symbol, side, qty)
                        print(f"     ✅ Market {side} placed: {order.get('orderId','?')}")

                        if 'orderId' in order:
                            # Place OCO for exit
                            exit_side  = 'SELL' if direction == 1 else 'BUY'
                            stop_limit = sl_p * (0.999 if direction == 1 else 1.001)

                            try:
                                oco = place_oco_order(symbol, exit_side, qty, sl_p, stop_limit, tp_p)
                                print(f"     ✅ OCO placed (SL=${sl_p:.2f} / TP=${tp_p:.2f})")
                            except Exception as oe:
                                print(f"     ⚠ OCO failed: {oe} — manual SL/TP needed")

                            in_trade  = True
                            trade_info= {'strategy': strat, 'direction': direction,
                                        'entry': entry_p, 'sl': sl_p, 'tp': tp_p,
                                        'qty': qty}
                            save_state()

        except KeyboardInterrupt:
            print(f"\n\nStopped by user.")
            print(f"Total trades: {total}  Wins: {wins}  WR: {wins/max(total,1)*100:.1f}%")
            print(f"Trade log: {csv_path}")
            save_state()
            return
        except Exception as e:
            print(f"\n  ⚠ Error: {e}")

        save_state()
        print()

        # Wait for next 5-min candle boundary
        secs_left = POLL_SECS - (int(time.time()) % POLL_SECS)
        print(f"  Next check in {secs_left}s  (Ctrl+C to stop)\n")
        try:
            time.sleep(secs_left)
        except KeyboardInterrupt:
            print(f"\nStopped. Trades: {total}  WR: {wins/max(total,1)*100:.1f}%")
            save_state()
            return

# ── ENTRY ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    symbol = 'BTCUSDT'
    for arg in sys.argv[1:]:
        if arg.upper().endswith('USDT'):
            symbol = arg.upper()
        else:
            symbol = arg.upper() + 'USDT'
    run(symbol)
