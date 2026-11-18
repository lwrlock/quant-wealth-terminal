"""
Multi-Asset Asymmetric Alpha & Anti-Loss Scanner (`alpha_hunter.py`)

Scans liquid BIST-30 blue chips, Global Tech/Index/Gold ETFs, and major crypto pairs
using strict no-lookahead walk-forward backtesting (5Y daily bars, slippage + ATR stops).
Only surfaces live opportunities whose exact historical setup passes the edge gate
(Win Rate >= 35%, Profit Factor >= 1.35, Positive Expectancy).
"""

import datetime
import json
import math
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import yfinance as yf
except Exception:
    yf = None  # type: ignore[assignment]

try:
    import quant_engine
except Exception:
    try:
        from finans_dashboard import quant_engine  # type: ignore[no-redef]
    except Exception:
        quant_engine = None  # type: ignore[assignment]


DATA_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(DATA_DIR, "alpha_cache.json")
ALERT_HISTORY_PATH = os.path.join(DATA_DIR, "alpha_alert_history.json")

_CACHE_LOCK = threading.Lock()
_SCAN_INFLIGHT_LOCK = threading.Lock()
_ALERT_LOCK = threading.Lock()
_MEMORY_CACHE: Optional[Dict[str, Any]] = None
_MEMORY_CACHE_TS: float = 0.0
_CACHE_TTL_SEC: float = 1800.0  # 30 minutes
_TR_TZ = datetime.timezone(datetime.timedelta(hours=3))
_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")


def _now_tr() -> datetime.datetime:
    return datetime.datetime.now(_TR_TZ)

SLIPPAGE_PCT_ONE_SIDE = 0.0015  # 0.15% commission + slippage per side (0.30% round trip)

UNIVERSE: List[Dict[str, str]] = [
    # BIST Blue Chips
    {"symbol": "THYAO.IS", "name": "Türk Hava Yolları", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "TUPRS.IS", "name": "Tüpraş", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "ASELS.IS", "name": "Aselsan", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "BIMAS.IS", "name": "BİM Mağazalar", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "KCHOL.IS", "name": "Koç Holding", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "FROTO.IS", "name": "Ford Otosan", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "MGROS.IS", "name": "Migros", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "TCELL.IS", "name": "Turkcell", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "AKBNK.IS", "name": "Akbank", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "GARAN.IS", "name": "Garanti BBVA", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "SAHOL.IS", "name": "Sabancı Holding", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "TURSG.IS", "name": "Türkiye Sigorta", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "EREGL.IS", "name": "Erdemir", "bucket": "BIST", "currency": "TRY"},
    {"symbol": "SISE.IS", "name": "Şişecam", "bucket": "BIST", "currency": "TRY"},
    # Global ETF & Tech Leaders
    {"symbol": "QQQ", "name": "Nasdaq-100 ETF", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "SPY", "name": "S&P 500 ETF", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "SMH", "name": "Yarı İletken ETF", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "GLD", "name": "SPDR Altın ETF", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "NVDA", "name": "NVIDIA", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "MSFT", "name": "Microsoft", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "AAPL", "name": "Apple", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "GOOGL", "name": "Alphabet", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "META", "name": "Meta Platforms", "bucket": "GLOBAL", "currency": "USD"},
    {"symbol": "AMZN", "name": "Amazon", "bucket": "GLOBAL", "currency": "USD"},
    # Major Liquid Crypto
    {"symbol": "BTC-USD", "name": "Bitcoin", "bucket": "CRYPTO", "currency": "USD"},
    {"symbol": "ETH-USD", "name": "Ethereum", "bucket": "CRYPTO", "currency": "USD"},
    {"symbol": "SOL-USD", "name": "Solana", "bucket": "CRYPTO", "currency": "USD"},
]

STRATEGY_LABELS: Dict[str, str] = {
    "UPTREND_PULLBACK": "Yükselişteyken Düşüp Döndüğünde",
    "DEEP_VALUE_REBOUND": "Büyük Çöküşten Toparlandığında",
    "MOMENTUM_BREAKOUT": "Zirve Kırıp Yükselişe Geçtiğinde",
}

MIN_EDGE_PROFIT_FACTOR = 1.35
MIN_EDGE_AVG_PCT = 0.50
MIN_EDGE_WIN_RATE = 35.0


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return default


def _compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(close), 50.0, dtype=float)
    if len(close) <= period:
        return out
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    out[period] = 100.0 if avg_loss <= 1e-12 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + float(gains[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(losses[i])) / period
        if avg_loss <= 1e-12:
            out[i + 1] = 100.0 if avg_gain > 0 else 50.0
        else:
            out[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def _compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.zeros(len(close), dtype=float)
    if len(close) <= period:
        return out
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    val = float(np.mean(tr[:period]))
    out[period] = val
    for i in range(period, len(tr)):
        val = (val * (period - 1) + float(tr[i])) / period
        out[i + 1] = val
    return out


def _compute_sma(arr: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(arr), np.nan, dtype=float)
    if len(arr) >= window:
        out[window - 1 :] = np.convolve(arr, np.ones(window) / float(window), mode="valid")
    return out


def compute_indicators(ohlcv: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    o = ohlcv["open"]
    h = ohlcv["high"]
    l = ohlcv["low"]
    c = ohlcv["close"]
    v = ohlcv["volume"]

    s20 = _compute_sma(c, 20)
    s50 = _compute_sma(c, 50)
    s200 = _compute_sma(c, 200)
    r14 = _compute_rsi(c, 14)
    a14 = _compute_atr(h, l, c, 14)
    v20 = _compute_sma(v, 20)

    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "sma20": s20,
        "sma50": s50,
        "sma200": s200,
        "rsi14": r14,
        "atr14": a14,
        "vol_sma20": v20,
    }


def _check_entry_signal(ind: Dict[str, np.ndarray], i: int, strategy: str) -> bool:
    """Pure causal check using only bars <= i (zero lookahead)."""
    if i < 200:
        return False
    c = ind["close"]
    o = ind["open"]
    s50 = ind["sma50"]
    s200 = ind["sma200"]
    r14 = ind["rsi14"]
    a14 = ind["atr14"]

    if np.isnan(s200[i]) or np.isnan(s50[i]) or a14[i] <= 0 or c[i] <= 0:
        return False

    hi20 = float(np.max(c[i - 20 : i]))
    hi90 = float(np.max(c[max(0, i - 90) : i]))
    dd20 = (c[i] / hi20 - 1.0) * 100.0 if hi20 > 0 else 0.0
    dd90 = (c[i] / hi90 - 1.0) * 100.0 if hi90 > 0 else 0.0
    green_turn = bool(c[i] > c[i - 1] and c[i] >= o[i])

    if strategy == "UPTREND_PULLBACK":
        return bool(
            c[i] > s200[i]
            and s50[i] > s200[i]
            and (dd20 <= -4.0 or r14[i] <= 44.0)
            and green_turn
        )
    if strategy == "DEEP_VALUE_REBOUND":
        return bool(
            dd90 <= -14.0
            and r14[i] <= 34.0
            and r14[i] > r14[i - 1]
            and c[i] > c[i - 1]
        )
    if strategy == "MOMENTUM_BREAKOUT":
        return bool(
            c[i] > s50[i] > s200[i]
            and c[i] > hi20
            and 55.0 <= r14[i] <= 74.0
        )
    return False


def backtest_strategy(ind: Dict[str, np.ndarray], strategy: str) -> Dict[str, Any]:
    """
    Runs a strict causal daily-bar backtest with 0.15% one-way slippage/fees,
    hard ATR stop-loss (-2.5 ATR), and ATR trailing profit protection (-3.0 ATR).
    """
    c = ind["close"]
    h = ind["high"]
    l = ind["low"]
    s50 = ind["sma50"]
    r14 = ind["rsi14"]
    a14 = ind["atr14"]

    n_bars = len(c)
    min_trades = 3 if strategy == "DEEP_VALUE_REBOUND" else 4
    if n_bars < 220:
        return {"qualified": False, "trades": 0, "reason": "Yetersiz geçmiş veri (<220 gün)"}

    trades: List[float] = []
    in_pos = False
    entry_p = 0.0
    stop_p = 0.0
    peak_p = 0.0
    entry_i = 0

    for i in range(205, n_bars - 1):
        if not in_pos:
            if _check_entry_signal(ind, i, strategy):
                in_pos = True
                entry_p = float(c[i]) * (1.0 + SLIPPAGE_PCT_ONE_SIDE)
                stop_p = entry_p - 2.5 * float(a14[i])
                peak_p = entry_p
                entry_i = i
        else:
            peak_p = max(peak_p, float(h[i]))
            held = i - entry_i
            trail = peak_p - 3.0 * float(a14[i])
            eff_stop = max(stop_p, trail) if float(c[i]) > entry_p * 1.04 else stop_p

            exit_p: Optional[float] = None
            if float(l[i]) <= eff_stop:
                exit_p = max(eff_stop, float(l[i])) * (1.0 - SLIPPAGE_PCT_ONE_SIDE)
            elif strategy == "DEEP_VALUE_REBOUND" and (r14[i] >= 64.0 or held >= 55):
                exit_p = float(c[i]) * (1.0 - SLIPPAGE_PCT_ONE_SIDE)
            elif strategy == "UPTREND_PULLBACK" and ((r14[i] >= 72.0 and held >= 5) or held >= 55):
                exit_p = float(c[i]) * (1.0 - SLIPPAGE_PCT_ONE_SIDE)
            elif strategy == "MOMENTUM_BREAKOUT" and (not np.isnan(s50[i]) and float(c[i]) < float(s50[i]) and held >= 5):
                exit_p = float(c[i]) * (1.0 - SLIPPAGE_PCT_ONE_SIDE)

            if exit_p is not None:
                ret_pct = (exit_p / entry_p - 1.0) * 100.0
                trades.append(ret_pct)
                in_pos = False

    if len(trades) < min_trades:
        return {
            "qualified": False,
            "trades": len(trades),
            "win_rate_pct": 0.0,
            "avg_return_pct": 0.0,
            "profit_factor": 0.0,
            "compounded_pct": 0.0,
            "worst_trade_pct": 0.0,
            "reason": f"Yetersiz sinyal örneği ({len(trades)}/{min_trades})",
        }

    arr = np.array(trades, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    gross_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = abs(float(np.sum(losses))) if len(losses) > 0 else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 1e-9 else (9.9 if gross_win > 0 else 0.0)
    win_rate = float(np.mean(arr > 0) * 100.0)
    avg_ret = float(np.mean(arr))
    comp_ret = float((np.prod(1.0 + arr / 100.0) - 1.0) * 100.0)
    worst_ret = float(np.min(arr))

    if strategy == "MOMENTUM_BREAKOUT":
        # Trend breakouts naturally have ~30-45% win rate with large asymmetric winners
        qualified = pf >= 1.50 and avg_ret >= 1.50 and win_rate >= 30.0
    else:
        # Mean-reversion dip buys require high hit rate to avoid catching falling knives
        qualified = pf >= MIN_EDGE_PROFIT_FACTOR and avg_ret >= MIN_EDGE_AVG_PCT and win_rate >= 45.0

    reason = "Onaylı İstatistiksel Avantaj" if qualified else f"Eşik altı (PF {pf:.2f}, Ort {avg_ret:+.1f}%)"

    return {
        "qualified": qualified,
        "trades": int(len(arr)),
        "win_rate_pct": round(win_rate, 1),
        "avg_return_pct": round(avg_ret, 2),
        "profit_factor": round(pf, 2),
        "compounded_pct": round(comp_ret, 1),
        "worst_trade_pct": round(worst_ret, 1),
        "reason": reason,
    }


def _extract_ohlcv_from_df(raw_df: Any) -> Optional[Dict[str, np.ndarray]]:
    if raw_df is None or len(raw_df) == 0:
        return None
    try:
        sub = raw_df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
        if len(sub) < 60:
            return None
        vol = sub["Volume"].fillna(1.0).values.astype(float)
        return {
            "open": sub["Open"].values.astype(float),
            "high": sub["High"].values.astype(float),
            "low": sub["Low"].values.astype(float),
            "close": sub["Close"].values.astype(float),
            "volume": vol,
            "dates": [idx.strftime("%Y-%m-%d") for idx in sub.index],
        }
    except Exception:
        return None


def _get_regime_position_multiplier() -> Tuple[float, str]:
    if quant_engine is None:
        return 1.0, "NORMAL"
    try:
        q = quant_engine.build_quant_radar(budget_try=6000.0, stress_mode="live", force_refresh=False)
        lvl = str(q.get("overall_regime", {}).get("level", "GREEN")).upper()
        if lvl == "RED":
            return 0.50, "RED"
        if lvl == "YELLOW":
            return 0.75, "YELLOW"
        return 1.0, "GREEN"
    except Exception:
        return 1.0, "NORMAL"


def evaluate_asset(
    meta: Dict[str, str],
    ohlcv: Dict[str, np.ndarray],
    regime_mult: float = 1.0,
) -> Dict[str, Any]:
    """Evaluates all 3 strategies on one asset and returns live signal + risk sizing."""
    ind = compute_indicators(ohlcv)
    c = ind["close"]
    r14 = ind["rsi14"]
    a14 = ind["atr14"]
    s50 = ind["sma50"]
    s200 = ind["sma200"]
    v = ind["volume"]
    vsma20 = ind["vol_sma20"]
    last_idx = len(c) - 1

    dates_list = ohlcv.get("dates") or []

    def _fmt_tr(d: str) -> str:
        if len(d) >= 10 and d[4] == "-" and d[7] == "-":
            return f"{d[8:10]}.{d[5:7]}.{d[0:4]}"
        return d

    last_close = float(c[last_idx])
    prev_close = float(c[last_idx - 1]) if last_idx >= 1 else last_close
    chg_1d_pct = ((last_close / prev_close) - 1.0) * 100.0 if prev_close > 0 else 0.0
    start90 = max(0, last_idx - 90)
    win90 = c[start90 : last_idx + 1]
    hi90_idx = start90 + int(np.argmax(win90))
    hi90 = float(c[hi90_idx])
    dd90_pct = ((last_close / hi90) - 1.0) * 100.0 if hi90 > 0 else 0.0
    start20 = max(0, last_idx - 20)
    win20 = c[start20 : last_idx + 1]
    hi20_idx = start20 + int(np.argmax(win20))
    hi20 = float(c[hi20_idx])
    dd20_pct = ((last_close / hi20) - 1.0) * 100.0 if hi20 > 0 else 0.0
    last_date_str = dates_list[last_idx] if len(dates_list) > last_idx else ""
    last_date_tr = _fmt_tr(last_date_str)
    peak20_date_tr = _fmt_tr(dates_list[hi20_idx]) if len(dates_list) > hi20_idx else ""
    peak90_date_tr = _fmt_tr(dates_list[hi90_idx]) if len(dates_list) > hi90_idx else ""
    cur_rsi = round(float(r14[last_idx]), 1)
    cur_atr = float(a14[last_idx])
    atr_pct = (cur_atr / last_close * 100.0) if last_close > 0 else 2.5
    vol_ratio = (
        round(float(v[last_idx] / vsma20[last_idx]), 2)
        if not np.isnan(vsma20[last_idx]) and vsma20[last_idx] > 0
        else 1.0
    )

    strat_results: Dict[str, Dict[str, Any]] = {}
    triggered_today: List[Tuple[str, Dict[str, Any]]] = []
    triggered_fresh_2d: List[Tuple[str, Dict[str, Any]]] = []

    for strat in ("UPTREND_PULLBACK", "DEEP_VALUE_REBOUND", "MOMENTUM_BREAKOUT"):
        bt = backtest_strategy(ind, strat)
        fire_today = _check_entry_signal(ind, last_idx, strat)
        fire_yest = _check_entry_signal(ind, last_idx - 1, strat) if last_idx >= 2 else False
        bt["fired_today"] = fire_today
        bt["fired_recent"] = fire_today or fire_yest
        strat_results[strat] = bt
        if fire_today and bt["qualified"]:
            triggered_today.append((strat, bt))
        elif (fire_today or fire_yest) and bt["qualified"]:
            triggered_fresh_2d.append((strat, bt))

    # Pick strongest qualified live trigger (prefer today's exact signal, then yesterday's fresh signal)
    chosen_pool = triggered_today or triggered_fresh_2d
    best_strat_name: Optional[str] = None
    best_bt: Optional[Dict[str, Any]] = None
    if chosen_pool:
        chosen_pool.sort(key=lambda item: (item[1]["profit_factor"], item[1]["avg_return_pct"]), reverse=True)
        best_strat_name, best_bt = chosen_pool[0]

    # Pick asset's overall best historical strategy for reference even when idle
    qualified_strats = [(k, v) for k, v in strat_results.items() if v.get("qualified")]
    best_hist_name, best_hist_bt = (
        max(qualified_strats, key=lambda item: (item[1]["profit_factor"], item[1]["compounded_pct"]))
        if qualified_strats
        else max(strat_results.items(), key=lambda item: item[1].get("profit_factor", 0.0))
    )

    # Anti-Falling-Knife detector: sharp drop without green turn OR below falling SMA200 without rebound
    is_oversold_falling = bool(
        (cur_rsi <= 36.0 or dd20_pct <= -6.0)
        and last_close <= prev_close
    )
    any_unproven_trigger = any(bt.get("fired_recent") and not bt.get("qualified") for bt in strat_results.values())

    stop_price = max(0.0, last_close - 2.5 * cur_atr)
    stop_pct = ((stop_price / last_close) - 1.0) * 100.0 if last_close > 0 else -5.0
    target_1 = last_close + 2.5 * cur_atr
    target_2 = last_close + 4.5 * cur_atr
    target_1_pct = ((target_1 / last_close) - 1.0) * 100.0 if last_close > 0 else 5.0
    target_2_pct = ((target_2 / last_close) - 1.0) * 100.0 if last_close > 0 else 10.0

    # Volatility-targeted position size capped between 3% and 15%, scaled by macro regime
    raw_alloc = 22.0 / max(abs(stop_pct), 1.5)
    max_pos_pct = round(min(15.0, max(3.0, raw_alloc * regime_mult)), 1)

    if best_strat_name and best_bt:
        status = "BUY_SIGNAL"
        # Score 60-99 based on PF, win rate, avg return, discount, and freshness
        score = (
            45.0
            + min(20.0, (best_bt["profit_factor"] - 1.35) * 8.0)
            + min(15.0, max(0.0, (best_bt["win_rate_pct"] - 40.0) * 0.45))
            + min(10.0, max(0.0, best_bt["avg_return_pct"] * 1.1))
            + (6.0 if best_bt["fired_today"] else 2.0)
            + (4.0 if vol_ratio >= 1.25 else 0.0)
        )
        score = int(round(min(99, max(60, score))))
        signal_strength = "STRONG" if (score >= 72 and best_bt["profit_factor"] >= 1.50 and best_bt["fired_today"]) else "MODERATE"
    elif is_oversold_falling:
        status = "FALLING_KNIFE_WAIT"
        score = 25
        signal_strength = "WAIT"
    elif any_unproven_trigger:
        status = "REJECTED_NO_EDGE"
        score = 15
        signal_strength = "BLOCKED"
    else:
        status = "NEUTRAL"
        score = 40
        signal_strength = "NONE"

    sma200_val = float(s200[last_idx]) if not np.isnan(s200[last_idx]) else last_close
    sma50_val = float(s50[last_idx]) if not np.isnan(s50[last_idx]) else last_close
    trend_label = (
        "Genel Yön Yukarı" if last_close > sma50_val > sma200_val
        else ("Orta Seviye" if last_close > sma200_val else "Düşüş Eğiliminde")
    )

    ref_date = peak90_date_tr if (abs(dd90_pct) > abs(dd20_pct) + 4.0 and peak90_date_tr) else peak20_date_tr
    ref_drop = abs(dd90_pct) if (abs(dd90_pct) > abs(dd20_pct) + 4.0 and peak90_date_tr) else abs(dd20_pct)

    if status == "BUY_SIGNAL" and best_bt:
        verdict_tr = "🟢 ALINABİLİR"
        if best_strat_name == "MOMENTUM_BREAKOUT":
            plain_reason = (
                f"{last_date_tr} tarihinde yeni yükseliş başlattı (bugün {chg_1d_pct:+.1f}%). "
                f"Son 5 yılda bu hareket {best_bt['trades']} kez oldu: %{best_bt['win_rate_pct']}'inde kazandırdı, ortalama +{best_bt['avg_return_pct']:.1f}% getirdi."
            )
        else:
            drop_txt = f"{ref_date} tarihinden beri %{ref_drop:.1f} düştü, " if (ref_date and ref_drop >= 2.0) else ""
            plain_reason = (
                f"{drop_txt}{last_date_tr} itibarıyla alıcılar geri döndü (bugün {chg_1d_pct:+.1f}%). "
                f"Son 5 yılda bu durumda {best_bt['trades']} kezden %{best_bt['win_rate_pct']}'inde kazandırdı, ortalama +{best_bt['avg_return_pct']:.1f}% yükseldi."
            )
    elif status == "FALLING_KNIFE_WAIT":
        verdict_tr = "🛑 ŞİMDİ ALMA (Düşmeye Devam Ediyor)"
        since_txt = f"{ref_date}'den {last_date_tr}'e kadar" if ref_date else f"{last_date_tr} itibarıyla"
        plain_reason = f"{since_txt} %{ref_drop:.1f} düştü ama satış henüz durmadı. Yeşile dönene kadar alma, bekle."
    elif status == "REJECTED_NO_EDGE":
        verdict_tr = "⛔ ALMA (Geçmişte Kaybettirdi)"
        since_txt = f"{ref_date}'den beri %{ref_drop:.1f} düşmüş görünüyor" if ref_date else f"{last_date_tr} itibarıyla ucuzlamış görünüyor"
        plain_reason = f"{since_txt} ama son 5 yılda bu seviyeden alanlar genelde zarar etmiş. Uzak dur."
    else:
        verdict_tr = "👀 BEKLE (Henüz Fırsat Yok)"
        if ref_date and ref_drop >= 2.0:
            plain_reason = f"{ref_date} zirvesine göre sadece %{ref_drop:.1f} aşağıda. Alım için belirgin bir düşüş veya güçlü yükseliş yok."
        else:
            plain_reason = f"{last_date_tr} itibarıyla normal fiyatında. Acele etme, uygun düşüşü bekle."

    return {
        "symbol": meta["symbol"],
        "name": meta["name"],
        "bucket": meta["bucket"],
        "currency": meta["currency"],
        "last_date": last_date_str,
        "last_date_tr": last_date_tr,
        "dip_from_date": ref_date,
        "verdict_tr": verdict_tr,
        "plain_reason": plain_reason,
        "price": round(last_close, 4),
        "chg_1d_pct": round(chg_1d_pct, 2),
        "dd20_pct": round(dd20_pct, 2),
        "dd90_pct": round(dd90_pct, 2),
        "rsi14": cur_rsi,
        "vol_ratio": vol_ratio,
        "trend": trend_label,
        "status": status,
        "signal_strength": signal_strength,
        "score": score,
        "active_strategy": best_strat_name,
        "active_strategy_label": STRATEGY_LABELS.get(best_strat_name or "", ""),
        "active_backtest": best_bt,
        "best_historical_strategy": best_hist_name,
        "best_historical_label": STRATEGY_LABELS.get(best_hist_name, best_hist_name),
        "best_historical_backtest": best_hist_bt,
        "risk_plan": {
            "entry": round(last_close, 4),
            "stop_loss": round(stop_price, 4),
            "stop_pct": round(stop_pct, 2),
            "target_1": round(target_1, 4),
            "target_1_pct": round(target_1_pct, 2),
            "target_2": round(target_2, 4),
            "target_2_pct": round(target_2_pct, 2),
            "rr_ratio": 1.8 if abs(stop_pct) > 0 else 1.0,
            "max_pos_pct": max_pos_pct,
        },
        "strategies": strat_results,
    }


def _load_disk_cache() -> Optional[Dict[str, Any]]:
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "all_assets" in data:
            return data
    except Exception:
        pass
    return None


def _save_disk_cache(payload: Dict[str, Any]) -> None:
    tmp_path = f"{CACHE_PATH}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, CACHE_PATH)
    except Exception:
        pass


_BG_REFRESH_RUNNING = False


def _trigger_bg_alpha_refresh() -> None:
    global _BG_REFRESH_RUNNING
    with _CACHE_LOCK:
        if _BG_REFRESH_RUNNING:
            return
        _BG_REFRESH_RUNNING = True

    def _worker():
        global _BG_REFRESH_RUNNING
        try:
            scan_alpha_universe(force_refresh=True)
        except Exception:
            pass
        finally:
            with _CACHE_LOCK:
                _BG_REFRESH_RUNNING = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def scan_alpha_universe(force_refresh: bool = False) -> Dict[str, Any]:
    """Scans the full 27-asset universe across 5Y history (cached 30 min, coalesced under lock)."""
    global _MEMORY_CACHE, _MEMORY_CACHE_TS
    now_mono = time.time()

    if not force_refresh:
        with _CACHE_LOCK:
            if _MEMORY_CACHE and (now_mono - _MEMORY_CACHE_TS) < _CACHE_TTL_SEC:
                return _MEMORY_CACHE
        disk = _load_disk_cache()
        if disk:
            age = now_mono - _safe_float(disk.get("fetched_epoch"), 0.0)
            with _CACHE_LOCK:
                _MEMORY_CACHE = disk
                _MEMORY_CACHE_TS = _safe_float(disk.get("fetched_epoch"), now_mono)
            if age >= _CACHE_TTL_SEC:
                _trigger_bg_alpha_refresh()
            return disk

    with _SCAN_INFLIGHT_LOCK:
        now_mono = time.time()
        if not force_refresh:
            with _CACHE_LOCK:
                if _MEMORY_CACHE and (now_mono - _MEMORY_CACHE_TS) < _CACHE_TTL_SEC:
                    return _MEMORY_CACHE

        regime_mult, regime_level = _get_regime_position_multiplier()
        symbols = [m["symbol"] for m in UNIVERSE]
        if yf is None:
            return {
                "status": "error",
                "fetched_at": _now_tr().strftime("%Y-%m-%d %H:%M:%S"),
                "fetched_epoch": time.time(),
                "macro_regime": regime_level,
                "regime_pos_multiplier": regime_mult,
                "universe_count": 0,
                "qualified_edge_count": 0,
                "opportunities": [],
                "falling_knives": [],
                "rejected_no_edge": [],
                "top_edge_leaders": [],
                "all_assets": [],
            }

        df = yf.download(
            symbols,
            period="5y",
            interval="1d",
            group_by="ticker",
            progress=False,
            threads=True,
        )

        evaluated: List[Dict[str, Any]] = []
        for meta in UNIVERSE:
            sym = meta["symbol"]
            try:
                sub_df = df[sym] if len(symbols) > 1 else df
                ohlcv = _extract_ohlcv_from_df(sub_df)
                if ohlcv is not None:
                    evaluated.append(evaluate_asset(meta, ohlcv, regime_mult=regime_mult))
            except Exception:
                continue
        del df
        try:
            import gc
            gc.collect()
        except Exception:
            pass

        # Stale-while-revalidate fallback if transient network error yielded empty evaluation
        if not evaluated:
            fallback = _MEMORY_CACHE or _load_disk_cache()
            if fallback:
                return {**fallback, "stale": True}

        opportunities = sorted(
            [a for a in evaluated if a["status"] == "BUY_SIGNAL"],
            key=lambda x: (x["score"], (x.get("active_backtest") or {}).get("profit_factor", 0.0)),
            reverse=True,
        )
        falling_knives = sorted(
            [a for a in evaluated if a["status"] == "FALLING_KNIFE_WAIT"],
            key=lambda x: x["rsi14"],
        )
        rejected = [a for a in evaluated if a["status"] == "REJECTED_NO_EDGE"]
        top_edge_leaders = sorted(
            [a for a in evaluated if (a.get("best_historical_backtest") or {}).get("qualified")],
            key=lambda x: (x["best_historical_backtest"]["profit_factor"], x["best_historical_backtest"]["compounded_pct"]),
            reverse=True,
        )

        payload = {
            "status": "ok",
            "fetched_at": _now_tr().strftime("%Y-%m-%d %H:%M:%S"),
            "fetched_epoch": time.time(),
            "macro_regime": regime_level,
            "regime_pos_multiplier": regime_mult,
            "universe_count": len(evaluated),
            "qualified_edge_count": len(top_edge_leaders),
            "opportunities": opportunities,
            "falling_knives": falling_knives,
            "rejected_no_edge": rejected,
            "top_edge_leaders": top_edge_leaders[:8],
            "all_assets": evaluated,
        }

        with _CACHE_LOCK:
            _MEMORY_CACHE = payload
            _MEMORY_CACHE_TS = payload["fetched_epoch"]
        if evaluated:
            _save_disk_cache(payload)
        return payload


def resolve_user_symbol(raw_symbol: str) -> Tuple[str, str, str]:
    """Resolves user input like 'ASELS', 'BTC', 'NVDA' to Yahoo Finance ticker + bucket."""
    s = str(raw_symbol or "").strip().upper().replace("/", "-")
    if not _VALID_SYMBOL_RE.match(s):
        return "", "", "INVALID"
    for m in UNIVERSE:
        base = m["symbol"].split(".")[0].split("-")[0]
        if s in (m["symbol"], base):
            return m["symbol"], m["name"], m["bucket"]

    if s in ("BTC", "ETH", "SOL", "AVAX", "BNB", "XRP", "DOGE", "ADA", "LINK"):
        return f"{s}-USD", s, "CRYPTO"

    if yf is None:
        return s, s, ("CRYPTO" if s.endswith("-USD") else ("BIST" if s.endswith(".IS") else "GLOBAL"))

    # Try exact first, then BIST (.IS)
    try:
        h = yf.Ticker(s).history(period="1mo")
        if h is not None and not h.empty:
            bucket = "CRYPTO" if s.endswith("-USD") else ("BIST" if s.endswith(".IS") else "GLOBAL")
            return s, s, bucket
    except Exception:
        pass

    if not s.endswith(".IS") and "-" not in s:
        cand = f"{s}.IS"
        try:
            h = yf.Ticker(cand).history(period="1mo")
            if h is not None and not h.empty:
                return cand, s, "BIST"
        except Exception:
            pass
    return s, s, "GLOBAL"


def analyze_single_asset(raw_symbol: str) -> Optional[Dict[str, Any]]:
    """Fetches 5Y daily history and runs full backtest + live risk evaluation for any asset."""
    if yf is None:
        return None
    sym, name, bucket = resolve_user_symbol(raw_symbol)
    if not sym or bucket == "INVALID":
        return None
    currency = "TRY" if sym.endswith(".IS") else "USD"
    try:
        hist = yf.Ticker(sym).history(period="5y", interval="1d")
        ohlcv = _extract_ohlcv_from_df(hist)
        if ohlcv is None:
            return None
        regime_mult, _ = _get_regime_position_multiplier()
        meta = {"symbol": sym, "name": name, "bucket": bucket, "currency": currency}
        return evaluate_asset(meta, ohlcv, regime_mult=regime_mult)
    except Exception:
        return None


def _load_alert_history() -> Dict[str, float]:
    if not os.path.exists(ALERT_HISTORY_PATH):
        return {}
    try:
        with open(ALERT_HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_alert_history(hist: Dict[str, float]) -> None:
    tmp_path = f"{ALERT_HISTORY_PATH}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=2)
        os.replace(tmp_path, ALERT_HISTORY_PATH)
    except Exception:
        pass


def get_new_high_conviction_alerts(cooldown_hours: float = 24.0) -> List[Dict[str, Any]]:
    """
    Returns strong backtest-verified BUY signals fired TODAY that have not
    alerted within the last `cooldown_hours`. Updates cooldown state atomically under lock.
    """
    scan = scan_alpha_universe(force_refresh=False)
    opps = scan.get("opportunities") or []
    now_ts = time.time()
    cooldown_sec = cooldown_hours * 3600.0

    with _ALERT_LOCK:
        hist = _load_alert_history()
        fresh_alerts: List[Dict[str, Any]] = []
        for item in opps:
            bt = item.get("active_backtest") or {}
            if item.get("signal_strength") != "STRONG" or not bt.get("fired_today"):
                continue
            key = f"{item['symbol']}:{item['active_strategy']}"
            last_sent = _safe_float(hist.get(key), 0.0)
            if (now_ts - last_sent) < cooldown_sec:
                continue
            fresh_alerts.append(item)
            hist[key] = now_ts

        if fresh_alerts:
            _save_alert_history(hist)
        return fresh_alerts[:3]


def _fmt_price(val: float, currency: str) -> str:
    sym = "₺" if currency == "TRY" else "$"
    if val >= 100:
        return f"{sym}{val:,.2f}"
    return f"{sym}{val:,.4f}"


def format_alpha_scan_telegram(scan: Optional[Dict[str, Any]] = None) -> str:
    data = scan or scan_alpha_universe(force_refresh=False)
    opps = data.get("opportunities") or []
    knives = data.get("falling_knives") or []
    rejected = data.get("rejected_no_edge") or []
    leaders = data.get("top_edge_leaders") or []
    reg = str(data.get("macro_regime", "GREEN")).upper()
    reg_tr = "🔴 Piyasa Gergin (Az Para Koy)" if reg == "RED" else ("🟡 Piyasa Dalgalı (Dikkatli Ol)" if reg == "YELLOW" else "🟢 Piyasa Sakin")
    today_tr = _now_tr().strftime("%d.%m.%Y")

    lines = [
        f"📢 *BUGÜN NE ALINIR, NE ALINMAZ? ({today_tr})*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"`{data.get('universe_count', 0)}` hisse ve coin incelendi · {reg_tr}",
        "",
    ]

    if opps:
        lines.append(f"🟢 *ŞU AN ALINABİLİRLER ({len(opps)}):*")
        for item in opps[:4]:
            bt = item["active_backtest"]
            rp = item["risk_plan"]
            cur = item["currency"]
            lines.append(
                f"\n✅ *{item['symbol']}* ({item['name']}) — *ALINABİLİR*\n"
                f"├ *Ne Oldu?:* {item.get('plain_reason', '')}\n"
                f"├ *Güncel Fiyat:* `{_fmt_price(item['price'], cur)}` (`Bugün {item['chg_1d_pct']:+.1f}%`)\n"
                f"├ *Geçmiş Kanıtı:* Son 5 yılda `{bt['trades']}` kez oldu → `%{bt['win_rate_pct']}` kazandırdı (ort. `+{bt['avg_return_pct']:.1f}%`)\n"
                f"└ *Plan:* Zarar Kes `{_fmt_price(rp['stop_loss'], cur)}` (`{rp['stop_pct']:+.1f}%`) · Hedef `{_fmt_price(rp['target_1'], cur)}` (`+{rp['target_1_pct']:.1f}%`) · En fazla `%{rp['max_pos_pct']}` koy"
            )
    else:
        lines.append("🛡 *BUGÜN ALINABİLECEK FIRSAT YOK*")
        lines.append("_Şu an düşüp de yükselişe dönen güvenli bir fırsat görünmüyor. Paran ZBJ'de / nakitte dursun._")

    dont_buy = knives[:3] + rejected[:2]
    if dont_buy:
        lines.append("")
        lines.append("🛑 *DÜŞTÜ AMA ŞİMDİ ALMA (Daha da Düşebilir):*")
        for k in dont_buy[:4]:
            cur = k["currency"]
            since = f"{k.get('dip_from_date')} tarihinden beri " if k.get("dip_from_date") else ""
            drop_v = abs(k.get("dd90_pct", 0.0)) if abs(k.get("dd90_pct", 0.0)) > abs(k.get("dd20_pct", 0.0)) + 4 else abs(k.get("dd20_pct", 0.0))
            why = "geçmişte buradan alanlar zarar etmiş" if k.get("status") == "REJECTED_NO_EDGE" else "satış henüz durmadı, yeşile dönmesini bekle"
            lines.append(f"• *{k['symbol']}* (`{_fmt_price(k['price'], cur)}`): {since}`%{drop_v:.1f}` düştü — _{why}_.")

    if not opps and leaders:
        lines.append("")
        lines.append("⏳ *FIRSAT ÇIKINCA İLK BAKILACAKLAR (En Çok Kazandıranlar):*")
        for ld in leaders[:4]:
            bt = ld["best_historical_backtest"]
            lines.append(
                f"• *{ld['symbol']}*: Son 5 yılda `{bt['trades']}` fırsatta `%{bt['win_rate_pct']}` kazandırdı (ort. `+{bt['avg_return_pct']:.1f}%`)"
            )

    lines.append("")
    lines.append("🔎 _Tek hisse veya coin sormak için:_ `/backtest ASELS` veya `/backtest BTC`")
    return "\n".join(lines)


def format_single_asset_telegram(raw_symbol: str) -> str:
    res = analyze_single_asset(raw_symbol)
    if not res:
        return f"⚠️ `{raw_symbol.upper()}` için yeterli geçmiş veri bulunamadı."

    cur = res["currency"]
    rp = res["risk_plan"]
    date_label = res.get("last_date_tr") or _now_tr().strftime("%d.%m.%Y")

    lines = [
        f"🔎 *{res['symbol']} NE DURUMDA? ({date_label})*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"• *Karar:* *{res.get('verdict_tr', res['status'])}*",
        f"• *Güncel Fiyat:* `{_fmt_price(res['price'], cur)}` (`Bugün {res['chg_1d_pct']:+.2f}%` · `{res['trend']}`)",
        f"• *Özet:* {res.get('plain_reason', '')}",
        "",
        "📊 *SON 5 YILDA AYNI DURUMDA NE YAPMIŞ?*",
    ]

    for s_key in ("UPTREND_PULLBACK", "DEEP_VALUE_REBOUND", "MOMENTUM_BREAKOUT"):
        bt = res["strategies"][s_key]
        badge = "✅" if bt["qualified"] else "❌"
        trig = " ⚡*ŞU AN AKTİF*" if bt.get("fired_recent") else ""
        if bt["trades"] > 0:
            lines.append(
                f"{badge} *{STRATEGY_LABELS[s_key]}*{trig}\n"
                f"  └ `{bt['trades']}` kez oldu → `%{bt['win_rate_pct']}` kazandırdı (ort. `{bt['avg_return_pct']:+.1f}%`, en kötü `{bt['worst_trade_pct']:+.1f}%`)"
            )
        else:
            lines.append(f"⚪ *{STRATEGY_LABELS[s_key]}*: _Son 5 yılda yeterli örnek yok_")

    lines.append("")
    lines.append(
        f"🛡 *ALIRSAN RİSK PLANIN:*\n"
        f"• *Zarar Kes (Bunun altına düşerse sat):* `{_fmt_price(rp['stop_loss'], cur)}` (`{rp['stop_pct']:+.1f}%`)\n"
        f"• *Hedefler:* İlk hedef `{_fmt_price(rp['target_1'], cur)}` (`+{rp['target_1_pct']:.1f}%`) · Büyük hedef `{_fmt_price(rp['target_2'], cur)}` (`+{rp['target_2_pct']:.1f}%`)\n"
        f"• *Ne Kadar Koymalı?:* Paranın en fazla `%{rp['max_pos_pct']}` kadarı"
    )
    return "\n".join(lines)
