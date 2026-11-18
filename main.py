#!/usr/bin/env python3
"""BTC race bot v6: oversold bounce + Chandelier (volatility) trailing exit.

Entry for make.fun btc-race-24h-20261118-1444. Dev window: 1,440 bars,
downtrend -2.80% ($65,083.31 -> $63,260.75) — a washout-bounce window,
which is exactly the regime v6 was designed for.

Long only, one position, full size. Strategy (see STRATEGY.md §9):

  - ENTRY (validated, see §8.1/§8.3): arm only when the close is at
    least 2% below the highest close of the last 240 bars (a genuine
    washout, ~8x the ~26 bps round-trip cost), then enter on a close
    above the highest close of the last 60 bars (1-hour Donchian
    breakout, confirming the turn at the START of the recovery). v5's
    trade 4 proved the entry: it entered the window-3 h17 bounce at
    $63,364, only 0.3% above the breakout.
  - EXIT (CHANGED from v5): Chandelier trailing exit — sell when the
    close falls 3x ATR(60) below the highest close since entry. The
    ATR is computed LIVE each bar from the last 60 one-minute bars
    (true range), so the trail scales with the window's current
    volatility instead of being a fixed number.
  - NO trend-break backstop (same reason as v5): price sits BELOW
    EMA(240) at entry by construction, so that backstop would fire
    immediately. NO take-profit: any TP level would be fitted to the
    data (same argument as v4 §7.3).

Why the exit changes, from the diagnosis (STRATEGY.md §9.1) — the
failure was in the exit, not the entry. v5's fixed 0.3% trail was set
from the COST (~26 bps round trip), but window 3's 1-minute noise is
12 bps stdev: a 30 bps pullback is only ~2.5x the noise, and in a
random 30-bar window the median max pullback from the running peak
is 53 bps (86% of windows pull back >= 30 bps). So the fixed trail
fires on normal bounce chop. It cut v5's trade 4 nine bars after
entry at +0.49% while the bounce ran on to +3.66%; the bounce's own
largest intra-rise pullback was 47 bps. The Chandelier exit is the
literature answer (LeBeau): place the stop 3 ATRs from the extreme,
because a pullback of up to ~2x ATR is normal chop inside a move
and only a move beyond ~3x ATR is a genuine reversal. At the
window-3 bounce, 3x ATR(60) was 72 bps — wide enough to survive the
bounce's own 47 bps chop, tight enough to exit on a real fade.
The multiple (3x) and the window (60 bars, 1 hour — the same scale
as the entry breakout, so the trail reads the same regime the entry
reads) come from the literature, NOT from testing trail widths
against the dev window: no trail width was gate-checked before this
design was fixed.

Trade-off, stated before testing: a wider trail also lets FAILED
bounces run longer before exiting (v5's trades 2/3, whose intra-hold
pullbacks were 42/51 bps, would exit later and lose more than
v5's -0.37%/-0.48%). The design bets the winner's held gain
(a bounce ridden near its peak instead of cut at +0.49%) outweighs
the losers' extra bleed. That is a structural bet, judged by the
single gate check below — not by tuning the multiple.

v6 is the only version that has ever passed the dev gate: race
20261115-0828, +$14.35 net PnL, submitted 15 Nov 2026.

Pure function of bar closes/highs/lows, integer math only:
deterministic, no clock, no network, no randomness, no files.
Speaks agent-fun/bars-replay/v1 via the vendored shim
(vendor/makefun_bars.py, byte-identical to the kit SDK).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

# --- Parameters (research-chosen; see STRATEGY.md §9) ------------------------
OVERSOLD_BARS = 240     # oversold lookback: 4 h (unchanged from v5)
OVERSOLD_DD_BPS = 200   # arm only when >= 2% below the 240-bar high
                        # (unchanged from v5)
BREAKOUT_BARS = 60      # entry trigger: 1-hour Donchian high (unchanged)
ATR_BARS = 60           # volatility lookback: 1 hour of 1-min bars —
                        # same scale as the breakout, so the trail reads
                        # the same regime the entry reads
ATR_MULT = 3            # Chandelier standard: 3x ATR (LeBeau). A
                        # pullback up to ~2x ATR is normal chop inside
                        # a move; beyond ~3x ATR is a genuine reversal.
MAX_SLIPPAGE_BPS = 100  # 4x the expected ~26 bps cost; caps gap fills
BPS = 10_000

SYMBOL = "CBBTC"

# --- State ------------------------------------------------------------------
closes = []             # close prices (ints, price_scale 5), in batch order
highs = []              # high prices, same order
lows = []               # low prices, same order
entry_price = None      # fill price of the open position (int), or None
entry_seq = -1          # seq of the batch whose fills opened the position
peak_close = None       # highest close seen while holding (int), or None
pending_buy = False     # a buy order is decided but not yet filled/rejected


def atr_now(n):
    """ATR over the last ATR_BARS bars ending at index n-1 (price units).

    True range per bar = max(h-l, |h-prev_close|, |l-prev_close|).
    Integer math only; n must be > ATR_BARS.
    """
    tr_sum = 0
    for k in range(n - ATR_BARS, n):
        h, l, pc = highs[k], lows[k], closes[k - 1]
        tr = h - l
        if h - pc > tr:
            tr = h - pc
        if pc - l > tr:
            tr = pc - l
        tr_sum += tr
    return tr_sum // ATR_BARS


def on_batch(state, batch):
    global entry_price, entry_seq, peak_close, pending_buy

    # --- Fold in this batch's fills first: they settle last batch's orders.
    for fill in batch.get("fills", []):
        if fill.get("symbol") != SYMBOL:
            continue
        if fill.get("side") == "buy":
            pending_buy = False
            if fill.get("status") == "filled":
                entry_price = int(fill["price"])
                entry_seq = state.seq
                peak_close = None
        elif fill.get("side") == "sell" and fill.get("status") == "filled":
            entry_price = None
            entry_seq = -1
            peak_close = None

    bar = state.latest.get(SYMBOL)
    if bar is None or state.warming_up:
        return []
    close = int(bar["c"])
    closes.append(close)
    highs.append(int(bar["h"]))
    lows.append(int(bar["l"]))

    held = state.positions.get(SYMBOL)
    n = len(closes)

    # --- Exit: holding a position -------------------------------------------
    if held is not None and held.qty > 0:
        if peak_close is None or close > peak_close:
            peak_close = close
        # Chandelier exit: sell when the close falls ATR_MULT x ATR(60)
        # below the highest close since entry. The trail is computed
        # live each bar, so it widens as the bounce's volatility
        # expands and tightens as it calms. The comparison uses closes
        # only (never the bar high): the entry trigger is a close
        # breakout, so the exit must read the same series, or a
        # one-minute spike high would move the trail without a close
        # ever confirming it. No EMA backstop (price is below EMA(240)
        # at entry by construction) and no take-profit (any TP level
        # would be fitted to the data).
        if n > ATR_BARS:
            trail = ATR_MULT * atr_now(n)
            if close < peak_close - trail:
                return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # --- Entry: flat (unchanged from v5) ------------------------------------
    # Need a full 240-bar oversold window (covers the 60-bar breakout
    # window and the 60-bar ATR window too).
    if pending_buy or n <= OVERSOLD_BARS:
        return []
    # Arm (oversold): close at least 2% below the highest close of the
    # last 240 bars — a genuine washout, not a 1-hour wobble.
    hi240 = max(closes[-OVERSOLD_BARS - 1:-1])
    drawdown_bps = (close - hi240) * BPS // hi240
    # Trigger (breakout): close above the highest close of the last
    # 60 bars — the turn is confirmed, at the start of the recovery.
    breakout = close > max(closes[-BREAKOUT_BARS - 1:-1])
    if drawdown_bps <= -OVERSOLD_DD_BPS and breakout:
        cash = state.cash
        if cash <= 0:
            return []
        pending_buy = True
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": cash,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    return []


makefun_bars.run(on_batch)
