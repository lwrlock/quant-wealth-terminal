import datetime
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

try:
    from tefas import Crawler
except ImportError:
    Crawler = None

import db
import market_service

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
LEAGUE_SNAPSHOT_FILE = os.path.join(DATA_DIR, "league_snapshot.json")
CACHE_TTL_SECONDS = 6 * 3600

# Furkan'ın kategorilerine göre gerçek TEFAS ligleri ve takip edilen rakip fonlar
LEAGUE_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "id": "ppf",
        "title": "Para Piyasası Ligi (Nema & Likidite)",
        "owned_codes": ["ZBJ", "TZL"],
        "benchmark_code": "ZBJ",
        "switch_threshold_30d": 0.45,
        "codes": [
            ("ZBJ", "Ziraat Başak PPF (2958)"),
            ("TZL", "Ziraat Para Piyasası"),
            ("BGP", "Ak Portföy 3. PPF"),
            ("TI1", "İş Portföy PPF"),
            ("PPN", "Nurol Portföy PPF"),
            ("NVB", "Neo Portföy 2. PPF"),
        ],
    },
    {
        "id": "altin",
        "title": "Altın Fonları Ligi",
        "owned_codes": ["TCA"],
        "benchmark_code": "TCA",
        "switch_threshold_30d": 0.65,
        "codes": [
            ("TCA", "Ziraat Altın Katılım"),
            ("KZL", "Kuveyt Türk Altın Katılım"),
            ("TTA", "İş Portföy Altın Fonu"),
            ("GGK", "Inveo Portföy Altın Fon"),
        ],
    },
    {
        "id": "teknoloji",
        "title": "Yabancı Teknoloji & Hisse Ligi",
        "owned_codes": ["AFT"],
        "benchmark_code": "AFT",
        "switch_threshold_30d": 1.50,
        "codes": [
            ("AFT", "Ak Portföy Yeni Teknolojiler"),
            ("YAY", "Yapı Kredi Yabancı Teknoloji"),
            ("AFA", "Ak Portföy Amerika Yabancı"),
            ("GUH", "Garanti Yabancı Teknoloji"),
            ("DVT", "Deniz Portföy Teknoloji"),
            ("GBG", "Inveo Yabancı Hisse"),
        ],
    },
]

_MEM_CACHE: Optional[Dict[str, Any]] = None
_MEM_CACHE_TS: float = 0.0


def _pct(new_v: float, old_v: float) -> float:
    if old_v and old_v > 0:
        return round(((new_v - old_v) / old_v) * 100.0, 2)
    return 0.0


def _fetch_single_tefas_30d(code: str, default_name: str, market_funds: Dict[str, Any]) -> Dict[str, Any]:
    # Eğer çekirdek fon market_service'te güncel prices_by_date içeriyorsa ekstra HTTP isteği yapma
    if code in market_funds and len(market_funds[code].get("prices_by_date") or {}) >= 15:
        info = market_funds[code]
        return {
            "code": code,
            "name": info.get("name", default_name),
            "price": round(float(info.get("price") or 0.0), 6),
            "chg_1d": round(float(info.get("daily_yield_pct") or 0.0), 2),
            "chg_7d": round(float(info.get("weekly_yield_pct") or 0.0), 2),
            "chg_30d": round(float(info.get("monthly_yield_pct") or 0.0), 2),
            "tefas_date": info.get("tefas_date", ""),
            "live": True,
        }

    if Crawler is None:
        return {"code": code, "name": default_name, "price": 0.0, "chg_1d": 0.0, "chg_7d": 0.0, "chg_30d": 0.0, "tefas_date": "", "live": False}

    today = market_service.now_tr().date()
    start_s = (today - datetime.timedelta(days=38)).strftime("%Y-%m-%d")
    end_s = today.strftime("%Y-%m-%d")

    for attempt in range(2):
        try:
            df = Crawler().fetch(start=start_s, end=end_s, name=code, columns=["code", "title", "date", "price"])
            if df is not None and not df.empty and "price" in df.columns:
                df = df[df["price"].astype(float) > 0.0]
                if "date" in df.columns:
                    df = df.sort_values("date")
                prices = [float(x) for x in df["price"].tolist() if x is not None and float(x) > 0.0]
                if prices:
                    last_p = prices[-1]
                    prev_p = prices[-2] if len(prices) >= 2 else last_p
                    week_p = prices[-6] if len(prices) >= 6 else prices[0]
                    month_p = prices[-22] if len(prices) >= 22 else prices[0]
                    title = str(df["title"].iloc[-1]).strip() if "title" in df.columns and df["title"].iloc[-1] else default_name
                    tefas_date = str(df["date"].iloc[-1])[:10] if "date" in df.columns else ""
                    return {
                        "code": code,
                        "name": title.title() if title.isupper() else title,
                        "price": round(last_p, 6),
                        "chg_1d": _pct(last_p, prev_p),
                        "chg_7d": _pct(last_p, week_p),
                        "chg_30d": _pct(last_p, month_p),
                        "tefas_date": tefas_date,
                        "live": True,
                    }
        except Exception:
            if attempt == 0:
                time.sleep(0.35)

    return {"code": code, "name": default_name, "price": 0.0, "chg_1d": 0.0, "chg_7d": 0.0, "chg_30d": 0.0, "tefas_date": "", "live": False}


def _evaluate_league_verdict(league_def: Dict[str, Any], ranked: List[Dict[str, Any]]) -> str:
    live_rows = [r for r in ranked if r.get("live") and r.get("price", 0) > 0]
    if not live_rows:
        return "TEFAS verisi bekleniyor."

    top = live_rows[0]
    bench_code = league_def["benchmark_code"]
    bench = next((r for r in live_rows if r["code"] == bench_code), None)
    if not bench:
        return f"Lider: {top['code']} (30G {top['chg_30d']:+.2f}%)"

    rank_idx = next((i + 1 for i, r in enumerate(live_rows) if r["code"] == bench_code), 1)
    diff_30d = round(top["chg_30d"] - bench["chg_30d"], 2)
    diff_per_10k = round(diff_30d * 100.0, 0)  # +0.10 puan -> ₺10 per ₺10.000
    thresh = float(league_def.get("switch_threshold_30d", 0.5))

    if top["code"] in league_def["owned_codes"] or diff_30d <= 0.05:
        return f"🏆 Senin {bench_code} fonun liginde 1. sırada ({bench['chg_30d']:+.2f}%) — değiştirmeye hiç gerek yok!"
    if diff_30d < thresh:
        return (
            f"📌 Senin {bench_code} (#{rank_idx}, {bench['chg_30d']:+.2f}%) lider {top['code']} ({top['chg_30d']:+.2f}%) ile başa baş "
            f"(30G fark {diff_30d:+.2f} puan ≈ ₺{diff_per_10k:.0f}/10B TL) — stopaj & valör kaybına değmez, elindekini koru."
        )
    return (
        f"💡 {top['code']} son 30 günde {bench_code}'den {diff_30d:+.2f} puan önde "
        f"(10.000 TL'de ~₺{diff_per_10k:.0f} fark). Yeni alımlarda alternatif olarak değerlendirilebilir."
    )


def _load_persisted_cache() -> Optional[Dict[str, Any]]:
    if os.path.exists(LEAGUE_SNAPSHOT_FILE):
        try:
            with open(LEAGUE_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("leagues"):
                return data
        except Exception:
            pass
    raw = db.get_setting("tefas_league_cache", "")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("leagues"):
                return data
        except Exception:
            pass
    return None


def get_tefas_league_table(force_refresh: bool = False) -> Dict[str, Any]:
    global _MEM_CACHE, _MEM_CACHE_TS
    now_ts = time.time()

    if not force_refresh:
        if _MEM_CACHE and (now_ts - _MEM_CACHE_TS < CACHE_TTL_SECONDS):
            return _MEM_CACHE
        disk = _load_persisted_cache()
        if disk and (now_ts - float(disk.get("fetched_epoch", 0)) < CACHE_TTL_SECONDS):
            _MEM_CACHE = disk
            _MEM_CACHE_TS = float(disk.get("fetched_epoch", now_ts))
            return disk

    market_funds = market_service.fetch_live_market().get("funds", {})
    all_pairs: List[tuple] = []
    seen = set()
    for ldef in LEAGUE_DEFINITIONS:
        for code, dname in ldef["codes"]:
            if code not in seen:
                seen.add(code)
                all_pairs.append((code, dname))

    fetched_map: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {code: pool.submit(_fetch_single_tefas_30d, code, dname, market_funds) for code, dname in all_pairs}
        for code, fut in futs.items():
            try:
                fetched_map[code] = fut.result(timeout=25.0)
            except Exception:
                fetched_map[code] = {"code": code, "name": code, "price": 0.0, "chg_1d": 0.0, "chg_7d": 0.0, "chg_30d": 0.0, "tefas_date": "", "live": False}

    leagues_out: List[Dict[str, Any]] = []
    for ldef in LEAGUE_DEFINITIONS:
        owned_set = set(ldef["owned_codes"])
        rows = []
        for code, _ in ldef["codes"]:
            item = dict(fetched_map.get(code) or {})
            item["is_owned"] = code in owned_set
            rows.append(item)

        # Canlı olanları 30G getirisine göre büyükten küçüğe sırala
        rows.sort(key=lambda r: (1 if r.get("live") else 0, float(r.get("chg_30d") or -999.0)), reverse=True)
        for idx, r in enumerate(rows, start=1):
            r["rank"] = idx

        leagues_out.append({
            "id": ldef["id"],
            "title": ldef["title"],
            "benchmark_code": ldef["benchmark_code"],
            "verdict": _evaluate_league_verdict(ldef, rows),
            "funds": rows,
        })

    result = {
        "updated_at": market_service.now_tr().strftime("%d.%m.%Y %H:%M"),
        "fetched_epoch": now_ts,
        "leagues": leagues_out,
    }
    _MEM_CACHE = result
    _MEM_CACHE_TS = now_ts

    try:
        with open(LEAGUE_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except Exception:
        pass
    try:
        db.set_setting("tefas_league_cache", json.dumps(result, ensure_ascii=False), sync_cloud=False)
    except Exception:
        pass

    return result
