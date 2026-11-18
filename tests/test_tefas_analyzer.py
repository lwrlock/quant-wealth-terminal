"""Unit tests for universal TEFAS fund analyzer, switch breakeven simulator, and Telegram routing."""

import os
import sys
import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import tefas_analyzer  # noqa: E402
import telegram_bot  # noqa: E402


def _mock_series(code: str, name: str, start_p: float, end_p: float, n: int = 250, dip_tail: bool = False):
    dates = [f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)]
    prices = [round(start_p + (end_p - start_p) * (i / max(1, n - 1)), 6) for i in range(n)]
    if dip_tail and n >= 20:
        for k in range(n - 6, n - 1):
            prices[k] = prices[k - 1] * 0.985
        prices[-1] = prices[-2] * 1.018
    return {
        "code": code,
        "name": name,
        "category": "Yerli Hisse" if code == "MAC" else ("Para Piyasası" if code == "ZBJ" else "Yabancı Hisse"),
        "dates": dates,
        "prices": prices,
        "price_map": dict(zip(dates, prices)),
    }


def test_hsyf_zero_stopaj_and_ppf_zero_valor():
    st_mac, val_mac = tefas_analyzer._infer_stopaj_and_valor("MAC", "Marmara Capital Hi\u0307sse Senedi\u0307 Fonu", "Yatırım Fonu")
    st_zbj, val_zbj = tefas_analyzer._infer_stopaj_and_valor("ZBJ", "Ziraat Portföy Başak Para Piyasası", "Para Piyasası")
    assert st_mac == 0.0
    assert val_mac == 2
    assert val_zbj == 0
    assert st_zbj >= 0.0


def test_analyze_tefas_fund_excess_vs_zbj(monkeypatch):
    mac = _mock_series("MAC", "Marmara Capital Hisse Senedi Fonu", 1.0, 1.65, 250)
    zbj = _mock_series("ZBJ", "Ziraat Başak PPF", 4.0, 5.60, 250)

    monkeypatch.setattr(
        tefas_analyzer,
        "_fetch_tefas_1y_series",
        lambda code, force_refresh=False: mac if code == "MAC" else (zbj if code == "ZBJ" else None),
    )
    res = tefas_analyzer.analyze_tefas_fund("MAC")
    assert res is not None
    assert res["code"] == "MAC"
    assert res["ret_1y_pct"] == pytest.approx(65.0, abs=0.1)
    assert res["zbj_1y_pct"] == pytest.approx(40.0, abs=0.1)
    assert res["excess_vs_zbj_1y_pct"] == pytest.approx(25.0, abs=0.2)
    assert res["stopaj_pct"] == 0.0


def test_switch_simulator_rejects_slower_target(monkeypatch):
    fast = _mock_series("AFT", "Ak Portföy Yeni Teknolojiler", 1.0, 1.55, 250)
    slow = _mock_series("YAY", "Yapı Kredi Yabancı Teknoloji", 1.0, 1.15, 250)
    zbj = _mock_series("ZBJ", "Ziraat Başak PPF", 4.0, 5.4, 250)
    mapping = {"AFT": fast, "YAY": slow, "ZBJ": zbj}
    monkeypatch.setattr(tefas_analyzer, "_fetch_tefas_1y_series", lambda c, force_refresh=False: mapping.get(c))
    monkeypatch.setattr(tefas_analyzer.db, "get_all_funds", lambda: {})

    sim = tefas_analyzer.simulate_fund_switch("AFT", "YAY", amount_try=10000.0)
    assert sim is not None
    assert sim["breakeven_days"] is None
    assert "GEÇME" in sim["verdict_tr"]


def test_switch_simulator_computes_breakeven_days_when_faster(monkeypatch):
    slow = _mock_series("AFT", "Ak Portföy Yeni Teknolojiler", 1.0, 1.05, 250)
    fast = _mock_series("YAY", "Yapı Kredi Yabancı Teknoloji", 1.0, 1.80, 250)
    zbj = _mock_series("ZBJ", "Ziraat Başak PPF", 4.0, 5.4, 250)
    mapping = {"AFT": slow, "YAY": fast, "ZBJ": zbj}
    monkeypatch.setattr(tefas_analyzer, "_fetch_tefas_1y_series", lambda c, force_refresh=False: mapping.get(c))
    monkeypatch.setattr(tefas_analyzer.db, "get_all_funds", lambda: {})

    sim = tefas_analyzer.simulate_fund_switch("AFT", "YAY", amount_try=20000.0, valor_days=2)
    assert sim is not None
    assert sim["breakeven_days"] is not None
    assert sim["breakeven_days"] > 0
    assert sim["total_switch_cost_try"] > 0


def test_telegram_fon_and_gecis_commands(monkeypatch):
    monkeypatch.setattr(
        tefas_analyzer,
        "format_tefas_fund_telegram",
        lambda c: f"FON_OK:{c.upper()}",
    )
    monkeypatch.setattr(
        tefas_analyzer,
        "format_fund_switch_telegram",
        lambda f, t, amount_try=None: f"GECIS_OK:{f.upper()}->{t.upper()}:{amount_try}",
    )
    assert telegram_bot.process_incoming_text(12345, "/fon mac") == "FON_OK:MAC"
    assert telegram_bot.process_incoming_text(12345, "/gecis aft yay 15000") == "GECIS_OK:AFT->YAY:15000.0"


def test_boundary_rejects_invalid_tefas_code():
    assert tefas_analyzer.analyze_tefas_fund("../bad") is None
    assert tefas_analyzer.simulate_fund_switch("AFT", "AFT") is None
