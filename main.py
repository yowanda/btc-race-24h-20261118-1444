#!/usr/bin/env python3
"""BTC race bot v4: Elder Triple Screen — 720-min tide + 240-min wave + 60-bar breakout.

Entry for make.fun btc-race-24h-20261118-1444. Dev window: 1,440 bars,
downtrend -2.80% ($65,083.31 -> $63,260.75). Gate checks on this window:
v6 +$0.00 / 0 trades (FAILED gate); v5 +$0.00 / 0 trades (FAILED gate).
v4 is now being gate-checked on the same window, per the v6 -> v5 -> v4
rotation (STRATEGY.md §7/§8/§9) — v4 is the last candidate for this race.

Long only, one position, full size. Strategy (see STRATEGY.md §7):

  - Screen 1, TIDE (12 h, NEW in v4): only consider longs when the
    720-min return is positive AND price is above EMA(720). This is the
    structural fix for the v3 failure mode on the -4.91% window: v3's
    240-min gate flipped positive on every 1-hour dead-cat bounce
    (ret240 was +57/+123/+65/+10 bps at its four entries), and the
    Donchian breakout then fired at the top of each bounce — two of the
    four round trips never rose above their entry at all. A 12-hour
    return can only be positive if the bounce retraced the entire
    12-hour drop, i.e. a genuine reversal, not a bounce. Measured on
    that window, the 720-min gate was open 0/720 bars (the 240-min gate
    was open 20.7% of the time). Elder's rule, verbatim in spirit:
    screen one is a strict filter, no exceptions — never long a
    downtrend tide, however favourable the waves look.
  - Screen 2, WAVE (4 h, promoted from v3's regime gate): 240-min
    return positive AND price above EMA(240). v3's 120-min trend filter
    is dropped: the middle screen moves to 240 min so the three screens
    are evenly spaced (60 -> 240 -> 720, ~4x/3x, Elder spacing), and a
    240-min condition already captures what the 120-min condition did
    (medium-term trend up) at a timescale a 1-hour bounce cannot flip.
  - Screen 3, RIPPLE (1 h, unchanged from v3): close breaks above the
    highest close of the last 60 bars (Donchian breakout).
  - Exit (trailing stop, unchanged from v3): sell when the close falls
    0.3% below the highest close since entry (~the round-trip cost).
    PLUS a structural backstop (NEW in v4): sell if the close falls
    below EMA(240) — the medium-term trend that justified the entry is
    gone, so the premise is gone. The trail usually fires first; the
    backstop covers slow rollovers where the price drifts under the
    trend without a sharp 0.3% pullback from the peak.

Trade-off, stated honestly: the 720-min return needs 720 bars of
history, so the bot cannot trade the first 12 hours of a 24-hour
window. That is the price of the tide filter. It is acceptable because
(a) trends persist (Moskowitz et al.) — the second half of a genuine
uptrend day still has up-moves to catch, and (b) the bot only needs
ONE good round trip to pass the dev gate; on a downtrend day the
correct answer is zero trades ($0, no bleed) rather than four losing
bounces (-$10.57).

Pure function of bar closes, integer math only: deterministic, no clock,
no network, no randomness, no files. Speaks agent-fun/bars-replay/v1 via
the vendored shim (vendor/makefun_bars.py, byte-identical to the kit SDK).
"""

# Version: v4 (Elder Triple Screen 720) — gate rotation for race btc-race-24h-20261118-1444.
# v6 gate on this window: +$0.00 / 0 trades (FAILED). v5 gate: +$0.00 / 0 trades (FAILED).
# v4 is the last candidate; strategy parameters/logic unchanged from
# main-v4-triple-screen-720.py (no tuning).

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

# --- Parameters (research-chosen; see STRATEGY.md §7) ------------------------
TIDE_BARS = 720         # Screen 1 tide lookback: 12 h (3x the wave)
TIDE_EMA_SPAN = 720     # EMA span for the tide gate
TREND_BARS = 240        # Screen 2 wave lookback: 4 h (4x the breakout)
EMA_SPAN = 240          # EMA span for the wave filter / exit backstop
BREAKOUT_BARS = 60      # Screen 3 ripple: 1-hour Donchian high (minutes)
TRAIL_BPS = 30          # trailing stop: 0.3% below the peak since entry
MAX_SLIPPAGE_BPS = 100  # 4x the expected ~26 bps cost; caps gap fills
BPS = 10_000
EMA_FP = 1_000_000      # fixed-point scale for the integer EMA

SYMBOL = "CBBTC"

# --- State ------------------------------------------------------------------
closes = []             # close prices (ints, price_scale 5), in batch order
ema_fp = None           # EMA(240) in fixed point (price * EMA_FP)
ema_tide_fp = None      # EMA(720) in fixed point (price * EMA_FP)
entry_price = None      # fill price of the open position (int), or None
entry_seq = -1          # seq of the batch whose fills opened the position
peak_close = None       # highest close seen while holding (int), or None
pending_buy = False     # a buy order is decided but not yet filled/rejected


def ret_bps(now, then):
    """Return from `then` to `now` in basis points (integer math)."""
    return (now - then) * BPS // then


def on_batch(state, batch):
    global ema_fp, ema_tide_fp, entry_price, entry_seq, peak_close, pending_buy

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

    # Integer EMAs: seeded on the first close, alpha = 2 / (span + 1).
    if ema_fp is None:
        ema_fp = close * EMA_FP
    else:
        ema_fp += (close * EMA_FP - ema_fp) * 2 // (EMA_SPAN + 1)
    if ema_tide_fp is None:
        ema_tide_fp = close * EMA_FP
    else:
        ema_tide_fp += (close * EMA_FP - ema_tide_fp) * 2 // (TIDE_EMA_SPAN + 1)

    held = state.positions.get(SYMBOL)
    n = len(closes)

    # --- Exit: holding a position -------------------------------------------
    if held is not None and held.qty > 0:
        if peak_close is None or close > peak_close:
            peak_close = close
        # Trailing stop: 0.3% below the highest close since entry.
        if peak_close is not None and close * BPS < peak_close * (BPS - TRAIL_BPS):
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        # Trend-break backstop: the 4-hour trend that justified the entry
        # is gone (close below EMA(240)) — premise gone, exit.
        if close * EMA_FP < ema_fp:
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # --- Entry: flat ---------------------------------------------------------
    # Need a full 720-bar tide window (covers the 240-bar wave and the
    # 60-bar breakout windows too).
    if pending_buy or n <= TIDE_BARS:
        return []
    # Screen 1 (tide, 12 h): the day-scale trend itself must be up.
    ret_tide = ret_bps(close, closes[-TIDE_BARS - 1])
    above_tide_ema = close * EMA_FP > ema_tide_fp
    # Screen 2 (wave, 4 h): the medium-term trend must be up.
    ret_trend = ret_bps(close, closes[-TREND_BARS - 1])
    above_ema = close * EMA_FP > ema_fp
    # Screen 3 (ripple, 1 h): breakout above the 1-hour high.
    breakout = close > max(closes[-BREAKOUT_BARS - 1:-1])
    if (ret_tide > 0 and above_tide_ema
            and ret_trend > 0 and above_ema and breakout):
        cash = state.cash
        if cash <= 0:
            return []
        pending_buy = True
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": cash,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    return []


makefun_bars.run(on_batch)
