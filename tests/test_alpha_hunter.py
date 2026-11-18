"""Unit and integration tests for alpha_hunter, telegram_bot routing, and FastAPI endpoints."""

import json
import os
import sys
import numpy as np
import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import alpha_hunter  # noqa: E402
import telegram_bot  # noqa: E402


def _make_trending_pullback_ohlcv(n: int = 420, crash_end: bool = False) -> dict:
    """
    Generates synthetic daily OHLCV with clean periodic pullbacks in an uptrend,
    ending on either a green reversal day (valid entry) or a crashing red day (falling knife).
    """
    rng = np.random.default_rng(42)
    close = np.zeros(n, dtype=float)
    open_p = np.zeros(n, dtype=float)
    high = np.zeros(n, dtype=float)
    low = np.zeros(n, dtype=float)
    vol = np.full(n, 1_000_000.0, dtype=float)

    price = 100.0
    for i in range(n):
        # Smooth primary uptrend with periodic 7-day ~6% pullbacks every 45 bars
        cycle = i % 45
        if 32 <= cycle <= 37:
            ret = -0.014
            candle_green = False
        elif cycle == 38:
            ret = +0.022
            candle_green = True
        elif 39 <= cycle <= 44:
            ret = +0.012
            candle_green = True
        else:
            ret = +0.0025 + float(rng.normal(0.0, 0.002))
            candle_green = ret >= 0

        prev_p = price
        price = max(10.0, price * (1.0 + ret))
        close[i] = price
        open_p[i] = prev_p * (0.996 if candle_green else 1.005)
        high[i] = max(open_p[i], close[i]) * 1.006
        low[i] = min(open_p[i], close[i]) * 0.994

    # Set bar n-1 specifically to test final bar behavior
    if crash_end:
        for k in range(n - 10, n):
            prev_p = close[k - 1]
            close[k] = prev_p * 0.955
            open_p[k] = prev_p * 1.002
            high[k] = open_p[k] * 1.005
            low[k] = close[k] * 0.99
    else:
        # Ensure index n-1 is right after a 6-day dip within uptrend with a green reversal candle
        base_hi = float(np.max(close[n - 25 : n - 7]))
        p = base_hi * 0.99
        for k in range(n - 7, n - 1):
            close[k] = p * 0.988
            open_p[k] = p
            high[k] = p * 1.002
            low[k] = close[k] * 0.995
            p = close[k]
        # Today (n-1): strong green reversal while still above SMA200
        open_p[n - 1] = close[n - 2] * 1.001
        close[n - 1] = close[n - 2] * 1.028
        high[n - 1] = close[n - 1] * 1.006
        low[n - 1] = open_p[n - 1] * 0.996

    dates = [f"2025-01-{(i % 28) + 1:02d}" for i in range(n)]
    return {
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
        "dates": dates,
    }


def _make_choppy_losing_ohlcv(n: int = 420) -> dict:
    """Generates mean-decaying choppy data where dip buys repeatedly lose to stop-loss."""
    rng = np.random.default_rng(7)
    close = np.zeros(n, dtype=float)
    open_p = np.zeros(n, dtype=float)
    high = np.zeros(n, dtype=float)
    low = np.zeros(n, dtype=float)
    vol = np.full(n, 500_000.0, dtype=float)

    price = 100.0
    for i in range(n):
        # Gentle drift with sharp fakeouts that hit -2.5 ATR stop repeatedly
        if i % 30 == 20:
            ret = -0.06
        elif i % 30 == 21:
            ret = +0.015  # fake bounce
        elif 22 <= (i % 30) <= 26:
            ret = -0.025  # continues dumping into stop
        else:
            ret = +0.002 + float(rng.normal(0.0, 0.008))
        prev = price
        price = max(5.0, price * (1.0 + ret))
        close[i] = price
        open_p[i] = prev * (0.998 if ret > 0 else 1.003)
        high[i] = max(open_p[i], close[i]) * 1.01
        low[i] = min(open_p[i], close[i]) * 0.99

    dates = [f"2025-02-{(i % 28) + 1:02d}" for i in range(n)]
    return {"open": open_p, "high": high, "low": low, "close": close, "volume": vol, "dates": dates}


def test_causal_signal_no_lookahead():
    ohlcv = _make_trending_pullback_ohlcv(420, crash_end=False)
    ind_full = alpha_hunter.compute_indicators(ohlcv)

    cut = 300
    ohlcv_trunc = {k: (v[: cut + 1] if isinstance(v, np.ndarray) else v[: cut + 1]) for k, v in ohlcv.items()}
    ind_trunc = alpha_hunter.compute_indicators(ohlcv_trunc)

    for strat in ("UPTREND_PULLBACK", "DEEP_VALUE_REBOUND", "MOMENTUM_BREAKOUT"):
        assert alpha_hunter._check_entry_signal(ind_full, cut, strat) == alpha_hunter._check_entry_signal(ind_trunc, cut, strat)


def test_falling_knife_never_triggers_buy():
    ohlcv = _make_trending_pullback_ohlcv(420, crash_end=True)
    meta = {"symbol": "TEST.IS", "name": "Test Falling", "bucket": "BIST", "currency": "TRY"}
    res = alpha_hunter.evaluate_asset(meta, ohlcv, regime_mult=1.0)
    assert res["status"] == "FALLING_KNIFE_WAIT"
    assert res["signal_strength"] == "WAIT"
    assert res["active_strategy"] is None


def test_unproven_choppy_asset_blocked_by_backtest_gate():
    ohlcv = _make_choppy_losing_ohlcv(420)
    ind = alpha_hunter.compute_indicators(ohlcv)
    for strat in ("UPTREND_PULLBACK", "DEEP_VALUE_REBOUND", "MOMENTUM_BREAKOUT"):
        bt = alpha_hunter.backtest_strategy(ind, strat)
        if bt["trades"] >= 4 and bt["profit_factor"] < alpha_hunter.MIN_EDGE_PROFIT_FACTOR:
            assert bt["qualified"] is False


def test_verified_uptrend_pullback_triggers_with_valid_risk_plan():
    ohlcv = _make_trending_pullback_ohlcv(420, crash_end=False)
    meta = {"symbol": "WIN.IS", "name": "Winner Test", "bucket": "BIST", "currency": "TRY"}

    res_green = alpha_hunter.evaluate_asset(meta, ohlcv, regime_mult=1.0)
    res_red = alpha_hunter.evaluate_asset(meta, ohlcv, regime_mult=0.5)

    assert res_green["status"] == "BUY_SIGNAL"
    assert res_green["verdict_tr"] == "🟢 ALINABİLİR"
    assert res_green["dip_from_date"] != ""
    assert "kazandırdı" in res_green["plain_reason"]
    assert res_green["active_backtest"]["qualified"] is True
    assert res_green["active_backtest"]["profit_factor"] >= alpha_hunter.MIN_EDGE_PROFIT_FACTOR

    rp = res_green["risk_plan"]
    assert rp["stop_loss"] < rp["entry"] < rp["target_1"] < rp["target_2"]
    assert rp["stop_pct"] < 0
    # Red macro regime must scale down max position size (or remain <= green)
    assert res_red["risk_plan"]["max_pos_pct"] <= res_green["risk_plan"]["max_pos_pct"]


def test_alert_cooldown_deduplication(tmp_path, monkeypatch):
    hist_file = tmp_path / "alert_hist.json"
    monkeypatch.setattr(alpha_hunter, "ALERT_HISTORY_PATH", str(hist_file))

    fake_scan = {
        "opportunities": [
            {
                "symbol": "ASELS.IS",
                "name": "Aselsan",
                "currency": "TRY",
                "price": 350.0,
                "chg_1d_pct": 2.4,
                "rsi14": 41.0,
                "score": 88,
                "signal_strength": "STRONG",
                "active_strategy": "UPTREND_PULLBACK",
                "active_strategy_label": "Trend İçi Dipten Dönüş",
                "active_backtest": {
                    "qualified": True,
                    "fired_today": True,
                    "trades": 25,
                    "win_rate_pct": 64.0,
                    "profit_factor": 3.0,
                    "avg_return_pct": 6.5,
                },
                "risk_plan": {
                    "stop_loss": 325.0,
                    "stop_pct": -7.1,
                    "target_1": 375.0,
                    "target_1_pct": 7.1,
                    "max_pos_pct": 6.5,
                },
            }
        ]
    }
    monkeypatch.setattr(alpha_hunter, "scan_alpha_universe", lambda force_refresh=False: fake_scan)

    first = alpha_hunter.get_new_high_conviction_alerts(cooldown_hours=24.0)
    second = alpha_hunter.get_new_high_conviction_alerts(cooldown_hours=24.0)
    assert len(first) == 1
    assert first[0]["symbol"] == "ASELS.IS"
    assert second == []
    assert os.path.exists(hist_file)


def test_telegram_commands_routing(monkeypatch):
    monkeypatch.setattr(
        alpha_hunter,
        "scan_alpha_universe",
        lambda force_refresh=False: {
            "universe_count": 27,
            "qualified_edge_count": 18,
            "macro_regime": "GREEN",
            "regime_pos_multiplier": 1.0,
            "opportunities": [],
            "falling_knives": [],
            "top_edge_leaders": [],
        },
    )
    reply_avci = telegram_bot.process_incoming_text(12345, "/avci")
    assert "BUGÜN NE ALINIR, NE ALINMAZ?" in reply_avci
    assert "27" in reply_avci

    monkeypatch.setattr(
        alpha_hunter,
        "analyze_single_asset",
        lambda sym: {
            "symbol": "ASELS.IS",
            "name": "Aselsan",
            "bucket": "BIST",
            "currency": "TRY",
            "last_date_tr": "16.11.2026",
            "dip_from_date": "27.10.2026",
            "verdict_tr": "👀 BEKLE (Henüz Fırsat Yok)",
            "plain_reason": "27.10.2026 zirvesine göre sadece %2.1 aşağıda.",
            "price": 353.0,
            "chg_1d_pct": 1.2,
            "dd20_pct": -2.1,
            "dd90_pct": -4.0,
            "rsi14": 54.0,
            "trend": "Genel Yön Yukarı",
            "status": "NEUTRAL",
            "score": 40,
            "risk_plan": {
                "stop_loss": 328.0,
                "stop_pct": -7.08,
                "target_1": 378.0,
                "target_1_pct": 7.08,
                "target_2": 398.0,
                "target_2_pct": 12.75,
                "max_pos_pct": 6.2,
            },
            "strategies": {
                "UPTREND_PULLBACK": {"qualified": True, "trades": 27, "win_rate_pct": 63.0, "profit_factor": 3.07, "avg_return_pct": 6.85, "worst_trade_pct": -11.7, "fired_recent": False},
                "DEEP_VALUE_REBOUND": {"qualified": False, "trades": 0, "reason": "Yetersiz sinyal örneği (0/3)"},
                "MOMENTUM_BREAKOUT": {"qualified": True, "trades": 20, "win_rate_pct": 50.0, "profit_factor": 5.47, "avg_return_pct": 11.61, "worst_trade_pct": -13.5, "fired_recent": False},
            },
        },
    )
    reply_bt = telegram_bot.process_incoming_text(12345, "/backtest ASELS")
    assert "NE DURUMDA?" in reply_bt
    assert "ASELS.IS" in reply_bt
    assert "`%63.0` kazandırdı" in reply_bt


def test_boundary_rejects_malformed_symbol_input():
    sym, _, bucket = alpha_hunter.resolve_user_symbol("../../etc/passwd")
    assert sym == ""
    assert bucket == "INVALID"
    assert alpha_hunter.analyze_single_asset("<script>alert(1)</script>") is None
