import datetime
import json
import threading
import time
from typing import Dict, Any, Optional

try:
    from tefas import Crawler
except ImportError:
    Crawler = None

try:
    import yfinance as yf
except ImportError:
    yf = None

TR_TZ = datetime.timezone(datetime.timedelta(hours=3))


def now_tr() -> datetime.datetime:
    """Sunucu UTC'de (örn. Render) çalışsa bile her zaman Türkiye saatini (UTC+3) döndürür."""
    return datetime.datetime.now(TR_TZ).replace(tzinfo=None)


_market_lock = threading.Lock()
_refresh_lock = threading.Lock()
_poller_started = False
_snapshot_loaded = False

PPF_ACCRUAL_CODES = {"ZBJ", "TZL"}
_TR_HOLIDAYS = {(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)}

# Resmi KAP / TEFAS Fon İşlem Adedi ve Katı Kuralları (Ziraat Mobil uyumlu)
FUND_LOT_STEPS: Dict[str, int] = {
    "TZL": 100,   # Ziraat Para Piyasası: En az 100 pay ve 100'ün katları
    "TCA": 500,   # Ziraat Altın Katılım: En az 500 pay ve 500'ün katları
    "ZBJ": 1,     # Ziraat Başak Para Piyasası: 1 pay ve katları
    "AFT": 1,     # Ak Portföy Yeni Teknolojiler: 1 pay ve katları
    "KZL": 1,     # Kuveyt Türk Altın Katılım (TEFAS A grubu): 1 pay ve katları
    "TI3": 1,     # İş Portföy BIST30: 1 pay ve katları
}


def get_fund_lot_step(code: str) -> int:
    """Fonun resmi TEFAS/KAP minimum işlem adedi & katını döndürür (örn: TZL=100, TCA=500, ZBJ=1)."""
    return max(1, int(FUND_LOT_STEPS.get(str(code or "").upper().strip(), 1) or 1))


def compute_fund_order_math(
    code: str,
    amount_try: float,
    unit_price: float,
    explicit_shares: float = 0.0,
    is_sell: bool = False,
    held_shares: Optional[float] = None,
) -> Dict[str, Any]:
    """
    TEFAS / Ziraat Mobil tam lot yuvarlama ve artan küsurat matematiği:
    - Alımda tutar verilip pay boşsa: `floor((tutar + 1e-9) / fiyat / pay_kati) * pay_kati`
      hesaplar; sadece `shares * unit_price` düşülür, artan küsurat kasada kalır.
    - Alımda hem tutar hem açık pay verilmişse (banka dekontu/valör bloke): tutar ve pay
      birebir korunur, gerçekleşen birim fiyat `tutar / pay` olarak kaydedilir.
    - Satımda: pay katına yuvarlanır (veya eldeki toplam payın tamamı çıkış yapılıyorsa korunur).
    """
    norm_code = str(code or "").upper().strip()
    lot_step = get_fund_lot_step(norm_code)
    amt = max(0.0, float(amount_try or 0.0))
    px = float(unit_price or 0.0)
    exp_sh = max(0.0, float(explicit_shares or 0.0))

    if px <= 0 and not (exp_sh > 0 and amt > 0):
        return {
            "ok": False,
            "error": f"{norm_code} fonu için geçerli birim fiyat bulunamadı.",
            "shares": 0.0,
            "effective_amt": 0.0,
            "exec_price": 0.0,
            "leftover_try": amt,
            "lot_step": lot_step,
        }

    if not is_sell:
        if exp_sh > 0:
            final_shares = round(exp_sh, 4)
            if amt > 0:
                effective_amt = round(amt, 2)
                exec_price = round(effective_amt / final_shares, 6)
            else:
                effective_amt = round(final_shares * px, 2)
                exec_price = round(px, 6)
            leftover = 0.0
        else:
            raw_units = int((amt + 1e-9) / px)
            lot_units = (raw_units // lot_step) * lot_step
            if lot_units <= 0:
                min_try = round(lot_step * px, 2)
                return {
                    "ok": False,
                    "error": f"{norm_code} fonu en az {lot_step:,} pay ve katları şeklinde alınabilir (1 lot ≈ ₺{min_try:,.2f}).",
                    "shares": 0.0,
                    "effective_amt": 0.0,
                    "exec_price": round(px, 6),
                    "leftover_try": round(amt, 2),
                    "lot_step": lot_step,
                }
            final_shares = float(lot_units)
            effective_amt = round(final_shares * px, 2)
            if effective_amt > amt > 0:
                # 1 pay fiyatı kuruş yuvarlamasından dolayı bütçeyi 1 kuruş aşarsa güvenli sınırla
                effective_amt = round(amt, 2)
            exec_price = round(px, 6)
            leftover = round(max(0.0, amt - effective_amt), 2)

        return {
            "ok": True,
            "error": "",
            "shares": final_shares,
            "effective_amt": effective_amt,
            "exec_price": exec_price,
            "leftover_try": leftover,
            "lot_step": lot_step,
        }

    # SATIM matematiği
    cur_sh = max(0.0, float(held_shares or 0.0))
    if cur_sh <= 0:
        return {
            "ok": False,
            "error": f"Portföyde satılacak {norm_code} payı bulunamadı.",
            "shares": 0.0,
            "effective_amt": 0.0,
            "exec_price": round(px, 6),
            "leftover_try": 0.0,
            "lot_step": lot_step,
        }

    if exp_sh > 0:
        final_shares = round(min(exp_sh, cur_sh), 4)
        effective_amt = round(amt if (amt > 0 and abs(amt - final_shares * px) < 0.5) else (final_shares * px), 2)
        exec_price = round(effective_amt / final_shares, 6) if final_shares > 0 else round(px, 6)
    else:
        raw_units = int((amt + 1e-9) / px)
        lot_units = (raw_units // lot_step) * lot_step
        if lot_units <= 0:
            if cur_sh < lot_step:
                # Portföydeki pay miktarı 1 lot altındaysa tam çıkışa izin ver
                final_shares = round(cur_sh, 4)
            else:
                min_try = round(lot_step * px, 2)
                return {
                    "ok": False,
                    "error": f"{norm_code} satışı için en az {lot_step:,} pay (~₺{min_try:,.2f}) gerekir (Mevcut: {cur_sh:,.0f} pay).",
                    "shares": 0.0,
                    "effective_amt": 0.0,
                    "exec_price": round(px, 6),
                    "leftover_try": 0.0,
                    "lot_step": lot_step,
                }
        else:
            final_shares = float(min(lot_units, cur_sh))
        effective_amt = round(final_shares * px, 2)
        exec_price = round(px, 6)

    return {
        "ok": True,
        "error": "",
        "shares": final_shares,
        "effective_amt": effective_amt,
        "exec_price": exec_price,
        "leftover_try": 0.0,
        "lot_step": lot_step,
    }


def _is_tr_biz_day(d: datetime.date) -> bool:
    return d.weekday() < 5 and (d.month, d.day) not in _TR_HOLIDAYS


def _next_tr_biz_day(d: datetime.date, steps: int = 1) -> datetime.date:
    cur = d
    if steps <= 0:
        while not _is_tr_biz_day(cur):
            cur += datetime.timedelta(days=1)
        return cur
    rem = steps
    while rem > 0:
        cur += datetime.timedelta(days=1)
        if _is_tr_biz_day(cur):
            rem -= 1
    return cur


_BANK_CAL_CACHE: Dict[str, Any] = {"ts": 0.0, "data": {}}


def _get_bank_calibrations() -> Dict[str, Dict[str, Any]]:
    """Ziraat Mobil'den doğrulanan hedef valör tarihi birim fiyatlarını döner."""
    now_ts = time.time()
    if (now_ts - float(_BANK_CAL_CACHE.get("ts") or 0.0)) < 5.0 and isinstance(_BANK_CAL_CACHE.get("data"), dict):
        return _BANK_CAL_CACHE["data"]
    data: Dict[str, Dict[str, Any]] = {}
    try:
        import db
        raw = db.get_setting("ppf_bank_calibration")
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
    except Exception:
        pass
    _BANK_CAL_CACHE["ts"] = now_ts
    _BANK_CAL_CACHE["data"] = data
    return data


def save_bank_calibration(
    code: str,
    target_date: str,
    price: float,
    official_price: Optional[float] = None,
    gap_days: int = 0,
    push_cloud: bool = True,
) -> Dict[str, Any]:
    """Dekont veya banka ekranından doğrulanan valör fiyatını ve günlük efektif getiriyi kaydeder."""
    fcode = str(code or "").upper().strip()
    px = round(float(price or 0.0), 6)
    if not fcode or px <= 0:
        return {}
    tdate = str(target_date or "")[:10]
    cur_map = dict(_get_bank_calibrations())
    prev_entry = dict(cur_map.get(fcode) or {})
    daily_rate = prev_entry.get("daily_rate")
    if official_price and float(official_price) > 0 and gap_days > 0:
        eff_r = (px / float(official_price)) ** (1.0 / gap_days) - 1.0
        if 0.0001 <= eff_r <= 0.0035:
            daily_rate = round(eff_r, 6)
    entry = {
        "target_date": tdate,
        "price": px,
        "daily_rate": daily_rate,
        "updated_at": now_tr().strftime("%Y-%m-%d %H:%M:%S"),
    }
    cur_map[fcode] = entry
    try:
        import db
        db.set_setting("ppf_bank_calibration", json.dumps(cur_map, ensure_ascii=False))
        if push_cloud:
            db.schedule_cloud_sync()
    except Exception:
        pass
    _BANK_CAL_CACHE["ts"] = time.time()
    _BANK_CAL_CACHE["data"] = cur_map
    return entry


def _calc_calendar_daily_rate(prices_by_date: Dict[str, float], gap_days: int = 1) -> float:
    """TEFAS tarihli fiyat haritasından gerçek takvim günü bazlı günlük nema oranını hesaplar."""
    if not prices_by_date or len(prices_by_date) < 2:
        return 0.0
    sorted_dates = sorted(prices_by_date.keys())
    last_d_str = sorted_dates[-1]
    last_p = float(prices_by_date.get(last_d_str) or 0.0)
    if last_p <= 0:
        return 0.0
    try:
        last_d = datetime.date.fromisoformat(last_d_str[:10])
    except Exception:
        return 0.0

    # Son ~4-7 takvim gününü tercih et; hafta sonu (gap_days>=2) ise önceki hafta sonu adımı + son günle dengele
    ref_d = None
    ref_p = 0.0
    for d_str in reversed(sorted_dates[:-1]):
        try:
            cand_d = datetime.date.fromisoformat(d_str[:10])
        except Exception:
            continue
        span = (last_d - cand_d).days
        if 3 <= span <= 10 and float(prices_by_date.get(d_str) or 0.0) > 0:
            ref_d = cand_d
            ref_p = float(prices_by_date[d_str])
            if span <= 7:
                break
    if ref_d is None:
        first_d_str = sorted_dates[0]
        try:
            ref_d = datetime.date.fromisoformat(first_d_str[:10])
            ref_p = float(prices_by_date[first_d_str])
        except Exception:
            return 0.0

    days_diff = (last_d - ref_d).days
    if days_diff <= 0 or ref_p <= 0:
        return 0.0
    daily_rate = (last_p / ref_p) ** (1.0 / days_diff) - 1.0

    if gap_days >= 2 and len(sorted_dates) >= 4:
        weekend_rate = 0.0
        for i in range(len(sorted_dates) - 1, 0, -1):
            try:
                d_hi = datetime.date.fromisoformat(sorted_dates[i][:10])
                d_lo = datetime.date.fromisoformat(sorted_dates[i - 1][:10])
                p_hi = float(prices_by_date[sorted_dates[i]] or 0.0)
                p_lo = float(prices_by_date[sorted_dates[i - 1]] or 0.0)
                sp = (d_hi - d_lo).days
                if sp >= 2 and p_hi > 0 and p_lo > 0:
                    wr = (p_hi / p_lo) ** (1.0 / sp) - 1.0
                    if 0.0001 <= wr <= 0.0035:
                        weekend_rate = wr
                        break
            except Exception:
                continue
        last_step_rate = 0.0
        try:
            d_p = datetime.date.fromisoformat(sorted_dates[-2][:10])
            p_p = float(prices_by_date[sorted_dates[-2]] or 0.0)
            sp1 = (last_d - d_p).days
            if sp1 >= 1 and p_p > 0:
                lr = (last_p / p_p) ** (1.0 / sp1) - 1.0
                if 0.0001 <= lr <= 0.0035:
                    last_step_rate = lr
        except Exception:
            pass
        if weekend_rate > 0 and last_step_rate > 0:
            daily_rate = (weekend_rate + last_step_rate) / 2.0
        elif weekend_rate > 0:
            daily_rate = (daily_rate + weekend_rate) / 2.0

    if 0.0001 <= daily_rate <= 0.0035:
        return round(daily_rate, 6)
    return 0.0


def _apply_ppf_evening_accrual(code: str, item: Dict[str, Any], now_dt: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """ZBJ/TZL için banka akşam/hafta sonu valör nemasını TEFAS geciktiğinde güncel tutar."""
    out = dict(item)
    official_p = float(out.get("price") or 0.0)
    out["official_price"] = official_p
    out["accrued_price_estimated"] = False
    out["accrued_gap_days"] = 0
    out["accrued_target_date"] = out.get("tefas_date") or ""

    fcode = code.upper().strip()
    if fcode not in PPF_ACCRUAL_CODES or official_p <= 0:
        return out

    tefas_date_str = str(out.get("tefas_date") or "")[:10]
    if len(tefas_date_str) != 10:
        return out
    try:
        tefas_d = datetime.date.fromisoformat(tefas_date_str)
    except Exception:
        return out

    if now_dt is None:
        now_dt = now_tr()
    today_d = now_dt.date()
    # Hafta içi 18:00+ veya hafta sonu Ziraat Mobil bir sonraki iş günü (valör) nemasını yansıtır
    if (not _is_tr_biz_day(today_d)) or (now_dt.hour >= 18):
        target_d = _next_tr_biz_day(today_d, 1 if _is_tr_biz_day(today_d) else 0)
    else:
        target_d = today_d

    if tefas_d >= target_d:
        return out

    gap_days = min(5, (target_d - tefas_d).days)
    target_iso = target_d.isoformat()
    cal_entry = _get_bank_calibrations().get(fcode) or {}
    if cal_entry.get("target_date") == target_iso and float(cal_entry.get("price") or 0.0) > 0:
        out["price"] = round(float(cal_entry["price"]), 6)
        out["accrued_price_estimated"] = True
        out["accrued_gap_days"] = gap_days
        out["accrued_target_date"] = target_iso
        out["accrued_price_source"] = "Ziraat Eşitlendi"
        return out

    pbd = out.get("prices_by_date") or {}
    cal_rate = _calc_calendar_daily_rate(pbd, gap_days=gap_days) if pbd else float(out.get("calendar_daily_rate") or 0.0)
    if cal_rate <= 0:
        fallback_pct = float(out.get("avg_daily_nema_pct") or 0.0)
        if 0.03 <= fallback_pct <= 0.25:
            cal_rate = fallback_pct / 100.0
    learned_rate = float(cal_entry.get("daily_rate") or 0.0)
    if 0.0001 <= learned_rate <= 0.0035 and cal_rate > 0:
        cal_rate = round(cal_rate * 0.65 + learned_rate * 0.35, 6)
    if gap_days <= 0 or cal_rate <= 0:
        return out

    proj_p = round(official_p * ((1.0 + cal_rate) ** gap_days), 6)
    out["price"] = proj_p
    out["accrued_price_estimated"] = True
    out["accrued_gap_days"] = gap_days
    out["accrued_target_date"] = target_iso
    return out

_market_data: Dict[str, Any] = {
    "macro": {
        "usd_try": 50.30,
        "usd_chg_pct": 0.0,
        "usd_weekly_pct": 0.0,
        "eur_try": 55.69,
        "eur_chg_pct": 0.0,
        "gram_altin_try": 6278.0,
        "altin_s1_try": 67.8,
        "ons_altin_usd": 3881.7,
        "ons_chg_pct": 0.0,
        "ons_weekly_pct": 0.0,
        "bist100": 11666.4,
        "bist100_chg_pct": 0.0,
        "bist100_weekly_pct": 0.0,
        "nasdaq100": 28289.2,
        "nasdaq_chg_pct": 0.0,
        "nasdaq_weekly_pct": 0.0,
    },
    "funds": {
        "ZBJ": {
            "code": "ZBJ", "name": "Ziraat Başak Para Piyasası", "price": 5.630101,
            "daily_yield_pct": 0.10, "weekly_yield_pct": 0.70, "monthly_yield_pct": 3.05,
            "avg_30d": 5.55, "high_30d": 5.630101, "low_30d": 5.46,
            "dist_from_avg_pct": 1.4, "drawdown_from_high_pct": 0.0, "avg_daily_nema_pct": 0.10,
            "category_rank": None, "category_total": None, "tefas_date": "",
            "category": "Para Piyasası (Başak)", "risk": 1,
        },
        "TZL": {
            "code": "TZL", "name": "Ziraat Para Piyasası", "price": 0.149766,
            "daily_yield_pct": 0.10, "weekly_yield_pct": 0.70, "monthly_yield_pct": 3.00,
            "avg_30d": 0.1475, "high_30d": 0.149766, "low_30d": 0.145,
            "dist_from_avg_pct": 1.5, "drawdown_from_high_pct": 0.0, "avg_daily_nema_pct": 0.10,
            "category_rank": None, "category_total": None, "tefas_date": "",
            "category": "Para Piyasası", "risk": 1,
        },
        "TCA": {
            "code": "TCA", "name": "Ziraat Altın Katılım", "price": 0.755613,
            "daily_yield_pct": -0.69, "weekly_yield_pct": 0.0, "monthly_yield_pct": 0.0,
            "avg_30d": 0.755613, "high_30d": 0.76, "low_30d": 0.73,
            "dist_from_avg_pct": 0.0, "drawdown_from_high_pct": -0.5, "avg_daily_nema_pct": 0.0,
            "category_rank": None, "category_total": None, "tefas_date": "",
            "category": "Kıymetli Madenler", "risk": 3,
        },
        "AFT": {
            "code": "AFT", "name": "Ak Portföy Yeni Teknolojiler", "price": 0.899322,
            "daily_yield_pct": 0.0, "weekly_yield_pct": 0.0, "monthly_yield_pct": 0.0,
            "avg_30d": 0.899322, "high_30d": 0.91, "low_30d": 0.85,
            "dist_from_avg_pct": 0.0, "drawdown_from_high_pct": -1.0, "avg_daily_nema_pct": 0.0,
            "category_rank": None, "category_total": None, "tefas_date": "",
            "category": "Yabancı Hisse", "risk": 6,
        },
        "KZL": {
            "code": "KZL", "name": "Kuveyt Türk Altın", "price": 27.505949,
            "daily_yield_pct": 0.0, "weekly_yield_pct": 0.0, "monthly_yield_pct": 0.0,
            "avg_30d": 27.505949, "high_30d": 27.8, "low_30d": 26.5,
            "dist_from_avg_pct": 0.0, "drawdown_from_high_pct": -1.0, "avg_daily_nema_pct": 0.0,
            "category_rank": None, "category_total": None, "tefas_date": "",
            "category": "Kıymetli Madenler", "risk": 3,
        },
        "TI3": {
            "code": "TI3", "name": "İş Portföy BIST30", "price": 0.097398,
            "daily_yield_pct": 0.0, "weekly_yield_pct": 0.0, "monthly_yield_pct": 0.0,
            "avg_30d": 0.097398, "high_30d": 0.10, "low_30d": 0.09,
            "dist_from_avg_pct": 0.0, "drawdown_from_high_pct": -2.0, "avg_daily_nema_pct": 0.0,
            "category_rank": None, "category_total": None, "tefas_date": "",
            "category": "Yerli Hisse", "risk": 6,
        },
    },
    "data_quality": {
        "tefas_live_count": 0,
        "macro_live": False,
        "source_status": "Başlangıç önbelleği",
    },
    "updated_at": now_tr().strftime("%H:%M:%S"),
}


def _load_persisted_snapshot_once():
    global _snapshot_loaded
    if _snapshot_loaded:
        return
    _snapshot_loaded = True
    try:
        import db
        raw = db.get_setting("last_market_snapshot")
        if not raw:
            return
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return
        with _market_lock:
            if isinstance(parsed.get("macro"), dict):
                _market_data["macro"].update(parsed["macro"])
            if isinstance(parsed.get("funds"), dict):
                for code, fvals in parsed["funds"].items():
                    if code in _market_data["funds"] and isinstance(fvals, dict):
                        _market_data["funds"][code].update(fvals)
            if parsed.get("updated_at"):
                _market_data["updated_at"] = parsed["updated_at"]
            _market_data["data_quality"]["source_status"] = "Son doğrulanmış önbellek yüklendi"
    except Exception:
        pass


def _save_persisted_snapshot():
    try:
        import db
        with _market_lock:
            payload = {
                "macro": dict(_market_data["macro"]),
                "funds": {k: dict(v) for k, v in _market_data["funds"].items()},
                "updated_at": _market_data["updated_at"],
            }
        db.set_setting("last_market_snapshot", json.dumps(payload, ensure_ascii=False), sync_cloud=False)
    except Exception:
        pass


def _calc_pct(new_val: float, old_val: float) -> float:
    if old_val and old_val > 0:
        return round(((new_val - old_val) / old_val) * 100.0, 2)
    return 0.0


def _fetch_yf_series(symbol: str, period: str = "14d") -> Optional[Dict[str, Any]]:
    if yf is None:
        return None
    try:
        hist = yf.Ticker(symbol).history(period=period)
        if hist is None or hist.empty:
            return None
        s_close = hist["Close"].dropna()
        closes = [float(x) for x in s_close.tolist() if float(x) > 0]
        if not closes:
            return None
        series_pts = []
        for idx_val, val in zip(s_close.index.tolist()[-14:], s_close.tolist()[-14:]):
            if float(val) > 0:
                d_str = str(idx_val)[:10]
                series_pts.append({"date": d_str, "price": round(float(val), 4)})
        last_v = closes[-1]
        prev_v = closes[-2] if len(closes) >= 2 else last_v
        week_ref = closes[-6] if len(closes) >= 6 else closes[0]
        return {
            "last": last_v,
            "daily_chg_pct": _calc_pct(last_v, prev_v),
            "weekly_chg_pct": _calc_pct(last_v, week_ref),
            "series": series_pts,
        }
    except Exception:
        return None


def _infer_fund_category(code: str, name: str) -> str:
    upper = f"{code} {name}".upper()
    if any(k in upper for k in ("PARA PİYASASI", "PARA PIYASASI", "BAŞAK", "BASAK", "LİKİT", "LIKIT")):
        return "Para Piyasası"
    if any(k in upper for k in ("ALTIN", "KIYMETLİ MADEN", "KIYMETLI MADEN", "GÜMÜŞ", "GUMUS")):
        return "Kıymetli Madenler"
    if any(k in upper for k in ("TEKNOLOJİ", "TEKNOLOJI", "YABANCI", "NASDAQ", "AMERİKA", "AMERIKA")):
        return "Yabancı Hisse"
    if any(k in upper for k in ("HİSSE", "HISSE", "BIST")):
        return "Yerli Hisse"
    return "Yatırım Fonu"


def _fetch_tefas_fund_stats(
    crawler: Any,
    fcode: str,
    start: str,
    end: str,
    existing_meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        df = crawler.fetch(start=start, end=end, name=fcode)
        if df is None or df.empty:
            return None
        if "date" in df.columns:
            df = df.sort_values("date")
        df = df[df["price"].astype(float) > 0]
        if df.empty:
            return None

        prices = [float(p) for p in df["price"].tolist()]
        last_p = prices[-1]
        prev_p = prices[-2] if len(prices) >= 2 else last_p
        week_p = prices[-6] if len(prices) >= 6 else prices[0]
        month_p = prices[-22] if len(prices) >= 22 else prices[0]

        recent_30 = prices[-22:]
        avg_30d = sum(recent_30) / len(recent_30)
        high_30d = max(recent_30)
        low_30d = min(recent_30)

        cat_rank = None
        cat_total = None
        if "category_rank" in df.columns and df["category_rank"].iloc[-1] is not None:
            try:
                cat_rank = int(df["category_rank"].iloc[-1])
            except Exception:
                pass
        if "category_total" in df.columns and df["category_total"].iloc[-1] is not None:
            try:
                cat_total = int(df["category_total"].iloc[-1])
            except Exception:
                pass

        tefas_date = str(df["date"].iloc[-1])[:10] if "date" in df.columns else ""
        prices_by_date: Dict[str, float] = {}
        if "date" in df.columns and "price" in df.columns:
            for _, row_df in df.tail(35).iterrows():
                d_str = str(row_df["date"])[:10]
                try:
                    p_val = float(row_df["price"])
                    if d_str and p_val > 0:
                        prices_by_date[d_str] = round(p_val, 6)
                except Exception:
                    pass

        cal_daily_rate = _calc_calendar_daily_rate(prices_by_date)
        avg_daily_nema = round(cal_daily_rate * 100.0, 4) if cal_daily_rate > 0 else round(_calc_pct(last_p, prev_p), 4)

        raw_title = ""
        if "title" in df.columns and df["title"].iloc[-1] is not None:
            raw_title = str(df["title"].iloc[-1]).strip()
        existing_name = (existing_meta or {}).get("name") or ""
        resolved_name = existing_name or raw_title or fcode
        resolved_cat = (existing_meta or {}).get("category") or _infer_fund_category(fcode, resolved_name)

        return {
            "code": fcode,
            "name": resolved_name,
            "category": resolved_cat,
            "risk": (existing_meta or {}).get("risk", 1 if "Para Piyasası" in resolved_cat else 4),
            "price": round(last_p, 6),
            "daily_yield_pct": _calc_pct(last_p, prev_p),
            "weekly_yield_pct": _calc_pct(last_p, week_p),
            "monthly_yield_pct": _calc_pct(last_p, month_p),
            "avg_30d": round(avg_30d, 6),
            "high_30d": round(high_30d, 6),
            "low_30d": round(low_30d, 6),
            "dist_from_avg_pct": _calc_pct(last_p, avg_30d),
            "drawdown_from_high_pct": _calc_pct(last_p, high_30d),
            "avg_daily_nema_pct": avg_daily_nema,
            "calendar_daily_rate": cal_daily_rate,
            "category_rank": cat_rank,
            "category_total": cat_total,
            "tefas_date": tefas_date,
            "prices_by_date": prices_by_date,
        }
    except Exception:
        return None


def _collect_tracked_fund_codes() -> list:
    base = ["TZL", "ZBJ", "TCA", "AFT", "KZL", "TI3"]
    extra = []
    try:
        import db
        db_funds = db.get_all_funds() or {}
        extra.extend([str(c).upper().strip() for c in db_funds.keys() if c])
        pending = db.get_pending_orders("BEKLIYOR") or []
        extra.extend([str(po.get("code", "")).upper().strip() for po in pending if po.get("code")])
    except Exception:
        pass
    seen = set()
    ordered = []
    for c in base + extra:
        if c and c != "TRY" and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def ensure_fund_in_market(fcode: str, fallback_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Kullanıcı portföyünde yer almayan yeni bir TEFAS fon kodu girdiğinde canlı TEFAS fiyatını çeker."""
    code = str(fcode or "").upper().strip()
    if not code or code == "TRY":
        return None
    _load_persisted_snapshot_once()
    with _market_lock:
        if code in _market_data["funds"]:
            return dict(_market_data["funds"][code])
    if Crawler is None:
        return None
    try:
        crawler = Crawler()
        today = now_tr().date()
        start = (today - datetime.timedelta(days=38)).strftime("%Y-%m-%d")
        end = (today + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        stats = _fetch_tefas_fund_stats(crawler, code, start, end, {"name": fallback_name} if fallback_name else None)
        if stats:
            with _market_lock:
                _market_data["funds"][code] = stats
            _save_persisted_snapshot()
            return dict(stats)
    except Exception:
        pass
    return None


def refresh_live_market_now() -> bool:
    """TEFAS ve küresel piyasa verilerini hemen senkronize eder (eşzamanlı çağrılara kilitli)."""
    _load_persisted_snapshot_once()
    with _refresh_lock:
        try:
            new_macro: Dict[str, Any] = {}
            usd_s = _fetch_yf_series("USDTRY=X", period="14d")
            eur_s = _fetch_yf_series("EURTRY=X", period="14d")
            gold_s = _fetch_yf_series("GC=F", period="14d")
            bist_s = _fetch_yf_series("XU100.IS", period="14d")
            ndx_s = _fetch_yf_series("^NDX", period="14d")

            if usd_s:
                new_macro["usd_try"] = round(usd_s["last"], 2)
                new_macro["usd_chg_pct"] = usd_s["daily_chg_pct"]
                new_macro["usd_weekly_pct"] = usd_s["weekly_chg_pct"]
                if usd_s.get("series"):
                    new_macro["usd_series"] = usd_s["series"]
            if eur_s:
                new_macro["eur_try"] = round(eur_s["last"], 2)
                new_macro["eur_chg_pct"] = eur_s["daily_chg_pct"]
            if gold_s:
                new_macro["ons_altin_usd"] = round(gold_s["last"], 2)
                new_macro["ons_chg_pct"] = gold_s["daily_chg_pct"]
                new_macro["ons_weekly_pct"] = gold_s["weekly_chg_pct"]
                if gold_s.get("series"):
                    new_macro["ons_series"] = gold_s["series"]

            u_for_gram = new_macro.get("usd_try") or _market_data["macro"]["usd_try"]
            g_for_gram = new_macro.get("ons_altin_usd") or _market_data["macro"]["ons_altin_usd"]
            if (usd_s or gold_s) and u_for_gram > 0 and g_for_gram > 0:
                gram = (g_for_gram / 31.1035) * u_for_gram
                new_macro["gram_altin_try"] = round(gram, 2)
                new_macro["altin_s1_try"] = round(gram / 100.0 * 1.08, 2)
                if gold_s and gold_s.get("series"):
                    usd_by_date = {x["date"]: x["price"] for x in (usd_s or {}).get("series", [])}
                    gram_pts = []
                    for pt in gold_s["series"]:
                        u_day = usd_by_date.get(pt["date"], u_for_gram)
                        gram_pts.append({"date": pt["date"], "price": round((pt["price"] / 31.1035) * u_day, 2)})
                    new_macro["gram_altin_series"] = gram_pts
                    if len(gram_pts) >= 2:
                        new_macro["gram_altin_chg_pct"] = _calc_pct(gram_pts[-1]["price"], gram_pts[-2]["price"])
                        new_macro["gram_altin_weekly_pct"] = _calc_pct(gram_pts[-1]["price"], gram_pts[max(0, len(gram_pts)-6)]["price"])

            if bist_s:
                new_macro["bist100"] = round(bist_s["last"], 1)
                new_macro["bist100_chg_pct"] = bist_s["daily_chg_pct"]
                new_macro["bist100_weekly_pct"] = bist_s["weekly_chg_pct"]
            if ndx_s:
                new_macro["nasdaq100"] = round(ndx_s["last"], 1)
                new_macro["nasdaq_chg_pct"] = ndx_s["daily_chg_pct"]
                new_macro["nasdaq_weekly_pct"] = ndx_s["weekly_chg_pct"]

            new_fund_stats: Dict[str, Dict[str, Any]] = {}
            if Crawler:
                try:
                    crawler = Crawler()
                    today = now_tr().date()
                    start = (today - datetime.timedelta(days=38)).strftime("%Y-%m-%d")
                    # Pazartesi/ileri valörlü TEFAS fiyatları Cuma gecesi veya hafta sonu yayınlanabildiği için +5 gün al
                    end = (today + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
                    with _market_lock:
                        existing_funds = {k: dict(v) for k, v in _market_data["funds"].items()}
                    for fcode in _collect_tracked_fund_codes():
                        stats = _fetch_tefas_fund_stats(crawler, fcode, start, end, existing_funds.get(fcode))
                        if stats:
                            new_fund_stats[fcode] = stats
                except Exception:
                    pass

            with _market_lock:
                if new_macro:
                    _market_data["macro"].update(new_macro)
                for fcode, stats in new_fund_stats.items():
                    if fcode in _market_data["funds"]:
                        _market_data["funds"][fcode].update(stats)
                    else:
                        _market_data["funds"][fcode] = stats
                tefas_cnt = len(new_fund_stats)
                macro_ok = bool(usd_s or gold_s or bist_s or ndx_s)
                _market_data["data_quality"] = {
                    "tefas_live_count": tefas_cnt,
                    "macro_live": macro_ok,
                    "source_status": "TEFAS + Küresel Piyasa Canlı" if (tefas_cnt > 0 and macro_ok) else (
                        "TEFAS Canlı" if tefas_cnt > 0 else ("Makro Canlı (TEFAS önbellek)" if macro_ok else "Önbellek verisi")
                    ),
                }
                _market_data["updated_at"] = now_tr().strftime("%H:%M:%S")

            if new_macro or new_fund_stats:
                _save_persisted_snapshot()
                return True
        except Exception:
            pass
        return False


def _bg_market_poller():
    """Arka planda çalışan asenkron piyasa tarayıcısı.
    HTTP endpoint'lerini asla bloklamaz; TEFAS (35 günlük seri) ve makro göstergeleri taze tutar."""
    _load_persisted_snapshot_once()
    while True:
        refresh_live_market_now()
        now_dt = now_tr()
        if now_dt.weekday() < 5 and 9 <= now_dt.hour <= 15:
            time.sleep(120)
        elif 16 <= now_dt.hour <= 23:
            time.sleep(180)
        else:
            time.sleep(600)


def start_market_poller():
    global _poller_started
    _load_persisted_snapshot_once()
    if _poller_started:
        return
    _poller_started = True
    t = threading.Thread(target=_bg_market_poller, daemon=True)
    t.start()


def fetch_live_market() -> Dict[str, Any]:
    _load_persisted_snapshot_once()
    now_dt = now_tr()
    weekday = now_dt.weekday()
    cur_time = now_dt.time()

    is_tefas_open = False
    is_tzl_open = False
    if weekday < 5:
        if datetime.time(9, 0) <= cur_time <= datetime.time(13, 30):
            is_tefas_open = True
            is_tzl_open = True
            session_title = "TEFAS Seansı Açık (Canlı Emir Saati)"
            session_badge = "Açık (09:00 - 13:30)"
        elif datetime.time(13, 30) < cur_time <= datetime.time(16, 0):
            is_tzl_open = True
            session_title = "TEFAS Kapandı • TZL Hâlâ Açık (16:00'ya kadar)"
            session_badge = "TZL Açık"
        else:
            session_title = "Seans Kapalı (Emirler Sabah 09:00'da İşlenir)"
            session_badge = "Kapalı (09:00)"
    else:
        session_title = "Hafta Sonu Kapalı (Emirler Pazartesi 09:00'da İşlenir)"
        session_badge = "Hafta Sonu"

    with _market_lock:
        funds_out = {}
        for k, v in _market_data["funds"].items():
            item = _apply_ppf_evening_accrual(k, v, now_dt=now_dt)
            p_map = item.get("prices_by_date") or {}
            if p_map:
                sorted_pts = [{"date": d, "price": p_map[d]} for d in sorted(p_map.keys())[-14:]]
            else:
                sorted_pts = []
            item["sparkline_points"] = sorted_pts
            item["lot_step"] = get_fund_lot_step(k)
            funds_out[k] = item
        return {
            "session": {
                "is_tefas_open": is_tefas_open,
                "is_tzl_open": is_tzl_open,
                "title": session_title,
                "badge": session_badge,
                "time_str": now_dt.strftime("%H:%M:%S"),
            },
            "macro": dict(_market_data["macro"]),
            "funds": funds_out,
            "data_quality": dict(_market_data["data_quality"]),
            "updated_at": _market_data["updated_at"],
        }


def get_live_fund_price(code: str, fallback_price: float = 1.0) -> float:
    _load_persisted_snapshot_once()
    with _market_lock:
        raw = _market_data["funds"].get(code.upper())
        if not raw:
            return fallback_price
        eff = _apply_ppf_evening_accrual(code.upper(), raw)
        return float(eff.get("price") or fallback_price)


def get_live_ppf_daily_rate() -> float:
    """ZBJ ve TZL fonlarının gerçek son 5 işlem günü ortalama günlük getiri oranını (örn. 0.0011) döndürür."""
    _load_persisted_snapshot_once()
    with _market_lock:
        zbj = _market_data["funds"].get("ZBJ", {})
        tzl = _market_data["funds"].get("TZL", {})
        candidates = []
        for f in (zbj, tzl):
            rate_pct = float(f.get("avg_daily_nema_pct") or f.get("daily_yield_pct") or 0.0)
            if 0.03 <= rate_pct <= 0.30:
                candidates.append(rate_pct / 100.0)
        if candidates:
            return round(sum(candidates) / len(candidates), 6)
    return 0.0011


def get_live_fund_snapshot() -> Dict[str, Dict[str, Any]]:
    _load_persisted_snapshot_once()
    with _market_lock:
        return {
            code: {
                "name": info["name"],
                "price": info["price"],
                "chg": info.get("daily_yield_pct", 0.0),
                "weekly_chg": info.get("weekly_yield_pct", 0.0),
                "monthly_chg": info.get("monthly_yield_pct", 0.0),
                "dist_from_avg_pct": info.get("dist_from_avg_pct", 0.0),
                "drawdown_from_high_pct": info.get("drawdown_from_high_pct", 0.0),
                "avg_daily_nema_pct": info.get("avg_daily_nema_pct", 0.0),
                "category_rank": info.get("category_rank"),
                "category_total": info.get("category_total"),
                "tefas_date": info.get("tefas_date", ""),
                "lot_step": get_fund_lot_step(code),
                "prices_by_date": dict(info.get("prices_by_date") or {}),
            }
            for code, info in _market_data["funds"].items()
        }


def get_fund_price_on_date(code: str, target_date: str) -> Optional[Dict[str, Any]]:
    """TEFAS serisinden belirli bir iş günü (veya sonrası ilk iş günü) için kesinleşmiş fiyatı döndürür."""
    _load_persisted_snapshot_once()
    norm_code = str(code or "").upper().strip()
    if not target_date:
        return None
    with _market_lock:
        info = _market_data["funds"].get(norm_code, {})
        p_map = info.get("prices_by_date") or {}
        if target_date in p_map:
            return {"date": target_date, "price": float(p_map[target_date])}
        # Hedef tarihten sonra yayınlanan ilk iş günü (örneğin ekstra tatil olduysa)
        later = sorted(d for d in p_map.keys() if d >= target_date)
        if later:
            d0 = later[0]
            return {"date": d0, "price": float(p_map[d0])}
        # Eğer tefas_date hedefe ulaştıysa doğrudan güncel fiyatı kullan
        if info.get("tefas_date") and str(info.get("tefas_date")) >= target_date and float(info.get("price") or 0) > 0:
            return {"date": str(info.get("tefas_date")), "price": float(info["price"])}
    return None
