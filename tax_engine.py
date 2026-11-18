import json
from typing import Any, Dict, List, Optional

import db
import market_service

# Türkiye güncel TEFAS fon stopaj oranları (%) — Kullanıcı ayarlardan güncelleyebilir
DEFAULT_STOPAJ_RATES: Dict[str, float] = {
    "ZBJ": 15.0,  # Para Piyasası Fonu
    "TZL": 15.0,  # Para Piyasası Fonu
    "TCA": 15.0,  # Altın / Kıymetli Maden Katılım Fonu
    "AFT": 15.0,  # Yabancı Hisse / Değişken
    "KZL": 15.0,  # Kıymetli Maden / Altın
    "TI3": 0.0,   # Hisse Senedi Yoğun Fon (HSYF - Stopaj Muaf %0)
}

FUND_TAX_LABELS: Dict[str, str] = {
    "ZBJ": "Para Piyasası Fonu (Kâr üzerinden stopaj)",
    "TZL": "Para Piyasası Fonu (Kâr üzerinden stopaj)",
    "TCA": "Altın Katılım Fonu (Kâr üzerinden stopaj)",
    "AFT": "Yabancı Hisse Sepeti Fonu (Kâr üzerinden stopaj)",
    "KZL": "Altın Katılım Fonu (Kâr üzerinden stopaj)",
    "TI3": "Hisse Senedi Yoğun Fon (HSYF Muafiyet %0)",
}


def get_stopaj_rates() -> Dict[str, float]:
    rates = dict(DEFAULT_STOPAJ_RATES)
    raw = db.get_setting("tax_stopaj_rates", "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    rates[str(k).upper().strip()] = max(0.0, min(50.0, float(v)))
        except Exception:
            pass
    return rates


def save_stopaj_rates(updates: Dict[str, Any]) -> Dict[str, float]:
    rates = get_stopaj_rates()
    for k, v in updates.items():
        try:
            rates[str(k).upper().strip()] = max(0.0, min(50.0, float(v)))
        except Exception:
            pass
    db.set_setting("tax_stopaj_rates", json.dumps(rates, ensure_ascii=False), sync_cloud=True)
    return rates


def calculate_fund_tax_breakdown(
    code: str,
    shares: float,
    buy_price: float,
    current_price: float,
    stopaj_rates: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    norm_code = str(code or "").upper().strip()
    rates = stopaj_rates if stopaj_rates is not None else get_stopaj_rates()
    stopaj_pct = float(rates.get(norm_code, 15.0))

    sh = max(0.0, float(shares or 0.0))
    bp = max(0.0, float(buy_price or 0.0))
    cp = max(0.0, float(current_price or bp))

    cost_basis = round(sh * bp, 2)
    gross_value = round(sh * cp, 2)
    gross_profit = round(gross_value - cost_basis, 2)

    # Stopaj sadece gerçekleşen pozitif kâr üzerinden kesilir
    tax_try = round(max(0.0, gross_profit) * (stopaj_pct / 100.0), 2)
    net_exit_value = round(gross_value - tax_try, 2)
    net_profit_try = round(gross_profit - tax_try, 2)
    net_profit_pct = round((net_profit_try / cost_basis * 100.0), 2) if cost_basis > 0 else 0.0

    return {
        "code": norm_code,
        "label": FUND_TAX_LABELS.get(norm_code, "TEFAS Yatırım Fonu"),
        "stopaj_pct": round(stopaj_pct, 2),
        "cost_basis_try": cost_basis,
        "gross_value_try": gross_value,
        "gross_profit_try": gross_profit,
        "estimated_tax_try": tax_try,
        "net_exit_value_try": net_exit_value,
        "net_profit_try": net_profit_try,
        "net_profit_pct": net_profit_pct,
    }


def build_portfolio_tax_summary(
    funds_detail: Optional[List[Dict[str, Any]]] = None,
    market: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rates = get_stopaj_rates()
    if market is None:
        market = market_service.fetch_live_market()
    mkt_funds = market.get("funds", {})

    if funds_detail is None:
        funds = db.get_all_funds()
        funds_detail = []
        for code, item in funds.items():
            sh = float(item.get("shares", 0.0))
            bp = float(item.get("buy_price", 0.0))
            cp = float(mkt_funds.get(code, {}).get("price", bp))
            funds_detail.append({"code": code, "shares": sh, "buy_price": bp, "current_price": cp})

    per_fund: List[Dict[str, Any]] = []
    tot_gross_val = 0.0
    tot_cost = 0.0
    tot_tax = 0.0

    for f in funds_detail:
        br = calculate_fund_tax_breakdown(
            f.get("code", ""),
            f.get("shares", 0.0),
            f.get("buy_price", 0.0),
            f.get("current_price", f.get("buy_price", 0.0)),
            stopaj_rates=rates,
        )
        per_fund.append(br)
        tot_gross_val += br["gross_value_try"]
        tot_cost += br["cost_basis_try"]
        tot_tax += br["estimated_tax_try"]

    tot_gross_profit = round(tot_gross_val - tot_cost, 2)
    tot_net_value = round(tot_gross_val - tot_tax, 2)
    tot_net_profit = round(tot_gross_profit - tot_tax, 2)
    tot_net_pct = round((tot_net_profit / tot_cost * 100.0), 2) if tot_cost > 0 else 0.0

    macro = market.get("macro", {})
    usd_try = max(1.0, float(macro.get("usd_try") or 50.0))
    gram_altin = max(1.0, float(macro.get("gram_altin_try") or 6000.0))
    usd_30d = float(macro.get("usd_monthly_chg") or 0.0)
    altin_30d = float(macro.get("altin_monthly_chg") or 0.0)

    # Mevcut PPF ortalama net aylık getirisi (stopaj düşülmüş tahmin)
    ppf_daily_gross = market_service.get_live_ppf_daily_rate()
    ppf_30d_gross_pct = ((1.0 + ppf_daily_gross) ** 30 - 1.0) * 100.0
    ppf_stopaj = float(rates.get("ZBJ", 15.0)) / 100.0
    ppf_30d_net_pct = round(ppf_30d_gross_pct * (1.0 - ppf_stopaj), 2)

    return {
        "rates": rates,
        "funds": per_fund,
        "totals": {
            "cost_basis_try": round(tot_cost, 2),
            "gross_value_try": round(tot_gross_val, 2),
            "gross_profit_try": tot_gross_profit,
            "estimated_tax_try": round(tot_tax, 2),
            "net_exit_value_try": tot_net_value,
            "net_profit_try": tot_net_profit,
            "net_profit_pct": tot_net_pct,
            "net_value_usd": round(tot_net_value / usd_try, 2),
            "net_value_gold_gr": round(tot_net_value / gram_altin, 3),
        },
        "benchmarks": {
            "ppf_30d_gross_pct": round(ppf_30d_gross_pct, 2),
            "ppf_30d_net_pct": ppf_30d_net_pct,
            "usd_30d_pct": round(usd_30d, 2),
            "altin_30d_pct": round(altin_30d, 2),
            "ppf_beats_usd": ppf_30d_net_pct > usd_30d,
        },
    }
