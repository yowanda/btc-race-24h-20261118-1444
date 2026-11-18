#!/usr/bin/env python3
"""BTC race bot v5: oversold bounce — deep 4-hour drawdown + 60-bar breakout.

Entry for make.fun btc-race-24h-20261118-1444. Dev window: 1,440 bars,
downtrend -2.80% ($65,083.31 -> $63,260.75) — a washout-bounce window.
Gate check v6 on this window: +$0.00 / 0 trades (FAILED gate); v5 is
now being gate-checked on the same window, per the v6 -> v5 -> v4
rotation (STRATEGY.md §8/§9).

Long only, one position, full size. Strategy (see STRATEGY.md §8):

  - ARM (oversold, NEW in v5): only consider longs when the close is at
    least 2% below the highest close of the last 240 bars (4 h). The
    decline must be an order of magnitude larger than the ~26 bps
    round-trip cost, so the snap-back that follows has room to pay.
    This is the INVERSE of v4's tide gate: v4 waits for the 12-hour
    trend to turn up (and therefore never trades a downtrend day); v5
    waits for the washout and trades the snap-back that follows it.
  - TRIGGER (unchanged from v3/v4): close breaks above the highest
    close of the last 60 bars (1-hour Donchian breakout). The breakout
    confirms the turn — it fires at the START of the recovery, not 30
    minutes into it (v1's momentum trigger) and not after a 4-hour
    return has already turned positive (v3's gate, which opened 24
    minutes after the window-3 bounce's breakout and filled 2.2%
    above the breakout price).
  - EXIT (trailing stop, unchanged from v3/v4): sell when the close
    falls 0.3% below the highest close since entry (~the round-trip
    cost). The trail locks the bounce's gain as it develops and cuts
    promptly when the bounce fades.
  - NO trend-break backstop (deliberate difference from v4): v4's
    backstop sells when the close falls below EMA(240), but v5's entry
    is justified by the washout low, not by a 4-hour uptrend — price
    sits BELOW EMA(240) at entry by construction, so that backstop
    would fire immediately. NO take-profit either: any TP level that
    "catches" these bounces would be chosen from the data itself —
    that is fitting, and the rule forbids it (same argument as v4 §7.3).

Why this structure, from the diagnosis (STRATEGY.md §8.1): on the
three dev windows, every version that bought bounces INSIDE a
downtrend lost a little per round trip (v3: -$2.32/-$10.57/-$6.34),
while the version that waited for an uptrend never traded at all
(v4: $0.00, 0 trades on both downtrend windows). The structural
hole is the bounce itself: the one move a long-only bot CAN catch on
a downtrend day. The h17 bounce of window 3 ran +5.47% from its low
to its peak (+2.87% in one hour) — the day's single large up-move —
and it came after a deep washout (drawdown -2.06% from the 240-bar
high at the breakout bar). v3 missed it because its gate needed a
positive 4-hour return first; v5 arms on the washout itself.

Measured arming frequency (diagnosis only, §8.1): the arm fires
0-3 times per 24 h across the three windows (window 1: 0; window 2:
1; window 3: 3) — rare, as the research requires (a few trades per
day at most). Every fire followed a deep decline and a breakout
confirming the turn.

Trade-off, stated before testing: on a window with no washout +
breakout (like window 1), v5 takes zero trades and fails the dev
gate the same way v4 does ($0, no bleed). v4 and v5 are
complementary: v4 for uptrend windows, v5 for downtrend windows
with a washout bounce. The bot only needs ONE good round trip to
pass the dev gate.

Pure function of bar closes, integer math only: deterministic, no
clock, no network, no randomness, no files. Speaks
agent-fun/bars-replay/v1 via the vendored shim (vendor/makefun_bars.py,
byte-identical to the kit SDK).
"""

# Version: v5 (oversold bounce) — gate rotation for race btc-race-24h-20261118-1444.
# v6 gate on this window: +$0.00 / 0 trades (FAILED). v5 now under gate check.
# Strategy parameters/logic unchanged from main-v5-oversold-bounce.py (no tuning).

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

# --- Parameters (research-chosen; see STRATEGY.md §8) ------------------------
OVERSOLD_BARS = 240     # oversold lookback: 4 h (4x the breakout window,
                        # same middle scale as v3/v4's 240-min screen)
OVERSOLD_DD_BPS = 200   # arm only when >= 2% below the 240-bar high:
                        # ~8x the ~26 bps round-trip cost, ~1/4 of a
                        # volatile day's range — a genuine washout, not noise
BREAKOUT_BARS = 60      # entry trigger: 1-hour Donchian high (minutes)
TRAIL_BPS = 30          # trailing stop: 0.3% below the peak since entry
MAX_SLIPPAGE_BPS = 100  # 4x the expected ~26 bps cost; caps gap fills
BPS = 10_000

SYMBOL = "CBBTC"

# --- State ------------------------------------------------------------------
closes = []             # close prices (ints, price_scale 5), in batch order
entry_price = None      # fill price of the open position (int), or None
entry_seq = -1          # seq of the batch whose fills opened the position
peak_close = None       # highest close seen while holding (int), or None
pending_buy = False     # a buy order is decided but not yet filled/rejected


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

    held = state.positions.get(SYMBOL)
    n = len(closes)

    # --- Exit: holding a position -------------------------------------------
    if held is not None and held.qty > 0:
        if peak_close is None or close > peak_close:
            peak_close = close
        # Trailing stop: 0.3% below the highest close since entry.
        # No EMA backstop: price is below EMA(240) at entry by
        # construction (deep decline), so that backstop would fire at
        # once. No take-profit: any TP level would be fitted to the data.
        if peak_close is not None and close * BPS < peak_close * (BPS - TRAIL_BPS):
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # --- Entry: flat ---------------------------------------------------------
    # Need a full 240-bar oversold window (covers the 60-bar breakout
    # window too).
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
