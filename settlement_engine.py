import datetime
import json
import threading
from typing import Any, Callable, Dict, List, Optional

import db
import market_service

_VALOR_TICK_LOCK = threading.Lock()

TR_DAYS_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]

# Türkiye sabit resmi tatilleri (Ay-Gün). Gerektiğinde ayarlardan genişletilebilir.
FIXED_TR_HOLIDAYS = {
    (1, 1),   # Yılbaşı
    (4, 23),  # Ulusal Egemenlik ve Çocuk Bayramı
    (5, 1),   # Emek ve Dayanışma Günü
    (5, 19),  # Atatürk'ü Anma, Gençlik ve Spor Bayramı
    (7, 15),  # Demokrasi ve Milli Birlik Günü
    (8, 30),  # Zafer Bayramı
    (10, 29), # Cumhuriyet Bayramı
}

# Fon bazlı TEFAS kesim saati ve valör takvimi kuralları (Alım emri)
# trade_date: emrin TEFAS seansına girdiği iş günü
# price_offset: trade_date üzerine eklenecek iş günü (TEFAS fiyat tarihi)
# settle_offset: trade_date üzerine eklenecek iş günü (Ziraat portföye geçme günü)
FUND_VALOR_RULES: Dict[str, Dict[str, Any]] = {
    "TZL": {
        "cutoff_hhmm": "23:59",
        "price_offset": 0,
        "settle_offset": 0,
        "after_trade_plus": 0,
        "instant_t0": True,
        "typical_window": "7/24 Anında (T+0)",
        "rule_label": "Ziraat İçi 7/24 T+0 (Anında Direkt Onay)",
    },
    "ZBJ": {
        "cutoff_hhmm": "13:30",
        "price_offset": 0,
        "settle_offset": 0,
        "after_trade_plus": 1,
        "typical_window": "09:30–10:30",
        "rule_label": "PPF T+0 (13:30 sonrası T+1)",
    },
    "TCA": {
        "cutoff_hhmm": "13:30",
        "price_offset": 1,
        "settle_offset": 1,
        "after_trade_plus": 1,
        "typical_window": "09:30–14:30",
        "rule_label": "İleri Valör T+1 (13:30 sonrası T+2)",
    },
    "AFT": {
        "cutoff_hhmm": "13:30",
        "price_offset": 1,
        "settle_offset": 1,
        "after_trade_plus": 1,
        "typical_window": "10:30–14:30",
        "rule_label": "Yabancı/Teknoloji T+1 (13:30 sonrası T+2)",
    },
    "KZL": {
        "cutoff_hhmm": "13:30",
        "price_offset": 1,
        "settle_offset": 1,
        "after_trade_plus": 1,
        "typical_window": "09:30–14:30",
        "rule_label": "Altın Fonu T+1 (13:30 sonrası T+2)",
    },
    "TI3": {
        "cutoff_hhmm": "13:30",
        "price_offset": 1,
        "settle_offset": 1,
        "after_trade_plus": 1,
        "typical_window": "09:30–14:30",
        "rule_label": "TEFAS Hisse T+1 (13:30 sonrası T+2)",
    },
}

DEFAULT_VALOR_RULE = {
    "cutoff_hhmm": "13:30",
    "price_offset": 1,
    "settle_offset": 1,
    "after_trade_plus": 1,
    "typical_window": "09:30–14:30",
    "rule_label": "Standart TEFAS T+1 (13:30 sonrası T+2)",
}


def is_tr_business_day(d: datetime.date) -> bool:
    if d.weekday() >= 5:
        return False
    if (d.month, d.day) in FIXED_TR_HOLIDAYS:
        return False
    return True


def next_tr_business_day(d: datetime.date, steps: int = 1) -> datetime.date:
    cur = d
    if steps <= 0:
        while not is_tr_business_day(cur):
            cur += datetime.timedelta(days=1)
        return cur
    rem = steps
    while rem > 0:
        cur += datetime.timedelta(days=1)
        if is_tr_business_day(cur):
            rem -= 1
    return cur


def _parse_dt(dt_str: str) -> datetime.datetime:
    clean = str(dt_str or "").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(clean, fmt)
        except Exception:
            pass
    return market_service.now_tr()


def get_valor_config() -> Dict[str, Any]:
    raw = db.get_setting("valor_auto_config", "")
    defaults = {
        "mode": "ask",          # "ask" (TEFAS fiyatı çıkınca Telegram/Web 1-tık onay) | "auto" (takas saatinde otopilot)
        "settle_hour": "14:30", # "auto" modunda beklenen en geç banka onay saati
        "notify_telegram": True,
    }
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                defaults.update(parsed)
        except Exception:
            pass
    if defaults.get("mode") not in ("ask", "auto", "manual"):
        defaults["mode"] = "ask"
    return defaults


def save_valor_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_valor_config()
    if "mode" in updates and str(updates["mode"]) in ("ask", "auto", "manual"):
        cfg["mode"] = str(updates["mode"])
    if "settle_hour" in updates and ":" in str(updates["settle_hour"]):
        cfg["settle_hour"] = str(updates["settle_hour"]).strip()[:5]
    if "notify_telegram" in updates:
        cfg["notify_telegram"] = bool(updates["notify_telegram"])
    db.set_setting("valor_auto_config", json.dumps(cfg, ensure_ascii=False), sync_cloud=True)
    return cfg


def compute_valor_schedule(code: str, created_at_str: str) -> Dict[str, Any]:
    norm_code = str(code or "TCA").upper().strip()
    rule = FUND_VALOR_RULES.get(norm_code, DEFAULT_VALOR_RULE)
    dt = _parse_dt(created_at_str)
    instant_t0 = bool(rule.get("instant_t0"))

    if instant_t0:
        trade_date = dt.date()
        price_date = dt.date()
        settle_date = dt.date()
        after_cutoff = False
        t_badge = "T+0"
        settle_label = f"{settle_date.strftime('%d.%m')} {TR_DAYS_SHORT[settle_date.weekday()]}"
        price_label = settle_label
        return {
            "code": norm_code,
            "instant_t0": True,
            "after_cutoff": False,
            "cutoff": rule["cutoff_hhmm"],
            "trade_date": trade_date.strftime("%Y-%m-%d"),
            "price_date": price_date.strftime("%Y-%m-%d"),
            "settle_date": settle_date.strftime("%Y-%m-%d"),
            "t_badge": t_badge,
            "price_label": price_label,
            "settle_label": settle_label,
            "typical_window": rule["typical_window"],
            "expected_text": f"Anında Onay ({t_badge})",
            "rule_label": rule["rule_label"],
        }

    cut_h, cut_m = [int(x) for x in rule["cutoff_hhmm"].split(":")]

    day_is_biz = is_tr_business_day(dt.date())
    past_cutoff = (dt.hour, dt.minute) >= (cut_h, cut_m)
    after_cutoff = (not day_is_biz) or past_cutoff

    if not after_cutoff:
        trade_date = dt.date()
    else:
        base_d = dt.date()
        trade_date = next_tr_business_day(base_d, 1) if day_is_biz else next_tr_business_day(base_d, 0)

    price_date = next_tr_business_day(trade_date, rule["price_offset"]) if rule["price_offset"] > 0 else trade_date
    settle_date = next_tr_business_day(trade_date, rule["settle_offset"]) if rule["settle_offset"] > 0 else trade_date

    # Takvim günü & iş günü farkı (T+N etiketi)
    biz_diff = 0
    cursor = dt.date() if day_is_biz else next_tr_business_day(dt.date(), 0)
    while cursor < settle_date:
        cursor = next_tr_business_day(cursor, 1)
        biz_diff += 1

    t_badge = f"T+{biz_diff}"
    settle_label = f"{settle_date.strftime('%d.%m')} {TR_DAYS_SHORT[settle_date.weekday()]}"
    price_label = f"{price_date.strftime('%d.%m')} {TR_DAYS_SHORT[price_date.weekday()]}"

    return {
        "code": norm_code,
        "instant_t0": False,
        "after_cutoff": after_cutoff,
        "cutoff": rule["cutoff_hhmm"],
        "trade_date": trade_date.strftime("%Y-%m-%d"),
        "price_date": price_date.strftime("%Y-%m-%d"),
        "settle_date": settle_date.strftime("%Y-%m-%d"),
        "t_badge": t_badge,
        "price_label": price_label,
        "settle_label": settle_label,
        "typical_window": rule["typical_window"],
        "expected_text": f"{settle_label} • ~{rule['typical_window']} ({t_badge})",
        "rule_label": rule["rule_label"],
    }


def enrich_pending_order(order: Dict[str, Any], market_funds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(order)
    code = str(out.get("code", "TCA")).upper().strip()
    sched = compute_valor_schedule(code, out.get("created_at", ""))
    amt = float(out.get("amount_try") or 0.0)
    est_p = float(out.get("estimated_price") or 0.0)

    if market_funds is None:
        market_funds = market_service.fetch_live_market().get("funds", {})
    fund_info = market_funds.get(code, {})
    live_p = float(fund_info.get("price") or est_p or 1.0)
    out["live_price"] = live_p

    confirmed = market_service.get_fund_price_on_date(code, sched["price_date"])
    now_dt = market_service.now_tr()
    today_str = now_dt.strftime("%Y-%m-%d")

    cfg = get_valor_config()
    sh_str = cfg.get("settle_hour", "14:30")
    try:
        sh_h, sh_m = [int(x) for x in sh_str.split(":")]
    except Exception:
        sh_h, sh_m = 14, 30

    if sched.get("instant_t0"):
        confirmed_price = float(confirmed["price"]) if confirmed else live_p
        confirmed_date = (confirmed["date"] if confirmed else (fund_info.get("tefas_date") or today_str))
        is_settle_day_or_after = True
        in_bank_window = True
        past_auto_hour = True
        ready_to_settle = True
    else:
        confirmed_price = float(confirmed["price"]) if confirmed else None
        confirmed_date = confirmed["date"] if confirmed else None
        is_settle_day_or_after = today_str >= sched["settle_date"]
        in_bank_window = is_settle_day_or_after and ((now_dt.hour, now_dt.minute) >= (9, 30) or today_str > sched["settle_date"])
        past_auto_hour = today_str > sched["settle_date"] or (today_str == sched["settle_date"] and (now_dt.hour, now_dt.minute) >= (sh_h, sh_m))
        ready_to_settle = bool(confirmed_price and is_settle_day_or_after)

    calc_price = confirmed_price if confirmed_price else live_p

    if amt > 0 and calc_price > 0:
        lot_step = market_service.get_fund_lot_step(code)
        raw_units = int((amt + 1e-9) / calc_price)
        lot_units = (raw_units // lot_step) * lot_step
        proj_shares = float(lot_units) if lot_units > 0 else (float(int(amt / calc_price)) if amt >= calc_price else 0.0)
        proj_spent = round(proj_shares * calc_price, 2)
        proj_refund = round(max(0.0, amt - proj_spent), 2)
    else:
        proj_shares = float(out.get("estimated_shares") or 0.0)
        proj_refund = 0.0

    if sched.get("instant_t0"):
        stage_badge = f"Ziraat İçi T+0 Anlık Fiyat (₺{calc_price:.4f}) • Onaya Hazır"
        stage_color = "emerald"
        progress_pct = 95
    elif ready_to_settle and in_bank_window:
        stage_badge = f"TEFAS Fiyatı Kesinleşti (₺{confirmed_price:.4f}) • Banka Onay Saatinde"
        stage_color = "emerald"
        progress_pct = 90
    elif confirmed_price:
        stage_badge = f"TEFAS Fiyatı Yayınlandı (₺{confirmed_price:.4f}) • Takas Bekliyor"
        stage_color = "cyan"
        progress_pct = 70
    elif today_str >= sched["trade_date"]:
        stage_badge = f"TEFAS Seansında • Fiyat {sched['price_label']} 09:00'da"
        stage_color = "amber"
        progress_pct = 45
    else:
        stage_badge = f"Kuyrukta • İlk İş Günü {sched['settle_label']}"
        stage_color = "amber"
        progress_pct = 20

    out["valor_schedule"] = {
        **sched,
        "confirmed_price": confirmed_price,
        "confirmed_date": confirmed_date,
        "latest_tefas_date": fund_info.get("tefas_date") or "",
        "projected_shares": proj_shares,
        "projected_refund_krs": proj_refund,
        "ready_to_settle": ready_to_settle,
        "in_bank_window": in_bank_window,
        "past_auto_hour": past_auto_hour,
        "stage_badge": stage_badge,
        "stage_color": stage_color,
        "progress_pct": progress_pct,
    }
    return out


def enrich_pending_orders(orders: List[Dict[str, Any]], market_funds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if market_funds is None:
        market_funds = market_service.fetch_live_market().get("funds", {})
    return [enrich_pending_order(o, market_funds) for o in orders]


def run_valor_automation_tick(
    notify_fn: Optional[Callable[[str, Optional[Dict[str, Any]]], bool]] = None,
    refresh_tefas: bool = False,
    source: str = "bg",
) -> Dict[str, Any]:
    """
    TEFAS'tan çekilen güncel fiyat/tarih bilgisine göre valördeki emirleri tarar:
    - refresh_tefas=True ise canlı TEFAS sunucusundan taze fiyat/tarih serisi çeker.
    - Fiyat kesinleşmiş ve takas gününe gelmişse:
      * mode == 'auto' ve saat >= settle_hour ise (veya instant T+0 ise) otomatik portföye geçirir.
      * mode == 'ask' ise ready_orders listesine ekler & Telegram aktifse tek tıkla [İşleme Al] bildirimi atar.
    """
    with _VALOR_TICK_LOCK:
        pending = db.get_pending_orders("BEKLIYOR")
        tefas_refreshed = False
        if refresh_tefas and pending:
            try:
                tefas_refreshed = bool(market_service.refresh_live_market_now())
            except Exception:
                tefas_refreshed = False

        now_dt = market_service.now_tr()
        today_str = now_dt.strftime("%Y-%m-%d")
        cfg = get_valor_config()
        mode = cfg.get("mode", "ask")

        market = market_service.fetch_live_market()
        dq = market.get("data_quality") or {}

        if not pending:
            return {
                "checked": 0,
                "mode": mode,
                "source": source,
                "checked_at": now_dt.strftime("%H:%M:%S"),
                "tefas_refreshed": tefas_refreshed,
                "tefas_live_count": dq.get("tefas_live_count", 0),
                "source_status": dq.get("source_status", ""),
                "auto_settled": [],
                "notified": [],
                "ready_orders": [],
                "details": [],
            }

        market_funds = market.get("funds", {})
        enriched = enrich_pending_orders(pending, market_funds)

        auto_settled: List[Dict[str, Any]] = []
        notified: List[str] = []
        ready_orders: List[Dict[str, Any]] = []
        details: List[Dict[str, Any]] = []

        for item in enriched:
            vs = item.get("valor_schedule", {})
            oid = item["id"]
            code = item["code"]
            conf_price = vs.get("confirmed_price")
            ready = bool(vs.get("ready_to_settle"))
            latest_tefas = vs.get("latest_tefas_date") or "-"
            settle_lbl = vs.get("settle_label") or vs.get("settle_date") or ""

            detail_entry = {
                "order_id": oid,
                "code": code,
                "amount_try": float(item.get("amount_try") or 0.0),
                "projected_shares": float(vs.get("projected_shares") or 0.0),
                "confirmed_price": conf_price,
                "confirmed_date": vs.get("confirmed_date"),
                "latest_tefas_date": latest_tefas,
                "expected_settle_date": vs.get("settle_date"),
                "expected_settle_label": settle_lbl,
                "ready_to_settle": ready,
                "instant_t0": bool(vs.get("instant_t0")),
                "stage_badge": vs.get("stage_badge", ""),
                "reason": "onay_bekliyor" if ready else f"TEFAS son fiyat {latest_tefas}; takas günü {settle_lbl}",
            }

            if not conf_price:
                details.append(detail_entry)
                continue

            # 1) OTOPİLOT MODU: Hedef takas günü & saati geçtiyse + TEFAS fiyatı kesinleştiyse otomatik onayla
            if mode == "auto" and ready and vs.get("past_auto_hour"):
                try:
                    res = db.settle_pending_order(
                        order_id=oid,
                        settled_price=conf_price,
                        settled_shares=vs.get("projected_shares"),
                    )
                    auto_settled.append(res)
                    detail_entry["reason"] = "auto_settled"
                    details.append(detail_entry)
                    if notify_fn and cfg.get("notify_telegram", True):
                        try:
                            notify_fn(
                                f"⚡ *VALÖR OTOMATİK ONAYLANDI ({code})*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"• *TEFAS Kesin Fiyat ({vs.get('confirmed_date')}):* `₺{conf_price:.6f}`\n"
                                f"• *Portföye Geçen Pay:* `{int(res['settled_shares']):,} adet`\n"
                                f"• *Artan Kuruş İadesi:* `₺{res['refund_krs']:.2f}`\n"
                                f"_Ziraat Mobil'de farklı küsurat varsa defterden düzeltebilirsin._",
                                None,
                            )
                        except Exception:
                            pass
                    continue
                except Exception as e:
                    print(f"[Valor Auto Settle Err] {oid}: {e}")
                    detail_entry["reason"] = f"auto_error: {e}"

            # 2) SORARAK ONAY MODU ('ask') veya WEB'DEN HAZIR EMRİ GÖSTERME
            if ready and vs.get("in_bank_window"):
                ready_orders.append({
                    "order_id": oid,
                    "code": code,
                    "amount_try": float(item.get("amount_try") or 0.0),
                    "confirmed_price": conf_price,
                    "confirmed_date": vs.get("confirmed_date"),
                    "projected_shares": float(vs.get("projected_shares") or 0.0),
                    "instant_t0": bool(vs.get("instant_t0")),
                })
                if mode == "ask":
                    notif_key = f"valor_notif_{oid}"
                    if db.get_setting(notif_key) != today_str and notify_fn and cfg.get("notify_telegram", True):
                        keyboard = {
                            "inline_keyboard": [[
                                {"text": f"✅ İşleme Al ({int(vs['projected_shares']):,} pay)", "callback_data": f"valor_settle:{oid}"},
                                {"text": "⏳ Henüz Geçmedi", "callback_data": f"valor_wait:{oid}"},
                            ]]
                        }
                        try:
                            sent = notify_fn(
                                f"🔔 *TEFAS FİYATI YAYINLANDI // VALÖR KONTROLÜ ({code})*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📌 *Emir Tutarı:* `₺{float(item['amount_try']):,.2f}`\n"
                                f"📈 *Kesinleşen TEFAS Fiyatı ({vs.get('confirmed_date')}):* `₺{conf_price:.6f}`\n"
                                f"🧮 *Beklenen Pay:* `{int(vs['projected_shares']):,} adet` (İade: `₺{vs['projected_refund_krs']:.2f}`)\n"
                                f"⏰ *Ziraat Onay Penceresi:* `{vs.get('typical_window')}`\n\n"
                                f"Ziraat Mobil'de fon hesabına geçtiyse tek dokunuşla onaylayalım mı?",
                                keyboard,
                            )
                            if sent:
                                db.set_setting(notif_key, today_str, sync_cloud=False)
                                notified.append(oid)
                        except Exception:
                            pass

            details.append(detail_entry)

        return {
            "checked": len(enriched),
            "mode": mode,
            "source": source,
            "checked_at": now_dt.strftime("%H:%M:%S"),
            "tefas_refreshed": tefas_refreshed,
            "tefas_live_count": dq.get("tefas_live_count", 0),
            "source_status": dq.get("source_status", ""),
            "auto_settled": auto_settled,
            "notified": notified,
            "ready_orders": ready_orders,
            "details": details,
        }
