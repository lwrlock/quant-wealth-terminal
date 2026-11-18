"""
Universal TEFAS Fund Analyzer & Switch Breakeven Simulator (`tefas_analyzer.py`)

Allows analyzing any 3-letter TEFAS fund (e.g. MAC, IIH, YAY, KZL, TTE) against ZBJ
risk-free benchmark and simulating whether switching from an existing fund to a rival
is worth paying stopaj tax + T+N valor waiting days.
"""

import datetime
from decimal import Decimal, ROUND_HALF_UP
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from tefas import Crawler
except Exception:
    Crawler = None  # type: ignore[assignment]

try:
    import db
    import market_service
    import tax_engine
except ImportError:
    from finans_dashboard import db, market_service, tax_engine  # type: ignore[no-redef]


_FUND_LOCK = threading.Lock()
_FUND_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SEC = 1800.0  # 30 minutes
_VALID_FUND_RE = re.compile(r"^[A-Z0-9]{2,6}$")


def _money_try(val: Any) -> float:
    try:
        d = Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(d)
    except Exception:
        return 0.0


def _fmt_date_tr(iso_str: str) -> str:
    if len(iso_str) >= 10 and iso_str[4] == "-" and iso_str[7] == "-":
        return f"{iso_str[8:10]}.{iso_str[5:7]}.{iso_str[0:4]}"
    return iso_str


def _normalize_tr_upper(text: str) -> str:
    return (
        str(text or "")
        .replace("\u0307", "")
        .replace("İ", "I")
        .replace("ı", "I")
        .replace("Ş", "S")
        .replace("ş", "S")
        .replace("Ğ", "G")
        .replace("ğ", "G")
        .replace("Ü", "U")
        .replace("ü", "U")
        .replace("Ö", "O")
        .replace("ö", "O")
        .replace("Ç", "C")
        .replace("ç", "C")
        .upper()
    )


def _infer_stopaj_and_valor(code: str, name: str, category: str) -> Tuple[float, int]:
    rates = tax_engine.get_stopaj_rates()
    c_up = code.upper().strip()
    upper = _normalize_tr_upper(f"{c_up} {name} {category}")
    if c_up in rates:
        stopaj = float(rates[c_up])
    elif "HISSE" in upper and "YABANCI" not in upper and "TEKNOLOJI" not in upper and "AMERIKA" not in upper:
        stopaj = 0.0  # Yerli Hisse Senedi Yoğun Fon (HSYF - %0 Stopaj)
    else:
        stopaj = 15.0

    is_ppf = ("PARA PIYASASI" in upper or "BASAK" in upper or "LIKIT" in upper or c_up in ("ZBJ", "TZL"))
    valor = 0 if is_ppf else 2
    return stopaj, valor


def _fetch_tefas_1y_series(code: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    norm = str(code or "").upper().strip()
    if not _VALID_FUND_RE.match(norm):
        return None
    now_ts = time.time()
    if not force_refresh:
        with _FUND_LOCK:
            cached = _FUND_CACHE.get(norm)
            if cached and (now_ts - cached[0]) < _CACHE_TTL_SEC:
                return cached[1]

    if Crawler is None:
        return None

    try:
        today = market_service.now_tr().date()
        start_s = (today - datetime.timedelta(days=375)).strftime("%Y-%m-%d")
        end_s = (today + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        df = Crawler().fetch(start=start_s, end=end_s, name=norm)
        if df is None or df.empty or "price" not in df.columns:
            return None
        if "date" in df.columns:
            df = df.sort_values("date")
        df = df[df["price"].astype(float) > 0.0]
        if df.empty:
            return None

        dates: List[str] = []
        prices: List[float] = []
        for _, row in df.iterrows():
            d_s = str(row.get("date", ""))[:10]
            p_v = float(row.get("price") or 0.0)
            if d_s and p_v > 0:
                dates.append(d_s)
                prices.append(round(p_v, 6))

        if len(prices) < 5:
            return None

        raw_title = str(df["title"].iloc[-1]).strip() if ("title" in df.columns and df["title"].iloc[-1]) else norm
        clean_title = raw_title.title() if raw_title.isupper() else raw_title
        cat = market_service._infer_fund_category(norm, clean_title)

        out = {
            "code": norm,
            "name": clean_title,
            "category": cat,
            "dates": dates,
            "prices": prices,
            "price_map": dict(zip(dates, prices)),
        }
        with _FUND_LOCK:
            _FUND_CACHE[norm] = (now_ts, out)
        return out
    except Exception:
        pass

    # Fallback: SQLite quant_deep_history_cache veya stale bellek önbelleği (Render IP / TEFAS kesintilerine karşı)
    try:
        import json as _json
        raw_q = db.get_setting("quant_deep_history_cache")
        if raw_q:
            parsed_q = _json.loads(raw_q)
            f_entry = (parsed_q.get("funds") or {}).get(norm)
            if isinstance(f_entry, dict):
                q_dates = [str(d)[:10] for d in (f_entry.get("dates") or []) if d]
                q_prices = [round(float(p), 6) for p in (f_entry.get("prices") or []) if float(p or 0) > 0]
                if len(q_dates) >= 5 and len(q_dates) == len(q_prices):
                    cat = market_service._infer_fund_category(norm, norm)
                    out = {
                        "code": norm,
                        "name": norm,
                        "category": cat,
                        "dates": q_dates,
                        "prices": q_prices,
                        "price_map": dict(zip(q_dates, q_prices)),
                    }
                    with _FUND_LOCK:
                        _FUND_CACHE[norm] = (now_ts, out)
                    return out
    except Exception:
        pass
    with _FUND_LOCK:
        stale = _FUND_CACHE.get(norm)
        if stale:
            return stale[1]
    return None


def _ret_over_bars(prices: List[float], bars: int) -> float:
    if len(prices) < 2:
        return 0.0
    idx = max(0, len(prices) - 1 - bars)
    base = prices[idx]
    return round(((prices[-1] / base) - 1.0) * 100.0, 2) if base > 0 else 0.0


def _backtest_tefas_dip_bounce(prices: List[float]) -> Dict[str, Any]:
    """Tests how often a >=3% dip followed by an up day gained over next 20 trading days."""
    n = len(prices)
    if n < 50:
        return {"trades": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0}
    arr = np.array(prices, dtype=float)
    rets: List[float] = []
    i = 21
    while i < n - 21:
        hi20 = float(np.max(arr[i - 20 : i]))
        dd = (arr[i] / hi20 - 1.0) * 100.0 if hi20 > 0 else 0.0
        turn_up = arr[i] > arr[i - 1]
        if dd <= -3.0 and turn_up:
            fwd = (arr[i + 20] / arr[i] - 1.0) * 100.0
            rets.append(float(fwd))
            i += 15  # cooldown so overlapping days aren't double counted
            continue
        i += 1
    if not rets:
        return {"trades": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0}
    a = np.array(rets, dtype=float)
    return {
        "trades": len(rets),
        "win_rate_pct": round(float(np.mean(a > 0) * 100.0), 1),
        "avg_return_pct": round(float(np.mean(a)), 2),
    }


def analyze_tefas_fund(raw_code: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """Fetches up to 1Y TEFAS history for any fund code and compares it vs ZBJ."""
    code = str(raw_code or "").upper().strip()
    ser = _fetch_tefas_1y_series(code, force_refresh=force_refresh)
    if not ser:
        return None

    prices = ser["prices"]
    dates = ser["dates"]
    last_p = prices[-1]
    prev_p = prices[-2] if len(prices) >= 2 else last_p
    last_date_tr = _fmt_date_tr(dates[-1])

    ret_1d = _ret_over_bars(prices, 1)
    ret_7d = _ret_over_bars(prices, 5)
    ret_30d = _ret_over_bars(prices, 21)
    ret_90d = _ret_over_bars(prices, 63)
    ret_1y = round(((last_p / prices[0]) - 1.0) * 100.0, 2) if prices[0] > 0 else 0.0

    # Peak & dip info over last ~90 trading days (or full available window)
    win_start = max(0, len(prices) - 90)
    win = prices[win_start:]
    peak_local_idx = int(np.argmax(win))
    peak_idx = win_start + peak_local_idx
    peak_p = prices[peak_idx]
    dd_peak_pct = round(((last_p / peak_p) - 1.0) * 100.0, 2) if peak_p > 0 else 0.0
    peak_date_tr = _fmt_date_tr(dates[peak_idx])

    # ZBJ risk-free comparison over exact overlapping dates
    zbj_ret_1y = 0.0
    zbj_ret_30d = 0.0
    zbj_ser = ser if code == "ZBJ" else _fetch_tefas_1y_series("ZBJ", force_refresh=False)
    if zbj_ser:
        zmap = zbj_ser["price_map"]
        common = [d for d in dates if d in zmap]
        if len(common) >= 2:
            zbj_ret_1y = round(((zmap[common[-1]] / zmap[common[0]]) - 1.0) * 100.0, 2)
            idx30 = max(0, len(common) - 22)
            zbj_ret_30d = round(((zmap[common[-1]] / zmap[common[idx30]]) - 1.0) * 100.0, 2)

    excess_1y = round(ret_1y - zbj_ret_1y, 2)
    stopaj_pct, valor_days = _infer_stopaj_and_valor(code, ser["name"], ser["category"])
    dip_bt = _backtest_tefas_dip_bounce(prices)

    # Human verdict
    is_ppf = valor_days == 0
    if is_ppf:
        verdict_tr = "🛡 GÜVENLİ GÜNLÜK KAZANÇ"
        plain_reason = f"Her gece risksiz büyüyen para piyasası fonu. Son 30 günde +%{ret_30d:.2f}, son 1 yılda +%{ret_1y:.2f} getirdi."
    elif dd_peak_pct <= -4.0 and last_p <= prev_p:
        verdict_tr = "🛑 DÜŞMEYE DEVAM EDİYOR (Dönüş Bekle)"
        plain_reason = f"{peak_date_tr} tarihinden beri %{abs(dd_peak_pct):.1f} düştü ama bugün hâlâ eksi/yatay ({ret_1d:+.2f}%). Yeşile dönene kadar bekle."
    elif dd_peak_pct <= -3.5 and last_p > prev_p and excess_1y >= -2.0:
        verdict_tr = "🟢 DİPTEN TOPARLANIYOR (Alınabilir)"
        bt_txt = (
            f" Son 1 yılda böyle {dip_bt['trades']} düşüşte %{dip_bt['win_rate_pct']}'inde sonraki ayda yükselmiş."
            if dip_bt["trades"] >= 2 else ""
        )
        plain_reason = f"{peak_date_tr}'den beri %{abs(dd_peak_pct):.1f} düşmüştü, {last_date_tr} itibarıyla yukarı döndü (+%{ret_1d:.2f}).{bt_txt}"
    elif excess_1y < -5.0 and len(prices) >= 120:
        verdict_tr = "⚠️ RİSKSİZ FAİZİN (ZBJ) GERİSİNDE"
        plain_reason = f"Son dönemde %{ret_1y:+.1f} getirmiş ama ZBJ (%{zbj_ret_1y:+.1f}) bu fona {abs(excess_1y):.1f} puan fark atmış. Paranla risksiz kalmak daha mantıklı olmuş."
    else:
        verdict_tr = "👀 NORMAL SEVİYEDE (Kademeli İzle)"
        beat_txt = f"ZBJ'yi +{excess_1y:.1f} puan geçmiş." if excess_1y >= 0 else f"ZBJ'nin {abs(excess_1y):.1f} puan gerisinde."
        plain_reason = f"{peak_date_tr} zirvesine göre %{abs(dd_peak_pct):.1f} aşağıda. {beat_txt}"

    return {
        "status": "ok",
        "code": code,
        "name": ser["name"],
        "category": ser["category"],
        "last_date_tr": last_date_tr,
        "bars_count": len(prices),
        "price": round(last_p, 6),
        "ret_1d_pct": ret_1d,
        "ret_7d_pct": ret_7d,
        "ret_30d_pct": ret_30d,
        "ret_90d_pct": ret_90d,
        "ret_1y_pct": ret_1y,
        "zbj_1y_pct": zbj_ret_1y,
        "zbj_30d_pct": zbj_ret_30d,
        "excess_vs_zbj_1y_pct": excess_1y,
        "peak_date_tr": peak_date_tr,
        "drawdown_from_peak_pct": dd_peak_pct,
        "stopaj_pct": stopaj_pct,
        "valor_days": valor_days,
        "dip_backtest": dip_bt,
        "verdict_tr": verdict_tr,
        "plain_reason": plain_reason,
    }


def simulate_fund_switch(
    from_code: str,
    to_code: str,
    amount_try: Optional[float] = None,
    valor_days: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Calculates whether selling `from_code` to buy `to_code` pays off after:
      1. Realized profit stopaj tax on `from_code`
      2. Dead waiting days (T+N valor opportunity cost)
    using live TEFAS historical returns over identical dates.
    """
    fc = str(from_code or "").upper().strip()
    tc = str(to_code or "").upper().strip()
    if not fc or not tc or fc == tc:
        return None

    from_a = analyze_tefas_fund(fc)
    to_a = analyze_tefas_fund(tc)
    if not from_a or not to_a:
        return None

    # Check user's actual position in DB first
    db_funds = db.get_all_funds() or {}
    held = db_funds.get(fc) or {}
    held_shares = float(held.get("shares") or 0.0)
    held_bp = float(held.get("buy_price") or 0.0)

    if held_shares > 0 and amount_try is None:
        tax_br = tax_engine.calculate_fund_tax_breakdown(
            code=fc,
            shares=held_shares,
            buy_price=held_bp if held_bp > 0 else from_a["price"],
            current_price=from_a["price"],
        )
        gross_try = tax_br["gross_value_try"]
        stopaj_cost_try = tax_br["estimated_tax_try"]
        used_source = "Mevcut Portföyün"
    else:
        raw_amt = _money_try(amount_try) if amount_try is not None else 10000.0
        gross_try = max(100.0, raw_amt)
        # If hypothetical amount is evaluated without cost basis, assume 1 month's gain as taxable profit proxy
        est_gain = max(0.0, gross_try * (max(0.0, from_a["ret_30d_pct"]) / 100.0))
        stopaj_cost_try = _money_try(est_gain * (from_a["stopaj_pct"] / 100.0))
        used_source = f"Örnek ₺{gross_try:,.0f} Bütçe"

    net_after_tax = max(0.0, gross_try - stopaj_cost_try)
    eff_valor = int(valor_days) if valor_days is not None else max(from_a["valor_days"], 1 if to_a["valor_days"] > 0 else 0)
    ppf_daily = market_service.get_live_ppf_daily_rate()
    valor_cost_try = _money_try(net_after_tax * ppf_daily * eff_valor)
    total_switch_cost = _money_try(stopaj_cost_try + valor_cost_try)

    # Compare annualized/90d/30d weighted advantage of `to_code` over `from_code`
    diff_30d = round(to_a["ret_30d_pct"] - from_a["ret_30d_pct"], 2)
    diff_90d = round(to_a["ret_90d_pct"] - from_a["ret_90d_pct"], 2)
    diff_1y = round(to_a["ret_1y_pct"] - from_a["ret_1y_pct"], 2)

    # Conservative daily extra edge: blend 30D (60%) + 90D (40%) converted to calendar days
    daily_edge_pct = (0.60 * (diff_30d / 30.0)) + (0.40 * (diff_90d / 90.0))
    daily_edge_try = round(net_after_tax * (daily_edge_pct / 100.0), 2)

    if daily_edge_try <= 0.05:
        breakeven_days = None
        verdict = "🛑 GEÇME (Değmez)"
        summary = (
            f"{tc}, son 1–3 ayda {fc}'yi yeterince geçememiş (30G fark {diff_30d:+.2f} puan). "
            f"Üstüne ₺{total_switch_cost:,.2f} stopaj + valör kaybı ödersin. Elindekini koru."
        )
    else:
        breakeven_days = int(round(total_switch_cost / daily_edge_try)) if total_switch_cost > 0 else 1
        breakeven_days = max(1, breakeven_days)
        if breakeven_days <= 45:
            verdict = "🟢 GEÇİLEBİLİR"
            summary = (
                f"₺{total_switch_cost:,.2f} (stopaj + {eff_valor} gün valör) kaybını {tc} tahminen {breakeven_days} günde kapatır. "
                f"Uzun vade tutacaksan geçiş mantıklı."
            )
        elif breakeven_days <= 120:
            verdict = "🟡 SINIRDA (Yeni Alımda Düşün)"
            summary = (
                f"Geçiş masrafını (₺{total_switch_cost:,.2f}) kapatması yaklaşık {breakeven_days} gün sürüyor. "
                f"Elindekini bozmak yerine bundan sonraki yeni paranı {tc}'ye koymak daha kârlı."
            )
        else:
            verdict = "🛑 DEĞMEZ (Elindekinde Kal)"
            summary = (
                f"Stopaj ve {eff_valor} gün valör kaybını kapatması {breakeven_days} gün sürer. "
                f"Elindeki {fc}'yi bozmaya değmez."
            )

    return {
        "status": "ok",
        "from_code": fc,
        "to_code": tc,
        "basis_label": used_source,
        "gross_try": round(gross_try, 2),
        "stopaj_cost_try": stopaj_cost_try,
        "valor_days": eff_valor,
        "valor_cost_try": valor_cost_try,
        "total_switch_cost_try": total_switch_cost,
        "diff_30d_pct": diff_30d,
        "diff_90d_pct": diff_90d,
        "diff_1y_pct": diff_1y,
        "daily_extra_try": daily_edge_try,
        "breakeven_days": breakeven_days,
        "verdict_tr": verdict,
        "summary_tr": summary,
        "from_fund": from_a,
        "to_fund": to_a,
    }


def format_tefas_fund_telegram(raw_code: str) -> str:
    res = analyze_tefas_fund(raw_code)
    if not res:
        return f"⚠️ `{raw_code.upper()}` kodlu TEFAS fonu bulunamadı veya yeterli veri yok."

    lines = [
        f"🏦 *TEFAS FON KARNESİ: {res['code']}* ({res['last_date_tr']})",
        f"_{res['name']}_ · `{res['category']}`",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• *Karar:* *{res['verdict_tr']}*",
        f"• *Güncel Fiyat:* `₺{res['price']:.6f}` (`Bugün {res['ret_1d_pct']:+.2f}%`)",
        f"• *Performans:* `7G {res['ret_7d_pct']:+.1f}%` · `30G {res['ret_30d_pct']:+.1f}%` · `90G {res['ret_90d_pct']:+.1f}%` · `1Y {res['ret_1y_pct']:+.1f}%`",
        f"• *ZBJ (Risksiz Faiz) Kıyası:* `{res['excess_vs_zbj_1y_pct']:+.1f} puan` _(ZBJ 1Y: +%{res['zbj_1y_pct']:.1f})_",
        f"• *Zirveden Durum:* `{res['peak_date_tr']}` zirvesine göre `{res['drawdown_from_peak_pct']:+.1f}%`",
        f"• *Vergi / Bekleme:* Stopaj `%{res['stopaj_pct']:g}` · Valör `T+{res['valor_days']}`",
        "",
        f"💡 *Özet:* {res['plain_reason']}",
    ]
    return "\n".join(lines)


def format_fund_switch_telegram(from_code: str, to_code: str, amount_try: Optional[float] = None) -> str:
    sim = simulate_fund_switch(from_code, to_code, amount_try=amount_try)
    if not sim:
        return f"⚠️ `{from_code.upper()}` → `{to_code.upper()}` kıyaslaması için TEFAS verisi alınamadı."

    b_txt = f"`{sim['breakeven_days']} gün`" if sim["breakeven_days"] else "_Kapanmıyor_"
    lines = [
        f"🔄 *FON DEĞİŞİM HESABI: {sim['from_code']} → {sim['to_code']}*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"• *Karar:* *{sim['verdict_tr']}*",
        f"• *Hesaplanan Tutar:* `₺{sim['gross_try']:,.2f}` ({sim['basis_label']})",
        f"• *Çıkış Masrafın:* Stopaj `-₺{sim['stopaj_cost_try']:,.2f}` + {sim['valor_days']}g Valör `-₺{sim['valor_cost_try']:,.2f}` = *`-₺{sim['total_switch_cost_try']:,.2f}`*",
        f"• *Getiri Farkı:* `30G {sim['diff_30d_pct']:+.2f}` puan · `90G {sim['diff_90d_pct']:+.2f}` puan · `1Y {sim['diff_1y_pct']:+.2f}` puan",
        f"• *Amorti Süresi:* {b_txt}",
        "",
        f"💡 *Yorum:* {sim['summary_tr']}",
    ]
    return "\n".join(lines)
