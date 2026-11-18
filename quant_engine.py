import datetime
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import numpy as np

try:
    from tefas import Crawler
except Exception:
    Crawler = None

try:
    import yfinance as yf
except Exception:
    yf = None

try:
    import db
    import market_service
except ImportError:
    from finans_dashboard import db, market_service


_DEEP_LOCK = threading.Lock()
_DEEP_CACHE: Optional[Dict[str, Any]] = None
_DEEP_CACHE_TS: float = 0.0
_DEEP_TTL_SEC: float = 1500.0  # 25 dakika önbellek
_SETTINGS_KEY = "quant_deep_history_cache"

_TEFAS_SYMBOLS = ["AFT", "TI3", "TCA", "KZL", "ZBJ", "TZL"]
_YF_SYMBOLS = {
    "NDX": "^NDX",
    "BIST100": "XU100.IS",
    "GOLD_USD": "GC=F",
    "USDTRY": "USDTRY=X",
    "VIX": "^VIX",
    "US10Y": "^TNX",
}


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return default


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    if len(arr) == 0:
        return arr
    alpha = 2.0 / (float(span) + 1.0)
    out = np.empty_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi_wilder(arr: np.ndarray, period: int = 14) -> float:
    if len(arr) < period + 2:
        return 50.0
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + float(gains[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(losses[i])) / period
    if avg_loss <= 1e-12:
        return 95.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _fetch_one_tefas(code: str, days_back: int = 300) -> Optional[Dict[str, Any]]:
    if Crawler is None:
        return None
    try:
        crawler = Crawler()
        today = market_service.now_tr().date()
        start = (today - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
        end = (today + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        df = crawler.fetch(start=start, end=end, name=code)
        if df is None or df.empty:
            return None
        if "date" in df.columns:
            df = df.sort_values("date")
        df = df[df["price"].astype(float) > 0]
        if df.empty:
            return None

        dates: List[str] = []
        prices: List[float] = []
        for _, row in df.iterrows():
            d_str = str(row.get("date", ""))[:10]
            p_val = _safe_float(row.get("price"), 0.0)
            if d_str and p_val > 0:
                dates.append(d_str)
                prices.append(round(p_val, 6))

        if not prices:
            return None

        cat_rank = None
        cat_total = None
        try:
            if "category_rank" in df.columns and df["category_rank"].iloc[-1] is not None:
                cat_rank = int(df["category_rank"].iloc[-1])
        except Exception:
            pass
        try:
            if "category_total" in df.columns and df["category_total"].iloc[-1] is not None:
                cat_total = int(df["category_total"].iloc[-1])
        except Exception:
            pass

        return {
            "dates": dates,
            "prices": prices,
            "category_rank": cat_rank,
            "category_total": cat_total,
        }
    except Exception:
        return None


def _fetch_one_yf(ticker: str, period: str = "1y") -> Optional[Dict[str, Any]]:
    if yf is None:
        return None
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if hist is None or hist.empty:
            return None
        s_close = hist["Close"].dropna()
        dates: List[str] = []
        prices: List[float] = []
        for idx_val, val in zip(s_close.index.tolist(), s_close.tolist()):
            p_val = _safe_float(val, 0.0)
            if p_val > 0:
                dates.append(str(idx_val)[:10])
                prices.append(round(p_val, 4))
        if not prices:
            return None
        return {"dates": dates, "prices": prices}
    except Exception:
        return None


def _load_db_deep_cache() -> Optional[Dict[str, Any]]:
    try:
        raw = db.get_setting(_SETTINGS_KEY)
        if not raw:
            return None
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("funds"):
            return parsed
    except Exception:
        pass
    return None


def _save_db_deep_cache(payload: Dict[str, Any]) -> None:
    try:
        db.set_setting(_SETTINGS_KEY, json.dumps(payload, ensure_ascii=False), sync_cloud=False)
    except Exception:
        pass


def get_deep_market_history(force: bool = False) -> Dict[str, Any]:
    """280+ günlük TEFAS ve 1 yıllık Yahoo Finance serilerini (VIX dahil) 25 dk önbellekle döndürür."""
    global _DEEP_CACHE, _DEEP_CACHE_TS

    now_mono = time.time()
    with _DEEP_LOCK:
        if not force and _DEEP_CACHE and (now_mono - _DEEP_CACHE_TS) < _DEEP_TTL_SEC:
            return _DEEP_CACHE

    # Soğuk başlangıçta önce yerel DB önbelleğini dene (eğer force=False ise)
    if not force:
        with _DEEP_LOCK:
            if _DEEP_CACHE is None:
                db_cached = _load_db_deep_cache()
                if db_cached:
                    cached_epoch = _safe_float(db_cached.get("fetched_epoch"), 0.0)
                    _DEEP_CACHE = db_cached
                    _DEEP_CACHE_TS = cached_epoch
                    if (now_mono - cached_epoch) < _DEEP_TTL_SEC:
                        return _DEEP_CACHE

    funds_hist: Dict[str, Any] = {}
    macro_hist: Dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        tefas_futs = {code: pool.submit(_fetch_one_tefas, code, 300) for code in _TEFAS_SYMBOLS}
        yf_futs = {key: pool.submit(_fetch_one_yf, sym, "1y") for key, sym in _YF_SYMBOLS.items()}

        for code, fut in tefas_futs.items():
            try:
                res = fut.result(timeout=12)
                if res:
                    funds_hist[code] = res
            except Exception:
                pass

        for key, fut in yf_futs.items():
            try:
                res = fut.result(timeout=12)
                if res:
                    macro_hist[key] = res
            except Exception:
                pass

    # Fallback: Eğer TEFAS erişilemezse market_service içindeki son 35 günlük seriyi kullan
    live_mkt = market_service.fetch_live_market()
    live_funds = live_mkt.get("funds", {})
    for code in _TEFAS_SYMBOLS:
        if code not in funds_hist:
            pmap = live_funds.get(code, {}).get("prices_by_date") or {}
            if pmap:
                sd = sorted(pmap.keys())
                funds_hist[code] = {
                    "dates": sd,
                    "prices": [round(_safe_float(pmap[d]), 6) for d in sd],
                    "category_rank": live_funds.get(code, {}).get("category_rank"),
                    "category_total": live_funds.get(code, {}).get("category_total"),
                }

    # Önceki önbellekten eksik kalan makro verileri koru
    prev = _DEEP_CACHE or _load_db_deep_cache() or {}
    for key, val in (prev.get("macro") or {}).items():
        if key not in macro_hist and val:
            macro_hist[key] = val
    for code, val in (prev.get("funds") or {}).items():
        if code not in funds_hist and val:
            funds_hist[code] = val

    payload = {
        "fetched_at": market_service.now_tr().strftime("%Y-%m-%d %H:%M:%S"),
        "fetched_epoch": time.time(),
        "funds": funds_hist,
        "macro": macro_hist,
    }
    with _DEEP_LOCK:
        _DEEP_CACHE = payload
        _DEEP_CACHE_TS = payload["fetched_epoch"]
    if funds_hist:
        _save_db_deep_cache(payload)
    return payload


def _compute_series_technicals(prices: List[float], dates: List[str]) -> Dict[str, Any]:
    arr = np.array([p for p in prices if p > 0], dtype=float)
    n = len(arr)
    if n < 5:
        last_v = float(arr[-1]) if n > 0 else 0.0
        return {
            "count": n,
            "last": last_v,
            "ret_1d_pct": 0.0,
            "ret_5d_pct": 0.0,
            "ret_20d_pct": 0.0,
            "ret_60d_pct": 0.0,
            "sma20": last_v,
            "sma50": last_v,
            "sma_long": last_v,
            "sma_long_window": n,
            "dist_sma50_pct": 0.0,
            "dist_sma_long_pct": 0.0,
            "ema20_slope_pct": 0.0,
            "rsi14": 50.0,
            "macd_hist": 0.0,
            "macd_hist_prev": 0.0,
            "zscore20": 0.0,
            "vol_20_ann_pct": 0.0,
            "vol_60_ann_pct": 0.0,
            "vol_ratio": 1.0,
            "drawdown_60d_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "proj_30d": {
                "p10_pct": -2.0,
                "p50_pct": 1.0,
                "p90_pct": 4.0,
                "prob_drop_5pct": 5.0,
                "prob_drop_10pct": 1.0,
            },
        }

    last_v = float(arr[-1])
    prev_v = float(arr[-2])
    p5 = float(arr[-6]) if n >= 6 else float(arr[0])
    p20 = float(arr[-21]) if n >= 21 else float(arr[0])
    p60 = float(arr[-61]) if n >= 61 else float(arr[0])

    ret_1d = ((last_v / prev_v) - 1.0) * 100.0
    ret_5d = ((last_v / p5) - 1.0) * 100.0
    ret_20d = ((last_v / p20) - 1.0) * 100.0
    ret_60d = ((last_v / p60) - 1.0) * 100.0

    w20 = min(20, n)
    w50 = min(50, n)
    w_long = min(200, n) if n >= 120 else n

    sma20 = float(np.mean(arr[-w20:]))
    sma50 = float(np.mean(arr[-w50:]))
    sma_long = float(np.mean(arr[-w_long:]))

    ema12 = _ema(arr, 12)
    ema26 = _ema(arr, 26)
    ema20 = _ema(arr, 20)
    macd_line = ema12 - ema26
    signal_line = _ema(macd_line, 9)
    hist_arr = macd_line - signal_line
    macd_hist = float(hist_arr[-1])
    macd_hist_prev = float(hist_arr[-2]) if len(hist_arr) >= 2 else macd_hist

    ema20_slope = ((float(ema20[-1]) / float(ema20[-6])) - 1.0) * 100.0 if n >= 6 and ema20[-6] > 0 else 0.0

    std20 = float(np.std(arr[-w20:], ddof=0))
    zscore20 = ((last_v - sma20) / std20) if std20 > 1e-12 else 0.0

    rsi14 = _rsi_wilder(arr, 14)

    log_rets = np.diff(np.log(arr))
    vol20_d = float(np.std(log_rets[-min(20, len(log_rets)):], ddof=0)) if len(log_rets) >= 2 else 0.0
    vol60_d = float(np.std(log_rets[-min(60, len(log_rets)):], ddof=0)) if len(log_rets) >= 2 else vol20_d
    vol20_ann = vol20_d * math.sqrt(252.0) * 100.0
    vol60_ann = vol60_d * math.sqrt(252.0) * 100.0
    vol_ratio = (vol20_d / vol60_d) if vol60_d > 1e-9 else 1.0

    high_60 = float(np.max(arr[-min(60, n):]))
    dd_60 = ((last_v / high_60) - 1.0) * 100.0 if high_60 > 0 else 0.0

    running_max = np.maximum.accumulate(arr)
    dd_series = (arr / running_max - 1.0) * 100.0
    max_dd = float(np.min(dd_series))

    # 21 işlem günü (~30 takvim günü) ileri projeksiyon
    horizon = 21
    drift_60 = float(np.mean(log_rets[-min(60, len(log_rets)):])) if len(log_rets) > 0 else 0.0
    drift_full = float(np.mean(log_rets)) if len(log_rets) > 0 else 0.0
    mu_d = 0.65 * drift_60 + 0.35 * drift_full
    sigma_d = max(vol20_d, vol60_d * 0.85, 1e-4)

    log_mean = (mu_d - 0.5 * (sigma_d ** 2)) * horizon
    log_std = sigma_d * math.sqrt(horizon)

    p10 = (math.exp(log_mean - 1.2816 * log_std) - 1.0) * 100.0
    p50 = (math.exp(log_mean) - 1.0) * 100.0
    p90 = (math.exp(log_mean + 1.2816 * log_std) - 1.0) * 100.0

    prob_drop_5 = _norm_cdf((math.log(0.95) - log_mean) / log_std) * 100.0
    prob_drop_10 = _norm_cdf((math.log(0.90) - log_mean) / log_std) * 100.0

    return {
        "count": n,
        "last_date": dates[-1] if dates else "",
        "last": round(last_v, 6),
        "ret_1d_pct": round(ret_1d, 2),
        "ret_5d_pct": round(ret_5d, 2),
        "ret_20d_pct": round(ret_20d, 2),
        "ret_60d_pct": round(ret_60d, 2),
        "sma20": round(sma20, 6),
        "sma50": round(sma50, 6),
        "sma_long": round(sma_long, 6),
        "sma_long_window": w_long,
        "dist_sma50_pct": round(((last_v / sma50) - 1.0) * 100.0, 2) if sma50 > 0 else 0.0,
        "dist_sma_long_pct": round(((last_v / sma_long) - 1.0) * 100.0, 2) if sma_long > 0 else 0.0,
        "ema20_slope_pct": round(ema20_slope, 2),
        "rsi14": rsi14,
        "macd_hist": round(macd_hist, 6),
        "macd_hist_prev": round(macd_hist_prev, 6),
        "zscore20": round(zscore20, 2),
        "vol_20_ann_pct": round(vol20_ann, 1),
        "vol_60_ann_pct": round(vol60_ann, 1),
        "vol_ratio": round(vol_ratio, 2),
        "drawdown_60d_pct": round(dd_60, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "proj_30d": {
            "p10_pct": round(p10, 1),
            "p50_pct": round(p50, 1),
            "p90_pct": round(p90, 1),
            "prob_drop_5pct": round(min(99.9, max(0.0, prob_drop_5)), 1),
            "prob_drop_10pct": round(min(99.9, max(0.0, prob_drop_10)), 1),
        },
    }


def _classify_fund_regime(
    code: str,
    tech: Dict[str, Any],
    vix_last: float,
    cat_rank: Optional[int] = None,
    cat_total: Optional[int] = None,
) -> Dict[str, Any]:
    # Para Piyasası fonları her zaman güvenli kalkan kabul edilir
    if code in ("ZBJ", "TZL"):
        return {
            "health_score": 96,
            "crash_risk_pct": 2,
            "state": "SAFE_HAVEN",
            "level": "GREEN",
            "badge": "Güvenli Nema Limanı",
            "action_short": "Krizlerde ana parayı burada park et",
            "reasons": [
                f"20G yıllık volatilite sadece %{tech['vol_20_ann_pct']:.1f} (neredeyse sıfır dalgalanma)",
                f"Son 20 iş günü getirisi %{tech['ret_20d_pct']:+.2f}",
            ],
        }

    score = 50
    reasons: List[str] = []

    dist50 = tech["dist_sma50_pct"]
    dist_long = tech["dist_sma_long_pct"]
    slope = tech["ema20_slope_pct"]
    rsi = tech["rsi14"]
    macd_h = tech["macd_hist"]
    macd_prev = tech["macd_hist_prev"]
    vol_r = tech["vol_ratio"]
    z20 = tech["zscore20"]
    ret20 = tech["ret_20d_pct"]

    if dist50 >= 0:
        score += 15
        reasons.append(f"Fiyat 50G ortalamanın %{dist50:+.1f} üzerinde (orta vade pozitif)")
    elif dist50 <= -3.0:
        score -= 14
        reasons.append(f"Fiyat 50G ortalamanın %{dist50:.1f} altında (orta vade kırıldı)")
    else:
        score -= 7
        reasons.append(f"Fiyat 50G ortalamanın hafif altında (%{dist50:.1f})")

    if dist_long >= 0:
        score += 13
    else:
        score -= 12
        reasons.append(f"Uzun vade ({tech['sma_long_window']}G) trendin %{dist_long:.1f} altında")

    if slope >= 0.4:
        score += 8
    elif slope <= -1.0:
        score -= 8
        reasons.append(f"20G trend eğimi aşağı dönmüş (%{slope:.2f})")

    if macd_h > 0 and macd_h >= macd_prev:
        score += 8
        reasons.append("MACD momentumu yukarı ivmeleniyor")
    elif macd_h < 0 and macd_h < macd_prev:
        score -= 8
        reasons.append("MACD satış baskısı hızlanıyor")

    if 46 <= rsi <= 68:
        score += 10
    elif rsi < 33:
        score -= 4
        reasons.append(f"RSI-14 aşırı satım bölgesinde ({rsi:.1f} — tepki potansiyeli var ama teyit bekle)")
    elif rsi < 46:
        score -= 6
        reasons.append(f"RSI-14 zayıf bölgede ({rsi:.1f})")
    elif rsi > 74:
        score -= 4
        reasons.append(f"RSI-14 aşırı ısınmış ({rsi:.1f} — düzeltme riski)")

    if code in ("AFT", "TI3") and vix_last > 0:
        if vix_last >= 30:
            score -= 22
            reasons.append(f"Küresel korku endeksi VIX panik seviyesinde ({vix_last:.1f})")
        elif vix_last >= 24:
            score -= 12
            reasons.append(f"VIX korku endeksi yüksek ({vix_last:.1f} — türbülans)")
        elif vix_last < 19:
            score += 6

    if vol_r >= 1.45:
        score -= 10
        reasons.append(f"Son 20G oynaklık normalin {vol_r:.2f}x katına sıçradı")

    if cat_rank and cat_total and cat_total >= 5:
        pct_pos = cat_rank / float(cat_total)
        if pct_pos <= 0.25:
            score += 5
        elif pct_pos >= 0.70:
            score -= 7
            reasons.append(f"TEFAS kategori sırası zayıflamış ({cat_rank}/{cat_total})")

    score = int(max(5, min(98, round(score))))
    crash_risk = int(max(4, min(96, round(100 - score + (8 if vol_r >= 1.4 else 0)))))

    # İndirim vs Düşen Bıçak ayrımı
    if dist_long > 0 and score >= 52 and (rsi <= 44 or z20 <= -1.1) and vol_r < 1.55 and vix_last < 26:
        state = "HEALTHY_DIP"
        level = "GREEN"
        badge = "İndirim Fırsatı (Ana Trend Sağlam)"
        action_short = "Ana trend bozulmadı; acele etmeden kademeli ekleme bölgesi"
    elif score < 38 or (dist50 < -2.0 and dist_long < 0 and macd_h < 0) or (vix_last >= 28 and dist50 < 0) or ret20 <= -8.5:
        state = "FALLING_KNIFE"
        level = "RED"
        badge = "Düşen Bıçak / Ayı Rejimi"
        action_short = "Yeni alımı DURDUR; ZBJ ve TCA korumasına geç"
    elif score < 62 or vix_last >= 22 or dist50 < 0:
        state = "TURBULENCE"
        level = "YELLOW"
        badge = "Dikkat / Türbülans"
        action_short = "Yeni büyüme alımını yarıya kıs (%25-30), fazlasını ZBJ'de beklet"
    else:
        state = "BULL_HEALTHY"
        level = "GREEN"
        badge = "Sağlıklı Yükseliş Trendi"
        action_short = "Planlanan %50–55 büyüme oranıyla devam edilebilir"

    if not reasons:
        reasons.append(f"Trend ve momentum dengeli (RSI {rsi:.1f}, 50G fark %{dist50:+.1f})")

    return {
        "health_score": score,
        "crash_risk_pct": crash_risk,
        "state": state,
        "level": level,
        "badge": badge,
        "action_short": action_short,
        "reasons": reasons[:4],
    }


def _compute_overall_regime(
    fund_analyses: Dict[str, Any],
    macro_tech: Dict[str, Any],
) -> Dict[str, Any]:
    aft = fund_analyses.get("AFT", {})
    ti3 = fund_analyses.get("TI3", {})
    tca = fund_analyses.get("TCA", {})

    vix_last = _safe_float(macro_tech.get("VIX", {}).get("last"), 19.0)
    tnx_last = _safe_float(macro_tech.get("US10Y", {}).get("last"), 4.2)

    aft_sc = _safe_float(aft.get("regime", {}).get("health_score"), 55.0)
    ti3_sc = _safe_float(ti3.get("regime", {}).get("health_score"), 55.0)
    tca_sc = _safe_float(tca.get("regime", {}).get("health_score"), 60.0)

    # Büyüme motoru sağlığı (AFT + TI3 ağırlıklı)
    growth_health = round(0.60 * aft_sc + 0.40 * ti3_sc, 1)
    overall_score = int(round(0.75 * growth_health + 0.25 * tca_sc))

    aft_lvl = aft.get("regime", {}).get("level", "GREEN")
    ti3_lvl = ti3.get("regime", {}).get("level", "GREEN")

    if (aft_lvl == "RED" and ti3_lvl == "RED") or overall_score < 40 or vix_last >= 29:
        level = "RED"
        title = "KIRMIZI ALARM: Ayı Rejimi / Çöküş Kalkanı Aktif"
        summary = (
            "Büyüme piyasalarında trend aşağı kırıldı veya küresel korku sert yükseldi. "
            "Yeni AFT/TI3 alımını durdurup aylık bütçeyi ZBJ (güvenli nema) + TCA (altın) tarafında korumaya almak en güvenli hamle."
        )
    elif aft_lvl == "RED" or ti3_lvl == "RED" or overall_score < 60 or vix_last >= 22:
        level = "YELLOW"
        title = "SARI UYARI: Türbülans — Büyüme Payını Yarıya Kıs"
        summary = (
            "Piyasalarda zayıflama veya belirsizlik sinyali var. Her ay koyduğun büyüme oranını %55 yerine "
            "%25–30 bandına çekip kalan parayı günlük faizli ZBJ'de bekletmek olası düşüşte seni korur."
        )
    else:
        level = "GREEN"
        title = "YEŞİL REJİM: Trend Sağlıklı (%50–55 Büyüme Planı Uygun)"
        summary = (
            "Büyüme trendleri ve küresel korku endeksi normal sınırlar içinde. "
            "Planladığın %55 Büyüme (AFT/TI3) · %25 Altın (TCA) · %20 PPF (ZBJ) stratejisiyle düzenli eklemeye devam edebilirsin."
        )

    return {
        "level": level,
        "overall_score": overall_score,
        "growth_health": growth_health,
        "title": title,
        "summary": summary,
        "vix": round(vix_last, 2),
        "us10y": round(tnx_last, 2),
    }


def _build_dynamic_allocation(
    regime: Dict[str, Any],
    budget_try: float,
    fund_analyses: Dict[str, Any],
    live_funds: Dict[str, Any],
) -> Dict[str, Any]:
    lvl = regime["level"]
    aft_h = _safe_float(fund_analyses.get("AFT", {}).get("regime", {}).get("health_score"), 50.0)
    ti3_h = _safe_float(fund_analyses.get("TI3", {}).get("regime", {}).get("health_score"), 50.0)

    if lvl == "RED":
        # Kriz modunda 0% yeni büyüme
        w_aft, w_ti3, w_tca, w_zbj = 0.0, 0.0, 25.0, 75.0
        rebalance_advice = (
            "Eldeki AFT/TI3 pozisyonların toplam portföyün %35'ini geçiyorsa kademeli olarak %25-30 azaltıp ZBJ'ye çekmen önerilir."
        )
    elif lvl == "YELLOW":
        # Zayıf piyasada toplam büyüme %28; güçlü olana daha fazla ağırlık ver
        growth_tot = 28.0
        if aft_h >= ti3_h + 10:
            w_aft, w_ti3 = 20.0, 8.0
        elif ti3_h >= aft_h + 10:
            w_aft, w_ti3 = 10.0, 18.0
        else:
            w_aft, w_ti3 = 18.0, 10.0
        w_tca, w_zbj = 32.0, round(100.0 - growth_tot - 32.0, 1)
        rebalance_advice = (
            "Büyüme payı yarıya indirildi (%28). Piyasa tekrar Yeşil rejime dönene kadar kalan %40 nakit ZBJ'de günlük nema kazanır."
        )
    else:
        # Yeşil modda %55 büyüme; en sağlıklı büyüme fonuna hafif öncelik
        if aft_h >= ti3_h + 8:
            w_aft, w_ti3 = 38.0, 17.0
        elif ti3_h >= aft_h + 8:
            w_aft, w_ti3 = 20.0, 35.0
        else:
            w_aft, w_ti3 = 35.0, 20.0
        w_tca, w_zbj = 25.0, 20.0
        rebalance_advice = "Ana trend sağlıklı; planlanan %55 büyüme / %45 koruma dengesi tam uygulanabilir."

    spec = [
        ("AFT", "Yabancı Teknoloji (Büyüme)", w_aft),
        ("TI3", "BIST 30 Hisse (Büyüme)", w_ti3),
        ("TCA", "Altın Katılım (Kalkan)", w_tca),
        ("ZBJ", "Başak PPF (Nema & Nakit Cephane)", w_zbj),
    ]
    rows: List[Dict[str, Any]] = []
    for code, label, pct in spec:
        amt = round(budget_try * (pct / 100.0), 2)
        price = _safe_float(live_funds.get(code, {}).get("price"), 1.0)
        shares = int(amt / price) if (price > 0 and amt >= price) else 0
        rows.append({
            "code": code,
            "label": label,
            "pct": pct,
            "amount_try": amt,
            "price": round(price, 6),
            "shares": shares,
        })

    return {
        "budget_try": round(budget_try, 2),
        "regime_level": lvl,
        "growth_total_pct": round(w_aft + w_ti3, 1),
        "shield_total_pct": round(w_tca + w_zbj, 1),
        "rebalance_advice": rebalance_advice,
        "items": rows,
    }


def _compute_stress_tests(
    holdings_val: Dict[str, float],
    ppf_daily_rate: float,
) -> List[Dict[str, Any]]:
    zbj_v = _safe_float(holdings_val.get("PPF"), 0.0)
    tca_v = _safe_float(holdings_val.get("TCA"), 0.0)
    aft_v = _safe_float(holdings_val.get("AFT"), 0.0)
    ti3_v = _safe_float(holdings_val.get("TI3"), 0.0)
    total_v = zbj_v + tca_v + aft_v + ti3_v + _safe_float(holdings_val.get("CASH"), 0.0)

    # AFT hem Nasdaq hem USD/TRY taşır; TCA hem Ons Altın hem USD/TRY taşır.
    scenarios = [
        {
            "name": "Hafif Düzeltme",
            "desc": "Nasdaq -%5 · BIST -%5 · Altın -%2 · Dolar %0",
            "ndx": -0.05, "bist": -0.05, "gold": -0.02, "usd": 0.0,
        },
        {
            "name": "Sert Düzeltme",
            "desc": "Nasdaq -%15 · BIST -%12 · Altın -%4 · Dolar +%1.5",
            "ndx": -0.15, "bist": -0.12, "gold": -0.04, "usd": 0.015,
        },
        {
            "name": "Büyük Ayı Şoku",
            "desc": "Nasdaq -%30 · BIST -%22 · Altın -%8 · Dolar +%3",
            "ndx": -0.30, "bist": -0.22, "gold": -0.08, "usd": 0.03,
        },
        {
            "name": "Küresel Panik + Kur Sıçraması",
            "desc": "Nasdaq -%20 · BIST -%18 · Altın +%4 · Dolar +%10 (Kur kalkanı)",
            "ndx": -0.20, "bist": -0.18, "gold": 0.04, "usd": 0.10,
        },
    ]

    daily_nema_try = zbj_v * max(0.0005, ppf_daily_rate)
    out: List[Dict[str, Any]] = []
    for sc in scenarios:
        aft_chg = (1.0 + sc["ndx"]) * (1.0 + sc["usd"]) - 1.0
        tca_chg = (1.0 + sc["gold"]) * (1.0 + sc["usd"]) - 1.0
        ti3_chg = sc["bist"]
        pnl_try = (aft_v * aft_chg) + (ti3_v * ti3_chg) + (tca_v * tca_chg)
        pct = (pnl_try / total_v * 100.0) if total_v > 0 else 0.0
        recovery_days = int(math.ceil(abs(pnl_try) / daily_nema_try)) if (pnl_try < 0 and daily_nema_try > 0.01) else 0
        out.append({
            "name": sc["name"],
            "desc": sc["desc"],
            "base_total_try": round(total_v, 2),
            "pnl_try": round(pnl_try, 2),
            "pnl_pct": round(pct, 2),
            "after_total_try": round(max(0.0, total_v + pnl_try), 2),
            "recovery_days_zbj": min(999, recovery_days),
        })
    return out


def _run_walkforward_backtest(deep_hist: Dict[str, Any]) -> Dict[str, Any]:
    funds = deep_hist.get("funds") or {}
    macro = deep_hist.get("macro") or {}
    aft_h = funds.get("AFT") or {}
    tca_h = funds.get("TCA") or {}
    zbj_h = funds.get("ZBJ") or {}
    vix_h = macro.get("VIX") or {}

    if not (aft_h.get("dates") and tca_h.get("dates") and zbj_h.get("dates")):
        return {"available": False, "reason": "Yeterli TEFAS geçmişi henüz yüklenemedi."}

    aft_map = dict(zip(aft_h["dates"], aft_h["prices"]))
    tca_map = dict(zip(tca_h["dates"], tca_h["prices"]))
    zbj_map = dict(zip(zbj_h["dates"], zbj_h["prices"]))
    vix_map = dict(zip(vix_h.get("dates", []), vix_h.get("prices", [])))

    common_dates = sorted(set(aft_map.keys()) & set(tca_map.keys()) & set(zbj_map.keys()))
    if len(common_dates) < 75:
        return {"available": False, "reason": f"Backtest için en az 75 ortak iş günü gerekli (mevcut: {len(common_dates)})."}

    aft_arr = np.array([aft_map[d] for d in common_dates], dtype=float)
    tca_arr = np.array([tca_map[d] for d in common_dates], dtype=float)
    zbj_arr = np.array([zbj_map[d] for d in common_dates], dtype=float)

    # VIX değerini önceki bilinen güne ileri doldur
    vix_vals: List[float] = []
    last_vix = 18.0
    for d in common_dates:
        if d in vix_map:
            last_vix = _safe_float(vix_map[d], last_vix)
        vix_vals.append(last_vix)

    start_idx = 55
    static_val = 100.0
    smart_val = 100.0
    ppf_val = 100.0

    static_curve = [100.0]
    smart_curve = [100.0]
    ppf_curve = [100.0]
    curve_dates = [common_dates[start_idx]]
    red_days = 0
    yellow_days = 0

    for t in range(start_idx + 1, len(common_dates)):
        # Sadece t-1'e kadar olan verilerle karar ver (lookahead yok)
        win_aft = aft_arr[:t]
        sma50_aft = float(np.mean(win_aft[-50:]))
        sma20_aft = float(np.mean(win_aft[-20:]))
        last_aft = float(win_aft[-1])
        rsi_aft = _rsi_wilder(win_aft[-40:], 14)
        vix_prev = vix_vals[t - 1]

        # Rejim kararı
        if (last_aft < sma50_aft and last_aft < sma20_aft and rsi_aft < 45) or vix_prev >= 27.0:
            w_g, w_t, w_p = 0.0, 0.25, 0.75
            red_days += 1
        elif last_aft < sma50_aft or vix_prev >= 22.0 or rsi_aft < 46:
            w_g, w_t, w_p = 0.28, 0.32, 0.40
            yellow_days += 1
        else:
            w_g, w_t, w_p = 0.55, 0.25, 0.20

        r_aft = (aft_arr[t] / aft_arr[t - 1]) - 1.0
        r_tca = (tca_arr[t] / tca_arr[t - 1]) - 1.0
        r_zbj = (zbj_arr[t] / zbj_arr[t - 1]) - 1.0

        static_ret = 0.55 * r_aft + 0.25 * r_tca + 0.20 * r_zbj
        smart_ret = w_g * r_aft + w_t * r_tca + w_p * r_zbj

        static_val *= (1.0 + static_ret)
        smart_val *= (1.0 + smart_ret)
        ppf_val *= (1.0 + r_zbj)

        static_curve.append(static_val)
        smart_curve.append(smart_val)
        ppf_curve.append(ppf_val)
        curve_dates.append(common_dates[t])

    def _max_dd(c: List[float]) -> float:
        a = np.array(c, dtype=float)
        peaks = np.maximum.accumulate(a)
        return round(float(np.min((a / peaks - 1.0) * 100.0)), 2)

    static_mdd = _max_dd(static_curve)
    smart_mdd = _max_dd(smart_curve)

    # Grafiği temiz tutmak için ~36 noktaya indirge
    step = max(1, len(curve_dates) // 36)
    idxs = list(range(0, len(curve_dates), step))
    if idxs[-1] != len(curve_dates) - 1:
        idxs.append(len(curve_dates) - 1)

    return {
        "available": True,
        "start_date": curve_dates[0],
        "end_date": curve_dates[-1],
        "trading_days": len(curve_dates) - 1,
        "yellow_days": yellow_days,
        "red_days": red_days,
        "static_return_pct": round(float(static_curve[-1] - 100.0), 2),
        "smart_return_pct": round(float(smart_curve[-1] - 100.0), 2),
        "ppf_return_pct": round(float(ppf_curve[-1] - 100.0), 2),
        "static_max_drawdown_pct": float(static_mdd),
        "smart_max_drawdown_pct": float(smart_mdd),
        "drawdown_saved_pct": round(float(smart_mdd - static_mdd), 2),
        "series": {
            "dates": [curve_dates[i] for i in idxs],
            "static": [round(static_curve[i], 2) for i in idxs],
            "smart": [round(smart_curve[i], 2) for i in idxs],
            "ppf": [round(ppf_curve[i], 2) for i in idxs],
        },
    }


def build_quant_radar(
    budget_try: float = 6000.0,
    stress_mode: str = "live",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Tüm çok-faktörlü tahmin, erken uyarı, dinamik bütçe reçetesi, TL stres testi
    ve gerçek walk-forward backtest özetini döndürür.
    """
    deep_hist = get_deep_market_history(force=force_refresh)
    live_mkt = market_service.fetch_live_market()
    live_funds = live_mkt.get("funds", {})
    ppf_rate = market_service.get_live_ppf_daily_rate()

    macro_tech: Dict[str, Any] = {}
    for key, s in (deep_hist.get("macro") or {}).items():
        macro_tech[key] = _compute_series_technicals(s.get("prices", []), s.get("dates", []))

    vix_last = _safe_float(macro_tech.get("VIX", {}).get("last"), 19.0)

    fund_names = {
        "AFT": "Ak Portföy Yeni Teknolojiler (Nasdaq/USD)",
        "TI3": "İş Portföy BIST 30 (Yerli Büyük Hisse)",
        "TCA": "Ziraat Altın Katılım (Kalkan)",
        "KZL": "Kuveyt Türk Altın (Alternatif Kalkan)",
        "ZBJ": "Ziraat Başak PPF (Güvenli Günlük Kazanç)",
        "TZL": "Ziraat Para Piyasası (Anlık Likit)",
    }

    fund_analyses: Dict[str, Any] = {}
    for code in _TEFAS_SYMBOLS:
        s = (deep_hist.get("funds") or {}).get(code) or {}
        prices = s.get("prices") or []
        dates = s.get("dates") or []
        tech = _compute_series_technicals(prices, dates)
        # Canlı fiyatı market_service üzerinden tazele
        lf = live_funds.get(code, {})
        if _safe_float(lf.get("price"), 0.0) > 0:
            tech["live_price"] = round(_safe_float(lf["price"]), 6)
        else:
            tech["live_price"] = tech["last"]
        cat_rank = lf.get("category_rank") or s.get("category_rank")
        cat_total = lf.get("category_total") or s.get("category_total")
        regime_info = _classify_fund_regime(code, tech, vix_last, cat_rank=cat_rank, cat_total=cat_total)
        fund_analyses[code] = {
            "code": code,
            "name": fund_names.get(code, code),
            "category_rank": cat_rank,
            "category_total": cat_total,
            "technicals": tech,
            "regime": regime_info,
        }

    overall_regime = _compute_overall_regime(fund_analyses, macro_tech)
    safe_budget = max(100.0, min(1_000_000.0, _safe_float(budget_try, 6000.0)))
    dynamic_plan = _build_dynamic_allocation(overall_regime, safe_budget, fund_analyses, live_funds)

    # Canlı portföy veya 10.000 TL %55 Planı için Stres Testi
    if stress_mode == "plan10k":
        holdings_val = {"PPF": 2000.0, "TCA": 2500.0, "AFT": 3500.0, "TI3": 2000.0, "CASH": 0.0}
        stress_base_label = "Örnek ₺10.000 (%55 Büyüme / %45 Kalkan) Planı"
    else:
        db_funds = db.get_all_funds()
        pending_orders = db.get_pending_orders("BEKLIYOR")
        p_by_code: Dict[str, float] = {}
        for po in pending_orders:
            c = str(po.get("code", "")).upper()
            p_by_code[c] = p_by_code.get(c, 0.0) + _safe_float(po.get("amount_try"), 0.0)

        def _fval(c: str) -> float:
            sh = _safe_float(db_funds.get(c, {}).get("shares"), 0.0)
            pr = _safe_float(live_funds.get(c, {}).get("price"), _safe_float(db_funds.get(c, {}).get("buy_price"), 1.0))
            return sh * pr + p_by_code.get(c, 0.0)

        holdings_val = {
            "PPF": _fval("ZBJ") + _fval("TZL"),
            "TCA": _fval("TCA") + _fval("KZL"),
            "AFT": _fval("AFT"),
            "TI3": _fval("TI3"),
            "CASH": _safe_float(db.get_total_cash(), 0.0),
        }
        stress_base_label = "Mevcut Canlı Portföyün"

    stress_tests = _compute_stress_tests(holdings_val, ppf_rate)
    backtest = _run_walkforward_backtest(deep_hist)

    return {
        "status": "ok",
        "updated_at": deep_hist.get("fetched_at", market_service.now_tr().strftime("%Y-%m-%d %H:%M:%S")),
        "overall_regime": overall_regime,
        "macro_signals": {
            k: {
                "last": v.get("last", 0.0),
                "ret_1d_pct": v.get("ret_1d_pct", 0.0),
                "ret_20d_pct": v.get("ret_20d_pct", 0.0),
                "rsi14": v.get("rsi14", 50.0),
                "dist_sma50_pct": v.get("dist_sma50_pct", 0.0),
            }
            for k, v in macro_tech.items()
        },
        "funds": fund_analyses,
        "dynamic_plan": dynamic_plan,
        "stress_test": {
            "mode": stress_mode,
            "label": stress_base_label,
            "holdings_try": {k: round(v, 2) for k, v in holdings_val.items()},
            "scenarios": stress_tests,
        },
        "backtest": backtest,
    }


def get_compact_quant_summary() -> str:
    """AI danışman ve Konsey promptları için hızlı, özet rejim metni üretir."""
    try:
        q = build_quant_radar(budget_try=6000.0, stress_mode="live", force_refresh=False)
        reg = q["overall_regime"]
        funds = q["funds"]
        aft_r = funds.get("AFT", {}).get("regime", {})
        aft_t = funds.get("AFT", {}).get("technicals", {})
        ti3_r = funds.get("TI3", {}).get("regime", {})
        ti3_t = funds.get("TI3", {}).get("technicals", {})
        tca_r = funds.get("TCA", {}).get("regime", {})
        dyn = q["dynamic_plan"]
        return (
            f"Rejim={reg['level']} ({reg['overall_score']}/100, VIX={reg['vix']}, ABD10Y={reg['us10y']}%), "
            f"AFT=[Sağlık {aft_r.get('health_score')}/100, {aft_r.get('badge')}, RSI {aft_t.get('rsi14')}, 50G {aft_t.get('dist_sma50_pct'):+.1f}%, 30G P10..P90 {aft_t.get('proj_30d',{}).get('p10_pct'):+.1f}%..{aft_t.get('proj_30d',{}).get('p90_pct'):+.1f}%], "
            f"TI3=[Sağlık {ti3_r.get('health_score')}/100, {ti3_r.get('badge')}, RSI {ti3_t.get('rsi14')}], "
            f"TCA=[Sağlık {tca_r.get('health_score')}/100, {tca_r.get('badge')}], "
            f"Önerilen Dinamik Dağılım=[Büyüme %{dyn['growth_total_pct']} / Kalkan %{dyn['shield_total_pct']}]"
        )
    except Exception:
        return "Rejim analizi henüz hazırlanıyor."
