import os
import json
import time
import re
import uuid
import datetime
import threading
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional, Tuple

try:
    import db
    import news_service
    import ai_advisor
    import market_service
    import settlement_engine
    import tax_engine
    import auto_allocator
    import league_service
    import quant_engine
    import alpha_hunter
    import tefas_analyzer
except ImportError:
    from finans_dashboard import db, news_service, ai_advisor, market_service, settlement_engine, tax_engine, auto_allocator, league_service, quant_engine, alpha_hunter, tefas_analyzer

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DATA_DIR, "telegram_config.json")
_CONFIG_LOCK = threading.Lock()
_BOT_INFO_CACHE: Optional[Dict[str, Any]] = None
_BOT_INFO_TIME: float = 0.0

DEFAULT_CONFIG = {
    "bot_token": "",
    "authorized_chat_ids": [],
    "enabled": True,
    "last_update_id": 0,
    "daily_summary_time": "18:30",
    "friday_alert_time": "13:00"
}

def load_config() -> Dict[str, Any]:
    with _CONFIG_LOCK:
        if not os.path.exists(CONFIG_PATH):
            cfg = DEFAULT_CONFIG.copy()
        else:
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    if k not in cfg:
                        cfg[k] = v
            except Exception:
                cfg = DEFAULT_CONFIG.copy()
        env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if env_token:
            cfg["bot_token"] = env_token
        return cfg

def save_config(cfg: Dict[str, Any]):
    with _CONFIG_LOCK:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Telegram] Config kaydetme hatası: {e}")

def tg_api_call(method: str, params: Optional[Dict[str, Any]] = None, token: Optional[str] = None, http_timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    if not token:
        cfg = load_config()
        token = cfg.get("bot_token", "").strip()
    if not token:
        return None
        
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if params:
            data = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "Furkan-Capital-Bot/1.0"})
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "Furkan-Capital-Bot/1.0"})
            
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            res_data = resp.read().decode("utf-8")
            res_json = json.loads(res_data)
            if res_json.get("ok"):
                return res_json.get("result")
    except Exception:
        pass
    return None

def send_message(text: str, chat_id: Optional[int] = None, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    cfg = load_config()
    token = cfg.get("bot_token", "").strip()
    if not token:
        return False
        
    targets = [chat_id] if chat_id else cfg.get("authorized_chat_ids", [])
    success = False
    for cid in targets:
        if not cid:
            continue
        payload: Dict[str, Any] = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        res = tg_api_call("sendMessage", payload, token=token)
        if res:
            success = True
    return success


def send_document(
    file_bytes: bytes,
    filename: str,
    caption: str = "",
    chat_id: Optional[int] = None,
    http_timeout: float = 25.0,
) -> bool:
    """Telegram Bot API sendDocument ile dosya (ör. JSON yedek) gönderir (stdlib multipart)."""
    cfg = load_config()
    token = cfg.get("bot_token", "").strip()
    if not token:
        return False

    targets = [chat_id] if chat_id else cfg.get("authorized_chat_ids", [])
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    success = False

    for cid in targets:
        if not cid:
            continue
        boundary = f"----FurkanFinansBoundary{uuid.uuid4().hex}"
        parts = []
        fields = {"chat_id": str(cid)}
        if caption:
            fields["caption"] = caption
            fields["parse_mode"] = "Markdown"
        for k, v in fields.items():
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
                    f"{v}\r\n"
                ).encode("utf-8")
            )
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
                f"Content-Type: application/json\r\n\r\n"
            ).encode("utf-8")
            + file_bytes
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        body = b"".join(parts)
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Furkan-Capital-Bot/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                if res_json.get("ok"):
                    success = True
        except Exception as e:
            print(f"[Telegram sendDocument Err]: {e}")
    return success


def send_json_backup(chat_id: Optional[int] = None, auto: bool = False) -> bool:
    """Tüm SQLite portföy verisini tam JSON yedeği olarak Telegram'a gönderir."""
    backup = db.export_full_backup()
    now_dt = market_service.now_tr()
    fname = f"finans_yedek_{now_dt.strftime('%Y%m%d_%H%M')}.json"
    raw_bytes = json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8")
    tx_count = len(backup.get("transactions") or [])
    fund_count = len((backup.get("holdings") or {}).get("funds") or {})
    cash_try = float((backup.get("holdings") or {}).get("cash_try") or 0.0)
    prefix = "🗓 *HAFTALIK OTOMATİK JSON YEDEK*" if auto else "💾 *MANUEL TAM JSON YEDEĞİ*"
    caption = (
        f"{prefix}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• Tarih: `{backup.get('created_at', now_dt.strftime('%Y-%m-%d %H:%M'))}`\n"
        f"• Nakit: `₺{cash_try:,.2f}` | Fon: `{fund_count}` | İşlem: `{tx_count}`\n"
        f"_Web panel → İçe Aktar ile tek tıkla geri yüklenebilir._"
    )
    return send_document(raw_bytes, fname, caption=caption, chat_id=chat_id)


def execute_recurring_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Tek bir aylık sabit akış kuralını ACID deftere işler ve ilgili ayı işaretler."""
    now_dt = market_service.now_tr()
    t_type = rule.get("type", "GİRİŞ").upper().strip()
    amt = round(float(rule.get("amount_try", 0.0)), 2)
    acc_id = db.normalize_account_id(rule.get("account_id"), "5001")
    new_tx = {
        "id": f"tx-rec-{uuid.uuid4().hex[:6]}",
        "date": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "type": t_type,
        "category": rule.get("category") or "Sabit Akış",
        "amount_try": amt,
        "shares": 0.0,
        "price": 0.0,
        "code": "TRY",
        "account_id": acc_id,
        "to_account_id": None,
        "description": f"{rule.get('title', 'Sabit Akış')} (Otomatik Akış)",
    }
    db.apply_and_insert_transaction(new_tx)
    db.mark_recurring_triggered(rule["id"], now_dt.strftime("%Y-%m"))
    return new_tx

def get_live_fund_price(code: str, fallback_buy_price: float = 1.0) -> float:
    return market_service.get_live_fund_price(code, fallback_buy_price)

# ======================================================================
# PROAKTİF AKILLI ALARMLAR VE FIRSAT RADARI MESAJ OLUŞTURUCULARI
# ======================================================================
def build_friday_alert_text() -> str:
    accounts = db.get_all_accounts()
    idle_cash = accounts.get("5001", {}).get("balance", 0.0) + accounts.get("5004", {}).get("balance", 0.0)
    return (
        "🔔 *CUMA 12:45 NEMA KİLİTLEME UYARISI*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ *Hafta Sonu Kapanışına Son 45 Dakika!*\n"
        "Saat 13:30'a kadar Ziraat Mobil'den fon alırsan *3 GÜNLÜK NEMA* (Cuma + Cmt + Pazar) tek seferde hesabına işler.\n\n"
        f"💵 *Vadesizde Boşta Nakit:* ₺{idle_cash:,.2f}\n"
        "🌱 *Önerilen Hamle:* Ziraat Mobil > Yatırım > Fon Alış > *2958 (ZBJ Başak)* veya *TZL*\n\n"
        "💡 _Hafta sonu paran boşta erimesin, gecelik bileşik nema çalışsın!_"
    )

def build_night_nema_text() -> str:
    funds = db.get_all_funds()
    total_cash = db.get_total_cash()
    ppf_rate = market_service.get_live_ppf_daily_rate()

    zbj_shares = funds.get("ZBJ", {}).get("shares", 0.0)
    tzl_shares = funds.get("TZL", {}).get("shares", 0.0)
    zbj_price = get_live_fund_price("ZBJ", 5.630101)
    tzl_price = get_live_fund_price("TZL", 0.149766)

    zbj_val = zbj_shares * zbj_price
    tzl_val = tzl_shares * tzl_price
    total_ppf = zbj_val + tzl_val
    overnight_nema = total_ppf * ppf_rate
    monthly_nema = overnight_nema * 30.5

    total_funds_val = sum(f.get("shares", 0.0) * get_live_fund_price(k, f.get("buy_price", 1.0)) for k, f in funds.items())
    pending_total = db.get_pending_total_try()
    total_wealth = total_cash + total_funds_val + pending_total
    pending_note = f"\n⏳ *Valörde Bekleyen Emir:* ₺{pending_total:,.2f}\n" if pending_total > 0 else "\n"

    return (
        "🌙 *GECE 00:05 GÜNLÜK KAZANÇ ÖZETİ*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 *Toplam Birikimin:* ₺{total_wealth:,.2f}\n"
        f"🌱 *Bu Gece Beklenen Günlük Kazanç (%{ppf_rate*100:.2f}):* +₺{overnight_nema:,.2f}\n"
        f"📅 *Aylık Tahmini Hız:* ~₺{monthly_nema:,.2f}/ay"
        f"{pending_note}\n"
        "📈 *Günlük Kazanç Üreten Fonların:*\n"
        f"• *ZBJ Başak (2958):* {zbj_shares:,.0f} pay (~₺{zbj_val:,.2f})\n"
        f"• *TZL:* {tzl_shares:,.0f} pay (~₺{tzl_val:,.2f})\n\n"
        "💤 _Sen uyurken paran senin için çalışmaya devam ediyor. İyi geceler!_"
    )


def build_weekly_summary_text() -> str:
    """Pazar akşamı 20:00 — 1 Dakikalık Haftalık Özet Karnesi."""
    market = market_service.fetch_live_market()
    macro = market.get("macro", {})
    funds_m = market.get("funds", {})
    ppf_rate = market_service.get_live_ppf_daily_rate()

    accounts = db.get_all_accounts()
    funds = db.get_all_funds()
    total_cash = db.get_total_cash()
    total_funds_val = sum(
        float(f.get("shares", 0.0)) * get_live_fund_price(k, f.get("buy_price", 1.0))
        for k, f in funds.items()
    )
    pending_total = db.get_pending_total_try()
    total_wealth = round(total_cash + total_funds_val + pending_total, 2)
    gram_price = max(1.0, float(macro.get("gram_altin_try", 6000.0)))
    usd_price = max(1.0, float(macro.get("usd_try", 50.0)))

    stats = db.get_weekly_summary_stats()
    spent_7d = stats["spent_7d"]
    prev_7d = stats["spent_prev_7d"]
    delta_pct = stats["spend_delta_pct"]

    if spent_7d <= 0:
        spend_line = "• *Bu Hafta Harcama:* Kayıtlı çıkış yok (₺0,00)"
    elif delta_pct is None:
        spend_line = f"• *Bu Hafta Harcama:* ₺{spent_7d:,.2f}"
    elif delta_pct > 0:
        spend_line = f"• *Bu Hafta Harcama:* ₺{spent_7d:,.2f} _(Geçen haftaya göre +%{delta_pct:.1f} daha fazla)_"
    else:
        spend_line = f"• *Bu Hafta Harcama:* ₺{spent_7d:,.2f} _(Geçen haftaya göre %{abs(delta_pct):.1f} tasarruf!)_"

    ref_7d = stats.get("history_7d_ago_try")
    if ref_7d is not None and ref_7d > 0:
        w_diff = total_wealth - ref_7d
        w_pct = (w_diff / ref_7d) * 100.0
        sign = "+" if w_diff >= 0 else ""
        wealth_line = f"• *7 Günlük Değişim:* {sign}₺{w_diff:,.2f} ({sign}%{w_pct:.2f})"
    else:
        wealth_line = "• *7 Günlük Değişim:* Yeni kayıt dönemi (önümüzdeki Pazar kıyaslanır)"

    top_cat = stats.get("top_category")
    if top_cat:
        cat_line = f"• *En Çok Nereye Gitti?* {top_cat} (₺{stats['top_category_amount']:,.2f} • %{stats['top_category_pct']:.0f})"
    else:
        cat_line = "• *En Çok Nereye Gitti?* Bu hafta kategori dağılımı oluşmadı"

    # Veri odaklı tek cümlelik haftalık tavsiye
    idle_cash = accounts.get("5001", {}).get("balance", 0.0) + accounts.get("5004", {}).get("balance", 0.0)
    tca_dd = float(funds_m.get("TCA", {}).get("drawdown_from_high_pct", 0.0))
    aft_dd = float(funds_m.get("AFT", {}).get("drawdown_from_high_pct", 0.0))
    if idle_cash >= 250.0:
        tip = f"Vadesizde ₺{idle_cash:,.0f} boşta duruyor; yeni haftada en azından ZBJ'ye alıp günlük ~%{ppf_rate*100:.2f} kazanç başlatabilirsin."
    elif aft_dd <= -2.5:
        tip = f"Teknoloji fonu (AFT) 30 günlük zirvesinden %{abs(aft_dd):.1f} geride; ek bütçen olursa kademeli alım için sakin bir dönem."
    elif tca_dd <= -1.5:
        tip = f"Altın fonu (TCA) son zirveden %{abs(tca_dd):.1f} aşağıda; ufak takviye için uygun görünüyor."
    elif delta_pct is not None and delta_pct >= 20.0:
        tip = f"Harcamalar geçen haftaya göre %{delta_pct:.0f} artmış; yeni haftada küçük harcamaları 1 adım yavaşlatmak rahatlatır."
    else:
        tip = "Her şey dengede ve planın yolunda. Sadece derslerine odaklan, paran kendi kendine çalışıyor."

    return (
        "📊 *PAZAR AKŞAMI • 1 DAKİKALIK HAFTALIK ÖZET*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 *Toplam Birikimin:* ₺{total_wealth:,.2f}\n"
        f"   └ ~${total_wealth/usd_price:,.1f} Dolar | ~{total_wealth/gram_price:.2f} gr Altın\n"
        f"{wealth_line}\n\n"
        "🧾 *Harcama Karnesi (Son 7 Gün):*\n"
        f"{spend_line}\n"
        f"{cat_line}\n\n"
        f"💡 *Haftaya Tek Tavsiye:* {tip}"
    )

def get_live_fund_data_dict() -> Dict[str, Dict[str, Any]]:
    return market_service.get_live_fund_snapshot()

def build_opportunity_radar_text() -> str:
    fund_data = get_live_fund_data_dict()
    tca = fund_data["TCA"]
    aft = fund_data["AFT"]
    zbj = fund_data["ZBJ"]
    tca_chg = tca["chg"]
    aft_chg = aft["chg"]
    tca_dd = tca.get("drawdown_from_high_pct", 0.0)
    aft_dd = aft.get("drawdown_from_high_pct", 0.0)
    ppf_rate = market_service.get_live_ppf_daily_rate()

    tca_signal = "🟡 UYGUN ALIM (İSKONTO)" if (tca_chg < 0 or tca_dd <= -1.5) else "⚪ DENGELİ BÖLGE"
    aft_knife = False
    try:
        q_snap = quant_engine.build_quant_radar(budget_try=6000.0, stress_mode="live", force_refresh=False)
        aft_state = (q_snap.get("funds") or {}).get("AFT", {}).get("regime", {}).get("state")
        aft_knife = (aft_state == "FALLING_KNIFE")
    except Exception:
        pass
    if aft_knife:
        aft_signal = "🛑 DÜŞEN BIÇAK — DÖNÜŞ BEKLE"
        aft_comment = "Nasdaq/AFT zayıf trendde; acele etmeden dönüş teyidi gelene kadar ZBJ'de bekle."
    elif aft_chg <= -1.0 or aft_dd <= -2.5:
        aft_signal = "🔥 DİP FIRSATI"
        aft_comment = "Teknoloji hisselerinde geri çekilme var, kademeli alım düşünülebilir."
    elif aft_chg >= 1.5:
        aft_signal = "🚀 YÜKSELİŞTE / BEKLE"
        aft_comment = "Teknoloji primli; acele etmeden geri çekilmeleri bekle."
    else:
        aft_signal = "⚪ DENGELİ BÖLGE"
        aft_comment = "Teknoloji dengeli; kademeli planına sadık kal."

    lines = [
        "🎯 *GÜNLÜK FIRSAT RADARI (35 GÜNLÜK VERİ)*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🥇 *TCA Altın Katılım:* ₺{tca['price']:.4f} (1G {tca_chg:+.2f}% | 7G {tca.get('weekly_chg', 0):+.2f}%)",
        f"   └ Sinyal: *{tca_signal}* (30G zirveden {tca_dd:+.2f}%)",
        f"   └ _Yorum:_ {('Altın geri çekildi; makas ödemeden kademeli toplamak için uygun bölge.' if (tca_chg < 0 or tca_dd <= -1.5) else 'Altın dengeli seyrediyor.')}",
        "",
        f"🚀 *AFT Teknoloji:* ₺{aft['price']:.4f} (1G {aft_chg:+.2f}% | 7G {aft.get('weekly_chg', 0):+.2f}%)",
        f"   └ Sinyal: *{aft_signal}* (30G zirveden {aft_dd:+.2f}%)",
        f"   └ _Yorum:_ {aft_comment}",
        "",
        f"🛡 *ZBJ Başak (2958):* ₺{zbj['price']:.4f} (+%{zbj['chg']:.2f} gün • 30G +%{zbj.get('monthly_yield_pct', zbj.get('monthly_chg', 3.0)):.2f})",
        f"   └ Sinyal: *GÜVENLİ GÜNLÜK KAZANÇ (~%{ppf_rate*100:.2f}/gün)*",
        "   └ _Yorum:_ Karar verene kadar nakdin burada her gece risksiz büyüsün.",
        "━━━━━━━━━━━━━━━━━━━━",
        "💡 _Kural: Sadece geri çekilme günlerinde TCA/AFT topla, beklerken ZBJ'de tut._"
    ]
    try:
        alpha_scan = alpha_hunter.scan_alpha_universe(force_refresh=False)
        opps = alpha_scan.get("opportunities") or []
        if opps:
            lines.append("")
            lines.append(f"🟢 *BUGÜN ALINABİLİR HİSSE / COİN FIRSATLARI ({len(opps)}):*")
            for item in opps[:2]:
                bt = item["active_backtest"]
                rp = item["risk_plan"]
                sym_c = "₺" if item["currency"] == "TRY" else "$"
                lines.append(
                    f"• *{item['symbol']}* (`{sym_c}{item['price']:,.2f}`)\n"
                    f"  └ {item.get('plain_reason', '')}\n"
                    f"  └ Zarar Kes: `{sym_c}{rp['stop_loss']:,.2f}` · Son 5 yılda `%{bt['win_rate_pct']}` kazandırdı"
                )
        lines.append("🔎 _Bugün ne alınır, ne alınmaz?_ `/avci` | _Tek hisse sor:_ `/backtest ASELS`")
    except Exception:
        pass
    return "\n".join(lines)

def build_alpha_hunter_text(force_refresh: bool = False) -> str:
    scan = alpha_hunter.scan_alpha_universe(force_refresh=force_refresh)
    return alpha_hunter.format_alpha_scan_telegram(scan)

def build_asset_backtest_text(raw_symbol: str) -> str:
    return alpha_hunter.format_single_asset_telegram(raw_symbol)

def build_quant_regime_alert_text(budget_try: float = 6000.0) -> str:
    q = quant_engine.build_quant_radar(budget_try=budget_try, stress_mode="live", force_refresh=False)
    reg = q["overall_regime"]
    lvl = reg["level"]
    icon = "🔴" if lvl == "RED" else ("🟡" if lvl == "YELLOW" else "🟢")
    dyn = q["dynamic_plan"]
    funds = q["funds"]

    lines = [
        f"{icon} *ERKEN UYARI & PARA KORUMA RAPORU*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• *Durum:* {reg['title']}",
        f"• *Genel Piyasa Sağlığı:* `{reg['overall_score']}/100` (Dünya Korku Endeksi: `{reg['vix']}`)",
        "",
        "📊 *Her Fon İçin Tek Tek Durum (1 Aylık Beklenti):*",
    ]
    for code in ("AFT", "TI3", "TCA", "ZBJ"):
        f = funds.get(code, {})
        r = f.get("regime", {})
        t = f.get("technicals", {})
        p30 = t.get("proj_30d", {})
        lines.append(
            f"• *{code}* (`Sağlık {r.get('health_score', 50)}/100`): _{r.get('badge', '')}_\n"
            f"  └ 2,5 aylık ort. farkı `{t.get('dist_sma50_pct', 0):+.1f}%` · "
            f"1 ayda olası hareket `%{p30.get('p10_pct', 0):+.0f} .. %{p30.get('p90_pct', 0):+.0f}`"
        )

    lines.append("")
    lines.append(f"🎯 *Bu Ayki ₺{budget_try:,.0f} İçin Güvenli Dağıtım:*")
    for item in dyn.get("items", []):
        lines.append(f"  ├ *{item['code']}* (%{item['pct']:g}): `₺{item['amount_try']:,.0f}` (~{item['shares']:,} adet)")
    lines.append(f"💡 _{dyn.get('rebalance_advice', '')}_")

    bt = q.get("backtest", {})
    if bt.get("available"):
        lines.append("")
        lines.append(
            f"🧪 *Geçmiş Testi ({bt['trading_days']} gün):* Korumasız `+{bt['static_return_pct']}%` (en sert düşüş `{bt['static_max_drawdown_pct']}%`) vs "
            f"Akıllı Fren `+{bt['smart_return_pct']}%` (en sert düşüş `{bt['smart_max_drawdown_pct']}%`)"
        )
    return "\n".join(lines)

# ======================================================================
# GELİŞMİŞ DOĞAL DİL İŞLEM AYRIŞTIRICI (NLP / MULTI-PATTERN ENGINE)
# ======================================================================
def normalize_turkish_text(text: str) -> str:
    cleaned = text.strip()
    # Noktayla ayrılmış kelimeleri temizle (örn: "tlyi.napayim" -> "tlyi napayim", "parami.napayim" -> "parami napayim")
    # Ancak ondalık sayıları koru ("890.52" veya "6.61")
    cleaned = re.sub(r'(?<=[a-zA-ZçğıöşüÇĞİÖŞÜ])\.(?=[a-zA-ZçğıöşüÇĞİÖŞÜ])', ' ', cleaned)
    cleaned = re.sub(r'(?<=[a-zA-ZçğıöşüÇĞİÖŞÜ])\.(?=\d)', ' ', cleaned)
    cleaned = re.sub(r'(?<=\d)\.(?=[a-zA-ZçğıöşüÇĞİÖŞÜ])', ' ', cleaned)
    return cleaned

def to_tr_ascii(text: str) -> str:
    s = text.replace("İ", "i").replace("I", "ı").lower()
    mapping = {
        "ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"
    }
    for tr, asc in mapping.items():
        s = s.replace(tr, asc)
    return s

def parse_natural_command(text: str) -> Tuple[Optional[str], Dict[str, Any]]:
    clean_txt = normalize_turkish_text(text)
    t_lower = clean_txt.lower()
    t_ascii = to_tr_ascii(clean_txt)
    
    # 0. GERİ AL / DÜZELT / İPTAL TESPİTİ (UNDO - Sadece net işlem geri alma komutlarında)
    is_explicit_undo = False
    clean_stripped = clean_txt.strip().lower()
    if clean_stripped in ["duzelt", "düzelt", "/duzelt", "geri al", "gerial", "/geri_al", "/gerial", "iptal", "iptal et"]:
        is_explicit_undo = True
    elif any(phrase in t_ascii for phrase in [
        "son islemi sil", "son islemi geri al", "islemi sil", "islemi geri al", 
        "islemi iptal et", "yanlis kaydettin", "yanlis islendi geri al", "son kaydi sil"
    ]):
        is_explicit_undo = True
    
    # "botu düzelt", "kodu düzelt", "yazıyı düzelt", "pozisyon kısmı yanlış" gibi sistem/bot şikayetlerini asla UNDO yapma!
    if any(ignore_term in t_ascii for ignore_term in ["botu", "kodu", "sistemi", "yaziyi", "ekrani", "kismi", "dogru duzgun"]):
        is_explicit_undo = False

    if is_explicit_undo:
        return "UNDO_TX", {}

    # Soru ve Tavsiye / Portföy Danışmanlığı İpuçları
    recom_stems = [
        r"\bnapay", r"\bnapcam", r"\bnapcaz", r"\bnapalim", r"\bnapiyim", r"\bnapicaz",
        r"\bne yap", r"\bne al", r"\bne et", r"\bne dersin", r"\bne diyorsun",
        r"\bnereye yatir", r"\bnereye at", r"\bnereye koy", r"\bnereye bagla",
        r"\bnasil degerlendir", r"\bnasil yap", r"\bnasil bol", r"\bnasil pay",
        r"\boner", r"\btavsiye", r"\bfikrin", r"\btavsiyen",
        r"\bhangi fon", r"\bhangisini al", r"\bdagilim", r"\bbolustur",
        r"/oneri", r"/tavsiye"
    ]
    is_advice_query = any(re.search(stem, t_ascii) for stem in recom_stems)
    is_general_question = "?" in clean_txt or any(k in t_ascii for k in [
        "nasil", "nedir", "nerede", "nerde", "ne kadar", "acaba",
        " mi", " mi?", " mı", " mı?", " mu", " mu?", " mü", " mü?",
        "miyim", "mıyım", "muyum", "müyüm"
    ])

    # Hesap ve Fon Kodlarını Ayıkla (Sayı avcısına tutar gibi takılmasınlar)
    # Örn: "5003 hesabtan", "5003. hesap", "5003ten", "5004e", "5001'den", "2958 fonu"
    acc_clean_pattern = r"\b(?:5001|5004|5003|5002|2958)(?:(?:\'|\.)?(?:den|dan|ten|tan|e|ye|de|da|deki|te|ta|li|lu|nolu|hesap|hessap|hesaptan|hesabtan|hesaba|hesaptaki|hesabindaki))?\b|\b(?:5001|5004|5003|5002|2958)\b"
    t_clean_num = re.sub(acc_clean_pattern, " ", t_ascii, flags=re.IGNORECASE)
    t_clean_num = re.sub(r"\b(?:hessap|hesap|nolu|hesaptan|hesabtan)\b", " ", t_clean_num, flags=re.IGNORECASE)

    # Para Tutarı Tespiti (Öncelik: 897.13 TL, 897.13 Lira, ₺897.13)
    detected_amount = None
    curr_match = re.search(r"(?:₺\s*)?(\d+(?:[\.,]\d+)?)\s*(?:tl|lira|₺)\b", t_clean_num, re.IGNORECASE)
    if curr_match:
        try:
            detected_amount = round(abs(float(curr_match.group(1).replace(",", "."))), 2)
        except ValueError:
            pass

    if not detected_amount:
        k_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*k\b", t_clean_num, re.IGNORECASE)
        if k_match:
            try:
                detected_amount = round(float(k_match.group(1).replace(",", ".")) * 1000.0, 2)
            except ValueError:
                pass

    if not detected_amount:
        amt_match = re.search(r"[-+]?\s*(\d+(?:[\.,]\d+)?)", t_clean_num)
        if amt_match:
            raw_n = amt_match.group(1)
            if "." in raw_n and "," in raw_n:
                raw_n = raw_n.replace(".", "").replace(",", ".")
            elif "," in raw_n:
                raw_n = raw_n.replace(",", ".")
            try:
                val = round(abs(float(raw_n)), 2)
                if val > 0:
                    detected_amount = val
            except ValueError:
                pass

    # Kaynak / Hedef Hesap Tespiti (5003 her zaman 5004 Yatırım hesabına eşlenir)
    target_account = "5004"
    explicit_account = None
    if re.search(r"\b(?:5004|5003)\b", t_ascii):
        target_account = "5004"
        explicit_account = "5004"
    elif re.search(r"\b5001\b", t_ascii):
        target_account = "5001"
        explicit_account = "5001"
    elif re.search(r"\b5002\b", t_ascii):
        target_account = "5002"
        explicit_account = "5002"
    elif re.search(r"\bcuzdan\b|\bnakit\b", t_ascii):
        target_account = "cuzdan"
        explicit_account = "cuzdan"

    # Eylem Fiilleri Tespiti
    has_action_verb = any(w in t_ascii for w in [
        "al", "aldim", "aliyorum", "alindi", "sat", "satdim", "satiyorum", "satildi", "bozdum",
        "harca", "harcadim", "virman", "aktar", "yatti", "geldi", "ekle", "gitti", "odedim", "giris"
    ])

    # 1. AKILLI PORTFÖY & ALIM ÖNERİSİ
    if is_advice_query:
        return "RECOMMENDATION", {"amount": detected_amount}

    # 2. TEK BAŞINA HESAP SORGUSU (Örn: "5003", "5003. hesap", "5004", "5001 bakiye", "5003 ne kadar")
    if explicit_account and not has_action_verb and (detected_amount is None or clean_txt in ["5001", "5004", "5003", "5002", "cuzdan"] or any(k in t_ascii for k in ["bakiye", "durum", "ne kadar", "param", "kac", "hesap", "hessap"])):
        return "QUERY_ACCOUNT", {"account_id": explicit_account}

    # 2.9. HAFTALIK ÖZET KARNESİ ("haftalık özet", "/ozet")
    if any(k in t_ascii for k in ["haftalik ozet", "haftalik rapor", "test haftalik", "/ozet", "/haftalik"]):
        return "WEEKLY_SUMMARY", {}

    # 3. GENEL DURUM / ÖZET / POZİSYON SORGUSU
    status_keywords = [
        "bakiye", "durum", "ozet", "portfoy", "/bakiye", "/durum",
        "pozisyon", "pozisyonlar", "fonlar", "fonlarim", "fonlarım",
        "varlik", "varliklar", "varliklarim", "varlıklarım", "/pozisyon"
    ]
    if any(k in t_ascii for k in status_keywords) and not has_action_verb:
        return "STATUS", {}

    # 4. FIRSAT RADARI, HABERLER, TESTLER VE YARDIM
    if any(k in t_ascii for k in ["firsat", "dip", "sinyal", "radar", "/firsat", "/radar"]):
        return "OPPORTUNITY_RADAR", {}
    if any(k in t_ascii for k in ["test cuma", "cuma testi", "cuma uyarisi", "cuma alarmi", "/cuma"]):
        return "TEST_FRIDAY", {}
    if any(k in t_ascii for k in ["test gece", "gece testi", "gece nema", "gece raporu", "/gece"]):
        return "TEST_NIGHT", {}
    if any(k in t_ascii for k in ["haber", "gundem", "makro", "piyasa", "/haber", "/haberler"]):
        return "NEWS", {}
    if any(k in t_ascii for k in ["yardim", "komut", "/help", "/yardim", "/start"]):
        return "HELP", {}

    # 5. FON KODU / NUMARASI TESPİTİ
    fund_code = None
    fund_num = None
    if any(w in t_ascii for w in ["2958", "basak", "zbj", "958"]):
        fund_code = "ZBJ"
        fund_num = "2958"
    elif any(w in t_ascii for w in ["tzl", "para piyasasi", "ppf", "likit"]):
        fund_code = "TZL"
    elif any(w in t_ascii for w in ["tca", "altin"]):
        fund_code = "TCA"
    elif any(w in t_ascii for w in ["aft", "teknoloji", "yabanci"]):
        fund_code = "AFT"

    # Pay Adetlerini Yakala
    pay_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:pay|adet|lot)", t_clean_num, re.IGNORECASE)
    detected_shares = None
    if pay_match:
        try:
            detected_shares = round(float(pay_match.group(1).replace(",", ".")), 4)
        except ValueError:
            pass

    # 6. FON İŞLEMLERİ (ALIM / SATIM)
    if fund_code and not is_general_question and (has_action_verb or detected_amount or detected_shares):
        is_sell = any(w in t_ascii for w in ["sattim", "satildi", "bozdum", "satis", "sat", "cikar"])
        
        fallback_p = 5.630101 if fund_code == "ZBJ" else (0.149766 if fund_code == "TZL" else (0.755613 if fund_code == "TCA" else 0.899322))
        f_price = get_live_fund_price(fund_code, fallback_p)
        
        final_shares = detected_shares or 0.0
        final_amount = detected_amount or 0.0

        # Eğer ne tutar ne adet verilmişse (Örn: sadece "altın aldım" yazıldıysa)
        if final_amount <= 0 and final_shares <= 0:
            # Kullanıcı "tüm parayla", "hepsiyle", "kalanla" demiş mi?
            if any(w in t_ascii for w in ["hepsi", "tum", "tüm", "kalan", "bosta"]):
                accs = db.get_all_accounts()
                bal_target = accs.get(target_account, {}).get("balance", 0.0)
                bal_5004 = accs.get("5004", {}).get("balance", 0.0)
                avail_cash = bal_target if bal_target > 0 else bal_5004
                if avail_cash > 1.0:
                    final_amount = avail_cash
                else:
                    return "AMBIGUOUS_FUND_BUY", {"code": fund_code, "text": clean_txt}
            else:
                return "AMBIGUOUS_FUND_BUY", {"code": fund_code, "text": clean_txt}
        
        if final_amount > 0 and final_shares == 0.0 and f_price > 0:
            # TEFAS / Ziraat pay katı kuralına göre (örn. TZL=100, TCA=500, ZBJ=1) tam lot adedi hesapla
            math_res = market_service.compute_fund_order_math(
                code=fund_code,
                amount_try=final_amount,
                unit_price=f_price,
                is_sell=is_sell,
            )
            final_shares = math_res["shares"] if math_res["ok"] else float(int((final_amount + 1e-9) / f_price))
        elif final_shares > 0 and final_amount == 0.0:
            final_amount = round(final_shares * f_price, 2)

        desc_label = f"Ziraat Başak (2958) {int(final_shares):,} Pay Alımı" if fund_code == "ZBJ" else f"{fund_code} Fon Alımı ({int(final_shares):,} pay)"
        if is_sell:
            desc_label = f"{fund_code} Fon Satışı (Telegram)"
            return "TX_SATIS", {
                "amount_try": final_amount,
                "shares": final_shares,
                "price": f_price,
                "code": fund_code,
                "account_id": target_account,
                "desc": desc_label
            }
        else:
            return "TX_ALIM", {
                "amount_try": final_amount,
                "shares": final_shares,
                "price": f_price,
                "code": fund_code,
                "account_id": target_account,
                "desc": desc_label
            }

    # 7. VİRMAN TESPİTİ
    if any(w in t_ascii for w in ["virman", "aktar", "transfer"]) and not is_general_question:
        from_acc = "5001"
        to_acc = "5004"
        if "5004" in t_ascii and "5001" in t_ascii:
            if t_ascii.find("5004") < t_ascii.find("5001"):
                from_acc, to_acc = "5004", "5001"
        return "TX_VIRMAN", {
            "amount_try": detected_amount or 0.0,
            "account_id": from_acc,
            "to_account_id": to_acc,
            "desc": f"Kurum İçi Virman ({from_acc} → {to_acc})"
        }

    # 8. GELİR / GİRİŞ TESPİTİ
    if any(w in t_ascii for w in ["yatti", "geldi", "harclik", "burs", "kyk", "maas", "giris", "kazandim"]) and not is_general_question:
        desc = "Gelir / Harçlık"
        if "kyk" in t_ascii: desc = "KYK Bursu"
        elif "harclik" in t_ascii: desc = "Harçlık"
        elif "kira" in t_ascii: desc = "Kira Geliri"
        return "TX_GIRIS", {
            "amount_try": detected_amount or 0.0,
            "account_id": target_account if target_account != "5004" else "5001",
            "desc": f"{desc} (Telegram)"
        }

    # 9. GİDER / HARCAMA TESPİTİ
    expense_words = [
        "harcadim", "yedim", "ictim", "odedim", "verdim", "gitti", "harcama", "cikis", "masraf",
        "yemek", "doner", "kahve", "market", "tavuk", "kebap", "su", "sigara",
        "otobus", "bilet", "fatura", "benzin", "kitap", "kirtasiye", "taksi", "kira", "ogle", "aksam"
    ]
    has_expense_keyword = any(w in t_ascii for w in expense_words)

    if detected_amount and has_expense_keyword and not is_general_question:
        desc = clean_txt
        for w in expense_words + ["tl", "lira", "₺"]:
            desc = re.sub(re.escape(w), "", desc, flags=re.IGNORECASE)
        desc = re.sub(r"\d+(?:[\.,]\d+)?", "", desc).strip()
        words = [w for w in clean_txt.split() if not re.match(r"^[-+]?\d+", w) and w.lower() not in ["tl", "lira", "₺"]]
        item_name = " ".join(words).strip() or desc or "Harcama"
        return "TX_CIKIS", {
            "amount_try": detected_amount,
            "account_id": "5001",
            "desc": f"{item_name.capitalize()} (Telegram)"
        }

    # 10. DOĞRUDAN KASA / BAKİYE AYARLAMA (SET_BALANCE - KATI GÜVENLİK KALKANI)
    set_bal_keywords = ["bakiye ayarla", "bakiyesi", "olarak guncelle", "kasa bakiye", "bakiyem"]
    has_set_bal_phrase = any(k in t_ascii for k in set_bal_keywords)
    if explicit_account and detected_amount is not None and has_set_bal_phrase and not has_action_verb and not is_general_question:
        return "SET_BALANCE", {"account_id": explicit_account, "balance": detected_amount}

    # 11. Belirsiz Tutar Koruması
    if detected_amount and not is_general_question:
        return "AMBIGUOUS_AMOUNT", {"amount": detected_amount, "text": clean_txt}

    # Finansal Soru / Genel Sohbet
    return "CHAT", {"message": clean_txt}

# ======================================================================
# İŞLEM VE MESAJ İŞLEYİCİ
# ======================================================================
def process_incoming_text(chat_id: int, user_text: str) -> str:
    clean_strip = user_text.strip()
    
    # 0. ÖZEL YAPAY ZEKA VE ENTEGRASYON KOMUTLARI
    if clean_strip.startswith("/api_key") or clean_strip.startswith("/key"):
        parts = clean_strip.split(maxsplit=1)
        if len(parts) > 1:
            return ai_advisor.save_ai_key_from_telegram(parts[1])
        return ai_advisor.get_ai_status_text()
        
    if clean_strip in ["/ai", "/model", "/yapayzeka"]:
        return ai_advisor.get_ai_status_text()

    if clean_strip.lower() in ["/yedek", "yedek", "yedek al", "/backup"]:
        ok = send_json_backup(chat_id=chat_id, auto=False)
        if not ok:
            return "❌ Yedek dosyası oluşturulup gönderilemedi."
        return ""

    if clean_strip in ["/valor", "valor", "valör"]:
        pending = settlement_engine.enrich_pending_orders(db.get_pending_orders("BEKLIYOR"))
        if not pending:
            return "⏳ Şu an valörde / bekleyen bir fon emrin bulunmuyor."
        lines = ["⏳ *VALÖRDEKİ EMİRLER & TEFAS DURUMU*", "━━━━━━━━━━━━━━━━━━━━"]
        for po in pending:
            vs = po.get("valor_schedule", {})
            conf = vs.get("confirmed_price")
            p_txt = f"₺{conf:.6f} (Kesinleşti)" if conf else f"₺{po.get('live_price', 0):.4f} (Tahmini)"
            lines.append(
                f"• *{po['code']}* (`₺{float(po['amount_try']):,.2f}`)\n"
                f"  ├ Beklenen Takas: `{vs.get('expected_text', po.get('expected_settlement', ''))}`\n"
                f"  ├ TEFAS Fiyat: `{p_txt}` → `~{int(vs.get('projected_shares', 0)):,} pay`\n"
                f"  └ Durum: _{vs.get('stage_badge', 'Bekliyor')}_"
            )
        return "\n".join(lines)

    if clean_strip in ["/stopaj", "/vergi", "stopaj"]:
        tsum = tax_engine.build_portfolio_tax_summary()
        tot = tsum["totals"]
        bm = tsum["benchmarks"]
        lines = [
            "🧾 *STOPAJ & GERÇEK NET ÇIKIŞ ÖZETİ*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"• *Brüt Fon Değeri:* `₺{tot['gross_value_try']:,.2f}`",
            f"• *Tahmini Stopaj Kesintisi:* `-₺{tot['estimated_tax_try']:,.2f}`",
            f"• *Net Ele Geçecek (Çıkış):* `₺{tot['net_exit_value_try']:,.2f}`",
            f"• *Net Kâr / Zarar:* `{tot['net_profit_try']:+,.2f} TL ({tot['net_profit_pct']:+.2f}%)`",
            f"• *Dolar / Altın Eşdeğeri:* `${tot['net_value_usd']:,.1f}` | `{tot['net_value_gold_gr']:.2f} gr`",
            "",
            f"📊 *30G Kıyaslama:* PPF Net `+{bm['ppf_30d_net_pct']:.2f}%` vs USD `{bm['usd_30d_pct']:+.2f}%` vs Altın `{bm['altin_30d_pct']:+.2f}%`",
        ]
        return "\n".join(lines)

    if clean_strip.lower() in ["/tahmin", "/risk", "/kriz", "risk durumu", "tahmin", "erken uyarı", "erken uyari"]:
        return build_quant_regime_alert_text()

    if clean_strip.lower() in ["/avci", "/avcı", "/alpha", "avci", "avcı", "firsat avcisi", "fırsat avcısı", "en iyi firsat", "en iyi fırsat"]:
        return build_alpha_hunter_text(force_refresh=False)

    bt_match = re.match(r"^(?:/backtest|backtest|/analiz)\s+([A-Za-z0-9.\-_]+)", clean_strip, re.IGNORECASE)
    if bt_match:
        return build_asset_backtest_text(bt_match.group(1))

    fon_match = re.match(r"^(?:/fon|fon|/tefas)\s+([A-Za-z0-9]{2,6})", clean_strip, re.IGNORECASE)
    if fon_match:
        return tefas_analyzer.format_tefas_fund_telegram(fon_match.group(1))

    gecis_match = re.match(r"^(?:/gecis|/geçiş|gecis|geçiş|degistir|değiştir)\s+([A-Za-z0-9]{2,6})\s+([A-Za-z0-9]{2,6})(?:\s+(\d+(?:[.,]\d+)?))?", clean_strip, re.IGNORECASE)
    if gecis_match:
        amt_v = float(gecis_match.group(3).replace(",", ".")) if gecis_match.group(3) else None
        return tefas_analyzer.format_fund_switch_telegram(gecis_match.group(1), gecis_match.group(2), amount_try=amt_v)

    if clean_strip in ["/lig", "lig", "kıyasla", "kiyasla"]:
        lt = league_service.get_tefas_league_table(force_refresh=False)
        lines = [f"🏆 *TEFAS LİG TABLOSU* (`{lt.get('updated_at', '')}`)", "━━━━━━━━━━━━━━━━━━━━"]
        for lg in lt.get("leagues", []):
            lines.append(f"\n📌 *{lg['title']}*")
            for f in lg.get("funds", [])[:4]:
                mark = " 👈 *SENİN*" if f.get("is_owned") else ""
                lines.append(f"  `#{f['rank']}` *{f['code']}* (`{f['chg_30d']:+.2f}%` 30G | `{f['chg_7d']:+.2f}%` 7G){mark}")
            lines.append(f"  💬 _{lg['verdict']}_")
        return "\n".join(lines)

    dagit_match = re.match(r"^(?:/dagit|/dağıt|kyk yatt[ıi]|maa[şs] da[ğg][ıi]t)\s*(\d+(?:[.,]\d+)?)?", clean_strip.lower())
    if dagit_match:
        cfg_a = auto_allocator.get_allocator_config()
        amt_str = dagit_match.group(1)
        inc_val = float(amt_str.replace(",", ".")) if amt_str else float(cfg_a.get("income_try", 4000.0))
        plan = auto_allocator.preview_allocation(income_try=inc_val, living_reserve_try=cfg_a.get("living_reserve_try", 1500.0))
        lines = [
            f"💸 *AKILLI GELİR / KYK DAĞITIM PLANI (₺{plan['income_try']:,.2f})*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"• 🛡 *Yaşam Rezervi (`{plan['living_account_id']}`):* `₺{plan['living_reserve_try']:,.2f}`",
            f"• 🚀 *Yatırım Havuzu (`{plan['invest_account_id']}`):* `₺{plan['invest_pool_try']:,.2f}`",
        ]
        for o in plan["orders"]:
            v_tag = "⏳ Valör" if o["needs_valor"] else "⚡ Anlık"
            lines.append(f"  ├ *{o['code']}* (%{o['pct']}): `₺{o['actual_try']:,.2f}` (~{o['shares']:,} pay) • `{v_tag}`")
        keyboard = {
            "inline_keyboard": [[
                {"text": f"🚀 Hepsini Deftere İşle (₺{plan['income_try']:,.0f})", "callback_data": f"alloc_exec:{plan['income_try']}"},
            ]]
        }
        send_message("\n".join(lines), chat_id=chat_id, reply_markup=keyboard)
        return ""

    action, data = parse_natural_command(user_text)
    
    # 0.5. İŞLEM GERİ ALMA (UNDO / DÜZELT)
    if action == "UNDO_TX":
        reverted = db.revert_last_transaction()
        if not reverted:
            return (
                "ℹ️ *GERİ ALINACAK İŞLEM BULUNAMADI*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Defterde geri alınabilecek son bir işlem kaydı bulunmuyor."
            )
        t_desc = reverted.get("description", reverted.get("id"))
        t_amt = float(reverted.get("amount_try", 0.0))
        t_type = reverted.get("type", "")
        return (
            f"↩️ *İŞLEM BAŞARIYLA GERİ ALINDI (DÜZELTİLDİ)*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 *İptal Edilen:* {t_desc}\n"
            f"💰 *Tutar:* ₺{t_amt:,.2f} ({t_type})\n"
            f"🏦 *Kasa & Fon Bakiyesi:* Eski durumuna getirildi.\n"
            f"_SQLite ACID defterinden silindi._"
        )

    # 1. HESAP BAKİYESİ GÜNCELLEME (Doğrudan Kasa Düzeltme)
    if action == "SET_BALANCE":
        acc_id = data["account_id"]
        if acc_id == "5003":
            acc_id = "5004"
        new_bal = data["balance"]
        accounts = db.get_all_accounts()
        old_bal = accounts.get(acc_id, {}).get("balance", 0.0)
        acc_name = accounts.get(acc_id, {}).get("name", f"Hesap {acc_id}")
        
        db.update_account(acc_id, new_bal)
        diff = round(new_bal - old_bal, 2)
        if abs(diff) >= 0.01:
            tx = {
                "id": f"tx-tg-adj-{uuid.uuid4().hex[:6]}",
                "date": market_service.now_tr().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "GİRİŞ" if diff > 0 else "ÇIKIŞ",
                "category": "Bakiye Senkronizasyonu",
                "amount_try": abs(diff),
                "shares": 0,
                "price": 0,
                "code": "TRY",
                "account_id": acc_id,
                "to_account_id": None,
                "description": f"Telegram'dan Bakiye Güncelleme (₺{old_bal:.2f} → ₺{new_bal:.2f})"
            }
            db.insert_transaction(tx)
            
        return (
            f"✅ *HESAP BAKİYESİ GÜNCELLENDİ!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 *Hesap:* {acc_name}\n"
            f"💰 *Yeni Bakiye:* ₺{new_bal:,.2f}\n"
            f"📝 *Fark:* {('+' if diff > 0 else '')}₺{diff:,.2f}\n"
            f"_SQLite ACID defterine anında işlendi._"
        )

    # 2. TEK HESAP SORGUSU
    if action == "QUERY_ACCOUNT":
        acc_id = data["account_id"]
        if acc_id == "5003":
            acc_id = "5004"
        accounts = db.get_all_accounts()
        acc = accounts.get(acc_id)
        if acc:
            return (
                f"🏦 *HESAP BİLGİSİ*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• *Hesap:* {acc.get('name', acc_id)}\n"
                f"• *Bakiye:* ₺{acc.get('balance', 0.0):,.2f} ({acc.get('bank', 'Ziraat')})\n"
                f"• *Durum:* Aktif & Kullanılabilir"
            )
        return f"⚠️ '{acc_id}' nolu hesap veritabanında bulunamadı."

    # 2.5. TUTAR BELİRTİLMEYEN FON ALIMI (AMBIGUOUS_FUND_BUY)
    if action == "AMBIGUOUS_FUND_BUY":
        fcode = data.get("code", "TCA")
        fname = "Ziraat Altın Katılım Fonu (TCA)" if fcode == "TCA" else f"{fcode} Fonu"
        return (
            f"ℹ️ *{fname} ALIM TUTARI BELİRTİLMEDİ*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Furkan, kaç TL'lik veya kaç adet {fcode} fonu aldın?\n\n"
            f"💡 *Örnek yazımlar:*\n"
            f"• `897.13 TL ile altın aldım`\n"
            f"• `1187 pay altın aldım 5004`\n"
            f"• `5004 hesabındaki tüm parayla altın aldım`\n\n"
            f"_Net tutarı yazarsan deftere tam pay ve kuruşu kuruşuna işleyelim._"
        )

    # 3. GENEL PORTFÖY & BAKİYE RAPORU
    if action == "STATUS":
        accounts = db.get_all_accounts()
        funds = db.get_all_funds()
        total_cash = db.get_total_cash()
        
        funds_text = []
        total_funds_val = 0.0
        ppf_val = 0.0
        gold_val = 0.0
        for code, f in funds.items():
            sh = f.get("shares", 0.0)
            bp = f.get("buy_price", 0.0)
            curr_p = get_live_fund_price(code, bp)
            val = round(sh * curr_p, 2)
            total_funds_val += val
            if code in ["ZBJ", "TZL"]:
                ppf_val += val
            elif code == "TCA":
                gold_val += val
            if sh > 0:
                funds_text.append(f"• *{code}* ({f.get('name')}): {int(sh):,} pay ~ *₺{val:,.2f}*")
                
        pending_orders = db.get_pending_orders("BEKLIYOR")
        pending_total = db.get_pending_total_try()
        pending_lines = []
        for po in pending_orders:
            pending_lines.append(
                f"• *{po.get('code')}* (Valörde): ~{int(po.get('estimated_shares', 0)):,} pay • *₺{float(po.get('amount_try', 0)):,.2f}*"
            )

        total_nav = round(total_cash + total_funds_val + pending_total, 2)
        
        acc_text = []
        for aid, acc in accounts.items():
            acc_text.append(f"• *{acc.get('name', aid)}*: ₺{acc.get('balance', 0.0):,.2f}")
            
        ppf_rate = market_service.get_live_ppf_daily_rate()
        daily_nema = ppf_val * ppf_rate
        monthly_nema = daily_nema * 30.5

        pending_block = ""
        if pending_lines:
            pending_block = f"\n\n⏳ *Valörde / İşleme Girmedi (₺{pending_total:,.2f}):*\n" + "\n".join(pending_lines)

        reply = (
            f"🏛 *FİNANS ÖZETİ • CANLI DURUM*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 *Toplam Birikimin:* ₺{total_nav:,.2f}\n\n"
            f"💵 *Nakit Kasası:* ₺{total_cash:,.2f}\n" +
            "\n".join(acc_text) + "\n\n"
            f"📈 *Aktif Fon Portföyü:* ₺{total_funds_val:,.2f}\n" +
            ("\n".join(funds_text) if funds_text else "• Aktif fon bulunmuyor") +
            pending_block + "\n\n"
            f"🛡 *Günlük Kazanç (%{ppf_rate*100:.2f}/gün):* ~₺{daily_nema:.2f}/gün (₺{monthly_nema:.2f}/ay)\n"
            f"📅 _Haftalık karne için `/ozet` yazabilirsin._"
        )
        return reply

    # 4. CANLI PİYASA & MAKRO HABERLER
    if action == "NEWS":
        news_items = news_service.fetch_live_news()
        if not news_items:
            return "📡 Canlı haber akışında son dakika verisi bulunmuyor."
        lines = ["📰 *CANLI MAKRO & PİYASA GELİŞMELERİ*\n━━━━━━━━━━━━━━━━━━━━"]
        for i, n in enumerate(news_items[:5], 1):
            lines.append(f"*{i}. [{n['source']}]* {n['title']}\n_{n.get('description', '')[:100]}..._\n")
        lines.append("🤖 _Bu haberler LLM Konseyi analizine anlık dahil edilmektedir._")
        return "\n".join(lines)

    # 5. DİPTEN ALIM & FIRSAT RADARI
    if action == "OPPORTUNITY_RADAR":
        return build_opportunity_radar_text()

    # 6. AKILLI PORTFÖY & ALIM ÖNERİSİ (YAPAY ZEKA ENTEGRASYONU)
    if action == "RECOMMENDATION":
        amt = data.get("amount")
        return ai_advisor.ask_ai_advisor(user_text, custom_amount=amt)

    # 6.5. BELİRSİZ TUTAR (BAKİYE KORUMA KALKANI)
    if action == "AMBIGUOUS_AMOUNT":
        amt = data["amount"]
        return (
            f"🤔 *TUTAR TESPİT EDİLDİ: ₺{amt:,.2f}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Bu tutarla ne yapmak istediğini tam anlayamadım. Bakiyeni korumak için deftere işlem kaydetmedim.\n\n"
            f"💡 *Yatırım Tavsiyesi Al:* `₺{amt:g} ne yapayım` veya `ne alayım`\n"
            f"💸 *Harcama Kaydet:* `₺{amt:g} döner yedim` veya `market ₺{amt:g}`\n"
            f"💵 *Gelir Kaydet:* `₺{amt:g} harçlık geldi`\n"
            f"🏦 *Kasa Bakiyesi Ayarla:* `5001 bakiye ₺{amt:g}`"
        )

    # 7. TEST & SİMÜLASYON TETİKLEYİCİLERİ
    if action == "TEST_FRIDAY":
        return build_friday_alert_text()
    if action == "TEST_NIGHT":
        return build_night_nema_text()
    if action == "WEEKLY_SUMMARY":
        return build_weekly_summary_text()

    # 8. YARDIM VE REHBER
    if action == "HELP":
        return (
            f"🤖 *FİNANS TELEGRAM ASİSTANI & YAPAY ZEKA*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Slash komutları veya doğal dille mesaj atabilirsin:\n\n"
            f"📊 *Özet / Bakiye:* `/bakiye`, `/ozet`, `5001`, `5004`\n"
            f"⏳ *Valör & Stopaj:* `/valor` (TEFAS takas), `/stopaj` (net çıkış)\n"
            f"💾 *Yedek Alma:* `/yedek` (tam JSON dosyasını gönderir)\n"
            f"💸 *Gelir Dağıtımı:* `/dagit 4000` (ilk ay 8000) veya `kyk yattı`\n"
            f"🏆 *TEFAS Lig & Tahmin:* `/lig` (rakipler), `/tahmin` (erken uyarı), `/firsat`\n"
            f"🏦 *Tüm TEFAS & Geçiş:* `/fon MAC` (fon karnesi), `/gecis AFT YAY` (amorti)\n"
            f"🦅 *5Y Alpha Avcısı:* `/avci` (ne alınır/alınmaz), `/backtest ASELS`\n"
            f"🧠 *Yapay Zeka:* `/ai`, `/api_key <key>`, `şimdi ne önerirsin`\n"
            f"🛒 *Fon Alımı:* `2958 fonundan 21 pay aldım` veya `118 tl başak`\n"
            f"🍔 *Harcama / Gelir:* `65 döner`, `2000 kyk yattı`, `1000 virman`\n"
            f"🏦 *Bakiye Ayarla:* `5001 hesabında 890.52 tl var`\n"
            f"🔔 *Testler:* `test cuma`, `test gece`\n\n"
            f"_Tüm işlemler SQLite/Turso veritabanına ve web paneline anında işlenir._"
        )

    # 6. DEFTER İŞLEMLERİ (ALIM / SATIM / HARCAMA / GELİR / VİRMAN)
    if action.startswith("TX_"):
        accounts = db.get_all_accounts()
        funds = db.get_all_funds()
        tx_id = f"tx-tg-{uuid.uuid4().hex[:6]}"
        now_str = market_service.now_tr().strftime("%Y-%m-%d %H:%M:%S")
        amt = data["amount_try"]
        
        if action == "TX_ALIM":
            fcode = data["code"]
            acc_id = data["account_id"]
            if acc_id == "5003":
                acc_id = "5004"
            if acc_id not in accounts:
                acc_id = "5004" if "5004" in accounts else "5001"
            
            fallback_p = 5.630101 if fcode == "ZBJ" else (0.149766 if fcode == "TZL" else (0.755613 if fcode == "TCA" else 0.899322))
            f_price = data.get("price") or get_live_fund_price(fcode, fallback_p)
            shares = data.get("shares", 0.0)
            lot_step = market_service.get_fund_lot_step(fcode)
            math_res = market_service.compute_fund_order_math(
                code=fcode,
                amount_try=amt,
                unit_price=f_price,
                explicit_shares=shares if (shares > 0 and abs(amt - shares * f_price) < 0.5) else 0.0,
                is_sell=False,
            )
            if not math_res["ok"] and shares <= 0:
                return f"⚠️ *YETERSİZ TUTAR / LOT:* {math_res['error']}"
            if shares <= 0:
                shares = math_res["shares"]
                invested_try = math_res["effective_amt"]
            else:
                invested_try = round(amt if (amt > 0 and abs(amt - shares * f_price) < max(5.0, shares * f_price * 0.25)) else (shares * f_price), 2)
                if invested_try > 0 and shares > 0:
                    f_price = round(invested_try / shares, 6)
            leftover_cash = max(0.0, round(amt - invested_try, 2)) if amt > invested_try else 0.0
                
            cur_bal = accounts.get(acc_id, {}).get("balance", 0.0)
            bal_5001 = accounts.get("5001", {}).get("balance", 0.0)
            
            account_note = ""
            deduct_amt = invested_try  # Bankadan sadece gerçekleşen fon bedeli düşer, küsürat nakitte kalır
            if acc_id == "5004" and cur_bal < deduct_amt:
                diff = round(deduct_amt - cur_bal, 2)
                if round(cur_bal + bal_5001, 2) >= round(deduct_amt - 0.05, 2):
                    db.update_account("5004", 0.0)
                    new_5001 = max(0.0, round(bal_5001 - diff, 2))
                    db.update_account("5001", new_5001)
                    account_note = f"Ziraat 5004 (₺{cur_bal:,.2f}) + 5001'den Virman (₺{diff:,.2f})"
                else:
                    return (
                        f"⚠️ *YETERSİZ NAKİT BAKİYESİ!*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"Gereken Fon Tutarı: ₺{deduct_amt:,.2f} ({int(shares):,} pay {fcode})\n"
                        f"Toplam Kullanılabilir Nakit: ₺{(cur_bal + bal_5001):,.2f}\n"
                        f"Bakiyen yetersiz olduğu için işlem yapılmadı."
                    )
            else:
                new_bal = max(0.0, round(cur_bal - deduct_amt, 2))
                db.update_account(acc_id, new_bal)
                account_note = f"{accounts.get(acc_id, {}).get('name', acc_id)} (Kalan: ₺{new_bal:,.2f})"
            
            fname = "Ziraat Başak Para Piyasası Fonu (2958)" if fcode == "ZBJ" else (funds.get(fcode, {}).get("name") or fcode)
            now_dt = now_tr()
            after_cutoff = (now_dt.weekday() >= 5) or (now_dt.hour > 13) or (now_dt.hour == 13 and now_dt.minute >= 30)
            needs_valor = after_cutoff or (fcode in ("TCA", "AFT", "KZL", "TI3"))

            if needs_valor:
                sched = settlement_engine.compute_valor_schedule(fcode, now_str)
                db.create_pending_order(
                    code=fcode,
                    amount_try=deduct_amt,
                    estimated_shares=shares,
                    estimated_price=f_price,
                    account_id=acc_id,
                    fund_name=fname,
                    expected_settlement=sched["expected_text"],
                    note=f"{fcode} Fon Alım Emri (~{int(shares):,} pay - ₺{deduct_amt:,.2f})",
                    deduct_cash=False,
                    record_tx=True,
                )
                return (
                    f"⏳ *FON ALIM EMRİ VALÖRDE KAYDEDİLDİ!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 *Fon:* {fcode} ({fname})\n"
                    f"💰 *Ayrılan Tutar:* ₺{deduct_amt:,.2f} (~{int(shares):,} tahmini pay)\n"
                    f"📅 *Beklenen Takas:* `{sched['expected_text']}`\n"
                    f"🪙 *Kalan Küsürat Nakit:* ₺{leftover_cash:,.2f}\n"
                    f"💳 *Kaynak:* {account_note}\n"
                    f"🛡 _TEFAS fiyatı kesinleşince otomatik uyarı alacaksın; o zamana dek sahte kâr/zarar yazılmaz._"
                )

            cur_shares = funds.get(fcode, {}).get("shares", 0.0)
            db.update_fund(fcode, round(cur_shares + shares, 4), f_price, fname, weighted_avg=True)
            
            tx = {
                "id": tx_id,
                "date": now_str,
                "type": "ALIM",
                "category": "Fon İşlemi",
                "amount_try": deduct_amt,
                "shares": shares,
                "price": f_price,
                "code": fcode,
                "account_id": acc_id,
                "to_account_id": None,
                "description": f"{fcode} Fon Alımı ({int(shares):,} pay)"
            }
            db.insert_transaction(tx)
            return (
                f"✅ *FON ALIMI DEFTERE İŞLENDİ!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 *Fon:* {fcode} ({fname})\n"
                f"💰 *Gerçekleşen Tutar:* ₺{deduct_amt:,.2f}\n"
                f"📊 *Alınan Pay:* {int(shares):,} adet tam pay (Birim: ₺{f_price:.4f})\n"
                f"🪙 *Kalan Küsürat Nakit:* ₺{leftover_cash:,.2f} (Hesabında güvende)\n"
                f"💳 *Kaynak:* {account_note}\n"
                f"⏱ *Zaman:* {now_str}\n\n"
                f"_İşlemi iptal etmek istersen 'işlemi geri al' yazabilirsin._"
            )

        elif action == "TX_SATIS":
            fcode = data["code"]
            acc_id = data["account_id"]
            if acc_id == "5003":
                acc_id = "5004"
            if acc_id not in accounts:
                acc_id = "5004" if "5004" in accounts else "5001"
            
            cur_shares = funds.get(fcode, {}).get("shares", 0.0)
            if cur_shares <= 0:
                return f"⚠️ *SATIŞ YAPILAMADI:* Portföyünde şu anda hiç *{fcode}* fonu bulunmuyor."

            fallback_p = 5.630101 if fcode == "ZBJ" else (0.149766 if fcode == "TZL" else (0.755613 if fcode == "TCA" else 0.899322))
            f_price = data.get("price") or get_live_fund_price(fcode, fallback_p)
            shares = data.get("shares", 0.0)
            sell_res = market_service.compute_fund_order_math(
                code=fcode,
                amount_try=amt,
                unit_price=f_price,
                explicit_shares=shares,
                is_sell=True,
                held_shares=cur_shares,
            )
            if not sell_res["ok"]:
                return f"⚠️ *SATIŞ YAPILAMADI:* {sell_res['error']}"
            shares = sell_res["shares"]
            amt = sell_res["effective_amt"]
                
            cur_bal = accounts.get(acc_id, {}).get("balance", 0.0)
            new_bal = round(cur_bal + amt, 2)
            db.update_account(acc_id, new_bal)
            
            new_shares = max(0.0, round(cur_shares - shares, 4))
            fname = "Ziraat Başak Para Piyasası Fonu (2958)" if fcode == "ZBJ" else (funds.get(fcode, {}).get("name") or fcode)
            db.update_fund(fcode, new_shares, None, fname)
            
            tx = {
                "id": tx_id,
                "date": now_str,
                "type": "SATIM",
                "category": "Fon Satışı",
                "amount_try": amt,
                "shares": shares,
                "price": f_price,
                "code": fcode,
                "account_id": acc_id,
                "to_account_id": None,
                "description": f"{fcode} Fon Satışı ({int(shares):,} pay)"
            }
            db.insert_transaction(tx)
            return (
                f"💰 *FON SATIŞI DEFTERE İŞLENDİ!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 *Fon:* {fcode} ({fname})\n"
                f"💵 *Giren Nakit:* ₺{amt:,.2f}\n"
                f"📊 *Satılan Pay:* {int(shares):,} adet (Kalan Pay: {int(new_shares):,})\n"
                f"💳 *Yatırılan Hesap:* {accounts.get(acc_id, {}).get('name', acc_id)} (Yeni Bakiye: ₺{new_bal:,.2f})\n"
                f"⏱ *Zaman:* {now_str}\n\n"
                f"_İşlemi iptal etmek istersen 'işlemi geri al' yazabilirsin._"
            )

        elif action == "TX_CIKIS":
            acc_id = data["account_id"]
            if acc_id not in accounts:
                acc_id = "5001"
            cur_bal = accounts.get(acc_id, {}).get("balance", 0.0)
            new_bal = max(0.0, cur_bal - amt)
            db.update_account(acc_id, new_bal)
            
            tx = {
                "id": tx_id,
                "date": now_str,
                "type": "ÇIKIŞ",
                "category": "Harcama",
                "amount_try": amt,
                "shares": 0,
                "price": 0,
                "code": "TRY",
                "account_id": acc_id,
                "to_account_id": None,
                "description": data["desc"]
            }
            db.insert_transaction(tx)
            return (
                f"💸 *HARCAMA DEFTERE İŞLENDİ!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔻 *Tutar:* ₺{amt:,.2f}\n"
                f"📝 *Açıklama:* {data['desc']}\n"
                f"💳 *Hesap:* {accounts.get(acc_id, {}).get('name', acc_id)} (Kalan: ₺{new_bal:,.2f})\n"
                f"⏱ *Zaman:* {now_str}"
            )

        elif action == "TX_GIRIS":
            acc_id = data["account_id"]
            if acc_id not in accounts:
                acc_id = "5001"
            cur_bal = accounts.get(acc_id, {}).get("balance", 0.0)
            new_bal = cur_bal + amt
            db.update_account(acc_id, new_bal)
            
            tx = {
                "id": tx_id,
                "date": now_str,
                "type": "GİRİŞ",
                "category": "Gelir",
                "amount_try": amt,
                "shares": 0,
                "price": 0,
                "code": "TRY",
                "account_id": acc_id,
                "to_account_id": None,
                "description": data["desc"]
            }
            db.insert_transaction(tx)
            return (
                f"🎉 *KASAYA NAKİT GİRİŞİ OLDU!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"➕ *Giren Tutar:* ₺{amt:,.2f}\n"
                f"📝 *Açıklama:* {data['desc']}\n"
                f"💳 *Hesap:* {accounts.get(acc_id, {}).get('name', acc_id)} (Yeni Bakiye: ₺{new_bal:,.2f})\n"
                f"⏱ *Zaman:* {now_str}"
            )

        elif action == "TX_VIRMAN":
            from_acc = data["account_id"]
            to_acc = data["to_account_id"]
            if from_acc not in accounts: from_acc = "5001"
            if to_acc not in accounts: to_acc = "5004"
            
            from_bal = accounts.get(from_acc, {}).get("balance", 0.0)
            to_bal = accounts.get(to_acc, {}).get("balance", 0.0)
            
            new_from_bal = max(0.0, from_bal - amt)
            new_to_bal = to_bal + amt
            db.update_account(from_acc, new_from_bal)
            db.update_account(to_acc, new_to_bal)
            
            tx = {
                "id": tx_id,
                "date": now_str,
                "type": "VIRMAN",
                "category": "Virman",
                "amount_try": amt,
                "shares": 0,
                "price": 0,
                "code": "TRY",
                "account_id": from_acc,
                "to_account_id": to_acc,
                "description": data["desc"]
            }
            db.insert_transaction(tx)
            return (
                f"🔄 *VİRMAN TAMAMLANDI!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔀 *Tutar:* ₺{amt:,.2f}\n"
                f"📤 *Kaynak:* {accounts.get(from_acc, {}).get('name', from_acc)} (Kalan: ₺{new_from_bal:,.2f})\n"
                f"📥 *Hedef:* {accounts.get(to_acc, {}).get('name', to_acc)} (Yeni: ₺{new_to_bal:,.2f})\n"
                f"⏱ *Zaman:* {now_str}"
            )

    # 7. YAPAY ZEKA SOHBET & DANIŞMANLIK (GEMINI / SİNİRSEL BEYİN)
    return ai_advisor.ask_ai_advisor(user_text)

# ======================================================================
# ARKA PLAN POLLING WORKER & BULUT MUTEX KORUMASI
# ======================================================================
_WORKER_THREAD: Optional[threading.Thread] = None
_RUNNING = False

def is_cloud_instance_active() -> bool:
    """Uzak bulut sunucusunun aktif çalışıp çalışmadığını kontrol eder."""
    if os.environ.get("RENDER"):
        return False
    if os.environ.get("FORCE_LOCAL_TELEGRAM_POLL") == "1":
        return False
    remote_url = os.environ.get("RENDER_REMOTE_URL", "").strip().rstrip("/")
    if not remote_url:
        return False
    try:
        req = urllib.request.Request(
            f"{remote_url}/api/auth/status",
            headers={"User-Agent": "LocalFinansInstance"}
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    return False

def _handle_single_update(u: Dict[str, Any], auth_chats: set) -> Optional[int]:
    up_id = u.get("update_id")
    if up_id is not None:
        try:
            if not db.mark_update_processed(int(up_id)):
                return int(up_id)
        except Exception:
            pass

    cq = u.get("callback_query")
    if cq:
        cq_id = cq.get("id")
        cq_msg = cq.get("message", {})
        chat_id = cq_msg.get("chat", {}).get("id")
        cb_data = str(cq.get("data", "")).strip()
        cfg = load_config()
        token = cfg.get("bot_token", "").strip()
        if not chat_id or chat_id not in auth_chats:
            if cq_id and token:
                tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "Yetkisiz erişim!"}, token=token)
            return int(up_id) if up_id is not None else None

        if cb_data.startswith("rec_exec:"):
            rule_id = cb_data.split(":", 1)[1]
            rule = next((r for r in db.get_recurring_rules() if r["id"] == rule_id), None)
            if rule:
                execute_recurring_rule(rule)
                if cq_id and token:
                    tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "✅ İşlem deftere işlendi!"}, token=token)
                send_message(
                    f"✅ *SABİT AKIŞ İŞLENDİ*\n• {rule['title']}: *₺{rule['amount_try']:,.2f}* ({rule['type']})\n• Hesap: `{rule['account_id']}`",
                    chat_id=chat_id,
                )
            elif cq_id and token:
                tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "Kural bulunamadı."}, token=token)
        elif cb_data.startswith("rec_skip:"):
            rule_id = cb_data.split(":", 1)[1]
            cur_ym = market_service.now_tr().strftime("%Y-%m")
            db.mark_recurring_triggered(rule_id, cur_ym)
            if cq_id and token:
                tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⏭ Bu ay atlandı"}, token=token)
            send_message("⏭ İlgili sabit akış bu ay için atlandı.", chat_id=chat_id)
        elif cb_data.startswith("valor_settle:"):
            oid = cb_data.split(":", 1)[1]
            orders = db.get_pending_orders("BEKLIYOR")
            target = next((o for o in orders if o["id"] == oid), None)
            if target:
                enriched = settlement_engine.enrich_pending_order(target)
                vs = enriched.get("valor_schedule", {})
                use_p = vs.get("confirmed_price") or enriched.get("live_price")
                res = db.settle_pending_order(oid, settled_price=use_p, settled_shares=vs.get("projected_shares"))
                if cq_id and token:
                    tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "✅ Portföye geçirildi!"}, token=token)
                send_message(
                    f"✅ *VALÖR ONAYLANDI ({res['code']})*\n"
                    f"• *Gerçekleşen Pay:* `{int(res['settled_shares']):,} adet`\n"
                    f"• *TEFAS Fiyatı:* `₺{res['settled_price']:.6f}`\n"
                    f"• *Artan Kuruş İadesi:* `₺{res['refund_krs']:.2f}`",
                    chat_id=chat_id,
                )
            elif cq_id and token:
                tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "Emir zaten işlenmiş."}, token=token)
        elif cb_data.startswith("valor_wait:"):
            if cq_id and token:
                tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⏳ Anlaşıldı, beklemede tutuluyor"}, token=token)
        elif cb_data.startswith("alloc_exec:"):
            try:
                inc_amt = float(cb_data.split(":", 1)[1])
                cfg_a = auto_allocator.get_allocator_config()
                res = auto_allocator.execute_allocation(
                    income_try=inc_amt,
                    living_reserve_try=float(cfg_a.get("living_reserve_try", 1500.0)),
                    record_income=True,
                )
                if cq_id and token:
                    tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🚀 Dağıtım uygulandı!"}, token=token)
                po_cnt = len(res.get("pending_orders_created", []))
                inst_cnt = len(res.get("instant_buys", []))
                send_message(
                    f"✅ *AKILLI DAĞITIM TAMAMLANDI!*\n"
                    f"• *Yaşam Rezervi:* `₺{res['plan']['living_reserve_try']:,.2f}`\n"
                    f"• *Anlık İşlenen Fon:* `{inst_cnt}` | *Valöre Alınan:* `{po_cnt}`",
                    chat_id=chat_id,
                )
            except Exception as exc:
                if cq_id and token:
                    tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Hata: {exc}"[:60]}, token=token)
        return int(up_id) if up_id is not None else None

    msg = u.get("message") or u.get("edited_message")
    if not msg:
        return int(up_id) if up_id is not None else None

    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = msg.get("text", "").strip()

    if not chat_id or not text:
        return int(up_id) if up_id is not None else None

    # Sıkı Güvenlik Kontrolü: Sadece Furkan (Yetkili Chat ID)
    if chat_id not in auth_chats:
        from_user = msg.get("from", {})
        user_tag = from_user.get("username", from_user.get("first_name", "Bilinmeyen"))

        send_message(
            "⛔ *ERİŞİM REDDEDİLDİ*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Bu bot yetkili kullanıcıya ait şifreli bir finans asistanıdır.\n"
            "Yetkiniz bulunmadığı için komutlar işlenemez ve veri görüntülenemez.",
            chat_id=chat_id
        )

        for admin_id in auth_chats:
            send_message(
                f"🚨 *GÜVENLİK ALARMI // YETKİSİZ ERİŞİM DENEMESİ*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *Kullanıcı:* @{user_tag} (Chat ID: `{chat_id}`)\n"
                f"💬 *Yazdığı Mesaj:* `{text}`\n"
                f"🛡 *Durum:* Sistem tarafından engellendi, hiçbir veri sızdırılmadı.",
                chat_id=admin_id
            )
        return int(up_id) if up_id is not None else None

    reply_text = process_incoming_text(chat_id, text)
    if reply_text:
        send_message(reply_text, chat_id=chat_id)
    return int(up_id) if up_id is not None else None


def process_webhook_update(u: Dict[str, Any]) -> Dict[str, Any]:
    """Telegram Webhook üzerinden gelen güncellemeyi tekilleştirerek işler."""
    cfg = load_config()
    auth_chats = set(cfg.get("authorized_chat_ids", []))
    up_id = _handle_single_update(u, auth_chats)
    if up_id is not None and up_id > cfg.get("last_update_id", 0):
        cfg["last_update_id"] = up_id
        save_config(cfg)
    return {"status": "ok", "update_id": up_id}


_COMMANDS_SYNCED = False


def _sync_bot_commands_once(token: str):
    global _COMMANDS_SYNCED
    if _COMMANDS_SYNCED or not token:
        return
    commands = [
        {"command": "ozet", "description": "1 dakikalık haftalık özet karnesi"},
        {"command": "bakiye", "description": "Toplam paran, nakit ve fonların"},
        {"command": "valor", "description": "Valördeki emirler & TEFAS durumu"},
        {"command": "stopaj", "description": "Stopaj düşülmüş gerçek net çıkış"},
        {"command": "dagit", "description": "KYK / Gelir akıllı dağıtım sihirbazı"},
        {"command": "lig", "description": "TEFAS lig tablosu (rakiplerle kıyasla)"},
        {"command": "tahmin", "description": "Erken uyarı, VIX & düşen bıçak tahmini"},
        {"command": "firsat", "description": "Altın ve Teknoloji düşüş fırsatı radarı"},
        {"command": "avci", "description": "Bugün ne alınır, ne alınmaz (5Y kontrol)"},
        {"command": "backtest", "description": "Tek hisse/coin 5Y geçmiş & stop analizi"},
        {"command": "fon", "description": "Herhangi bir TEFAS fonunu 1Y & ZBJ kıyasla"},
        {"command": "gecis", "description": "Fon değişimi stopaj+valör amorti hesabı"},
        {"command": "haberler", "description": "Güncel ekonomi gündemi"},
        {"command": "ai", "description": "Yapay zekâ asistan durumu"},
        {"command": "yardim", "description": "Kullanabileceğin tüm doğal dil örnekleri"},
    ]
    try:
        res = tg_api_call("setMyCommands", {"commands": json.dumps(commands, ensure_ascii=False)}, token=token, http_timeout=5.0)
        if res is not None:
            _COMMANDS_SYNCED = True
    except Exception:
        pass


def bot_polling_loop():
    global _RUNNING
    print("[Telegram Bot] Polling iş parçacığı başlatıldı.")
    last_friday_notif = None
    last_night_notif = None
    last_daily_notif = None
    last_dip_notif = None
    notified_rec_today = set()
    last_cloud_check = 0.0
    last_valor_check = 0.0
    last_quant_check = 0.0
    last_alpha_check = 0.0
    cloud_active = False
    
    while _RUNNING:
        # Çift Mesaj ve Veri Çakışması Koruması (Local vs Cloud Mutex):
        # Eğer Render bulut servisi çalışıyorsa, yerel bilgisayardaki dinleyiciyi beklemeye al!
        if not os.environ.get("RENDER") and not os.environ.get("FORCE_LOCAL_TELEGRAM_POLL"):
            now_sec = time.time()
            if now_sec - last_cloud_check > 25:
                last_cloud_check = now_sec
                cloud_active = is_cloud_instance_active()
                if cloud_active:
                    print("[Telegram Bot] Uzak bulut sunucusu aktif. Çift mesaj atmamak için yerel dinleyici uyku modunda.")
            if cloud_active:
                time.sleep(5)
                continue

        cfg = load_config()
        token = cfg.get("bot_token", "").strip()
        
        if not token:
            time.sleep(3)
            continue

        _sync_bot_commands_once(token)
        now_dt = market_service.now_tr()
        cur_date_str = now_dt.strftime("%Y-%m-%d")
        
        # 1. Cuma 12:45 "3 Günlük Nema" Kapanış Uyarısı (Hafta sonu kapanışına 45 dk kala)
        if now_dt.weekday() == 4 and now_dt.hour == 12 and 45 <= now_dt.minute <= 55:
            if last_friday_notif != cur_date_str:
                send_message(build_friday_alert_text())
                last_friday_notif = cur_date_str

        # 2. Gece 00:05 Günlük Nema & Kazanç Raporu
        if now_dt.hour == 0 and 5 <= now_dt.minute <= 15:
            if last_night_notif != cur_date_str:
                send_message(build_night_nema_text())
                last_night_notif = cur_date_str

        # 3. Günlük Dipten Alım Fırsatı Radarı (Hafta içi 10:15 - TEFAS seans açılış kontrolü)
        if now_dt.weekday() < 5 and now_dt.hour == 10 and 15 <= now_dt.minute <= 25:
            if last_dip_notif != cur_date_str:
                send_message(build_opportunity_radar_text())
                last_dip_notif = cur_date_str

        # 3b. Aylık Sabit Akış & Kira/KYK Onay Hatırlatıcısı (09:30'dan itibaren günü gelenler)
        if now_dt.hour >= 9 and (now_dt.hour > 9 or now_dt.minute >= 30):
            cur_ym = now_dt.strftime("%Y-%m")
            for rule in db.get_recurring_rules():
                r_key = f"{rule['id']}:{cur_date_str}"
                if (
                    rule.get("enabled")
                    and int(rule.get("day_of_month", 1)) == now_dt.day
                    and rule.get("last_triggered_month") != cur_ym
                    and r_key not in notified_rec_today
                ):
                    notified_rec_today.add(r_key)
                    keyboard = {
                        "inline_keyboard": [[
                            {"text": "✅ Şimdi İşle", "callback_data": f"rec_exec:{rule['id']}"},
                            {"text": "⏭ Bu Ayı Atla", "callback_data": f"rec_skip:{rule['id']}"}
                        ]]
                    }
                    send_message(
                        f"📅 *AYLIK SABİT AKIŞ HATIRLATICISI*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"• *Başlık:* {rule['title']}\n"
                        f"• *Tür / Kategori:* {rule['type']} ({rule['category']})\n"
                        f"• *Tutar:* ₺{float(rule['amount_try']):,.2f}\n"
                        f"• *Hesap:* `{rule['account_id']}`\n\n"
                        f"Deftere işleyeyim mi?",
                        reply_markup=keyboard,
                    )

        # 3c. Kişisel Fiyat & Düşüş Alarmları (4 saatlik bekleme süresi ile Telegram uyarısı)
        try:
            mkt = market_service.fetch_live_market()
            funds_m = mkt.get("funds", {})
            macro_m = mkt.get("macro", {})
            for al in db.get_price_alerts():
                if not al.get("enabled", 1):
                    continue
                last_trig = al.get("last_triggered_at")
                if last_trig:
                    try:
                        prev_dt = datetime.datetime.strptime(last_trig, "%Y-%m-%d %H:%M:%S")
                        if (now_dt - prev_dt).total_seconds() < 14400:
                            continue
                    except Exception:
                        pass
                code = str(al.get("code", "")).upper()
                cond = str(al.get("condition", "BELOW")).upper()
                thr = float(al.get("threshold", 0.0))
                cur_price = None
                cur_chg = None
                cur_drawdown = None
                if code in funds_m:
                    cur_price = float(funds_m[code].get("price", 0.0))
                    cur_chg = float(funds_m[code].get("daily_yield_pct", 0.0))
                    cur_drawdown = float(funds_m[code].get("drawdown_from_high_pct", 0.0))
                elif code in ("GRAM_ALTIN", "XAU", "ALTIN"):
                    cur_price = float(macro_m.get("gram_altin_try", 0.0))
                    cur_chg = float(macro_m.get("gram_altin_chg_pct", 0.0) or 0.0)
                    g_ser = [float(x.get("price", 0)) for x in (macro_m.get("gram_altin_series") or []) if float(x.get("price", 0)) > 0]
                    if g_ser and cur_price > 0:
                        peak = max(max(g_ser), cur_price)
                        cur_drawdown = round(((cur_price - peak) / peak) * 100.0, 2)
                elif code in ("USD", "USD_TRY", "DOLAR"):
                    cur_price = float(macro_m.get("usd_try", 0.0))
                    cur_chg = float(macro_m.get("usd_chg_pct", 0.0) or 0.0)
                    u_ser = [float(x.get("price", 0)) for x in (macro_m.get("usd_series") or []) if float(x.get("price", 0)) > 0]
                    if u_ser and cur_price > 0:
                        peak = max(max(u_ser), cur_price)
                        cur_drawdown = round(((cur_price - peak) / peak) * 100.0, 2)
                if cur_price is None or cur_price <= 0:
                    continue
                fired = False
                detail = ""
                if cond == "BELOW" and cur_price <= thr:
                    fired = True
                    detail = f"Fiyat ₺{thr:.4f} hedefinin altına düştü (Güncel: ₺{cur_price:.4f})."
                elif cond == "ABOVE" and cur_price >= thr:
                    fired = True
                    detail = f"Fiyat ₺{thr:.4f} hedefinin üzerine çıktı (Güncel: ₺{cur_price:.4f})."
                elif cond == "DROP_PCT" and cur_chg is not None and cur_chg <= -abs(thr):
                    fired = True
                    detail = f"Günlük düşüş -%{abs(thr):.2f} sınırını geçti (Bugün: %{cur_chg:.2f}, ₺{cur_price:.4f})."
                elif cond == "DRAWDOWN_PCT" and cur_drawdown is not None and cur_drawdown <= -abs(thr):
                    fired = True
                    detail = f"Zirveden geri çekilme -%{abs(thr):.2f} fırsat bölgesine ulaştı (Zirveye göre: %{cur_drawdown:.2f}, ₺{cur_price:.4f})."
                if fired:
                    note_txt = f"\n• *Not:* {al['note']}" if al.get("note") else ""
                    send_message(
                        f"🔔 *FİYAT ALARMI TETİKLENDİ ({code})*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"{detail}{note_txt}"
                    )
                    db.mark_price_alert_triggered(al["id"], now_dt.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as e:
            print(f"[Telegram Alert Check Err]: {e}")

        # 3d. Otomatik Valör & TEFAS Fiyat Kontrolü (Hafta içi 180 sn aralıkla)
        try:
            now_mono = time.time()
            if now_mono - last_valor_check > 180:
                last_valor_check = now_mono
                settlement_engine.run_valor_automation_tick(
                    notify_fn=lambda txt, kb: send_message(txt, reply_markup=kb)
                )
        except Exception as e:
            print(f"[Telegram Valor Check Err]: {e}")

        # 3e. Otomatik Piyasa Rejimi & Çöküş Erken Uyarısı (30 dk aralıkla sadece seviye değişince)
        try:
            now_mono = time.time()
            if now_mono - last_quant_check > 1800:
                last_quant_check = now_mono
                q_res = quant_engine.build_quant_radar(budget_try=6000.0, stress_mode="live", force_refresh=False)
                cur_lvl = q_res["overall_regime"]["level"]
                prev_lvl = (db.get_setting("last_quant_regime_alert") or "").strip()
                if not prev_lvl:
                    # İlk açılışta sessizce mevcut rejimi kaydet; sadece kritik RED ise haber ver
                    db.set_setting("last_quant_regime_alert", cur_lvl, sync_cloud=True)
                    if cur_lvl == "RED" and 9 <= now_dt.hour <= 22:
                        send_message(build_quant_regime_alert_text())
                elif cur_lvl != prev_lvl and 8 <= now_dt.hour <= 23:
                    db.set_setting("last_quant_regime_alert", cur_lvl, sync_cloud=True)
                    send_message(f"⚡ *REJİM DEĞİŞİMİ TESPİT EDİLDİ ({prev_lvl} → {cur_lvl})*\n\n" + build_quant_regime_alert_text())
        except Exception as e:
            print(f"[Telegram Quant Check Err]: {e}")

        # 3f. Proaktif Asimetrik Fırsat & Dip Dönüşü Avcısı (45 dk aralıkla, 24 saat cooldown korumalı)
        try:
            now_mono = time.time()
            if now_mono - last_alpha_check > 2700 and 9 <= now_dt.hour <= 22:
                last_alpha_check = now_mono
                alerts = alpha_hunter.get_new_high_conviction_alerts(cooldown_hours=24.0)
                for item in alerts:
                    bt = item["active_backtest"]
                    rp = item["risk_plan"]
                    cur = item["currency"]
                    sym_c = "₺" if cur == "TRY" else "$"
                    send_message(
                        f"🚨 *ALINABİLİR FIRSAT: {item['symbol']}* ({item['name']})\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"• *Ne Oldu?:* {item.get('plain_reason', '')}\n"
                        f"• *Güncel Fiyat:* `{sym_c}{item['price']:,.2f}` (`Bugün {item['chg_1d_pct']:+.2f}%`)\n"
                        f"• *Geçmişte Ne Yapmış?:* Son 5 yılda `{bt['trades']}` kez oldu → `%{bt['win_rate_pct']}` kazandırdı (ort. `+{bt['avg_return_pct']:.1f}%`)\n"
                        f"• *Zarar Kes (Altına düşerse sat):* `{sym_c}{rp['stop_loss']:,.2f}` (`{rp['stop_pct']:+.1f}%`)\n"
                        f"• *İlk Hedef Fiyat:* `{sym_c}{rp['target_1']:,.2f}` (`+{rp['target_1_pct']:.1f}%`)\n"
                        f"• *Ne Kadar Koymalı?:* Paranın en fazla `%{rp['max_pos_pct']}` kadarı"
                    )
        except Exception as e:
            print(f"[Telegram Alpha Hunter Err]: {e}")

        # 5. Pazar Akşamı 20:00 "1 Dakikalık Haftalık Özet" (Haftada 1 kez kalıcı korumalı)
        if now_dt.weekday() == 6 and now_dt.hour == 20:
            cur_week_key = now_dt.strftime("%Y-W%W")
            try:
                if db.get_setting("last_weekly_summary_week") != cur_week_key:
                    if send_message(build_weekly_summary_text()):
                        db.set_setting("last_weekly_summary_week", cur_week_key, sync_cloud=True)
            except Exception as e:
                print(f"[Weekly Summary Err]: {e}")

        # 6. Pazar Gecesi 21:00+ Otomatik Tam JSON Yedek Gönderimi (Haftada 1 kez kalıcı korumalı)
        if now_dt.weekday() == 6 and now_dt.hour >= 21:
            cur_week_key = now_dt.strftime("%Y-W%W")
            try:
                if db.get_setting("last_weekly_backup_week") != cur_week_key:
                    if send_json_backup(auto=True):
                        db.set_setting("last_weekly_backup_week", cur_week_key, sync_cloud=True)
            except Exception as e:
                print(f"[Weekly JSON Backup Err]: {e}")

        # 4. Günlük Akşam Özeti (18:30)
        if now_dt.hour == 18 and 30 <= now_dt.minute <= 40:
            if last_daily_notif != cur_date_str:
                total_c = db.get_total_cash()
                funds = db.get_all_funds()
                f_val = sum(round(f.get("shares", 0.0) * get_live_fund_price(k, f.get("buy_price", 0.0)), 2) for k, f in funds.items())
                p_val = db.get_pending_total_try()
                p_txt = f" | ⏳ Valörde: ₺{p_val:,.2f}" if p_val > 0 else ""
                send_message(
                    f"🌙 *GÜN SONU PİYASA & PORTFÖY ÖZETİ*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 Toplam Portföy: ₺{total_c + f_val + p_val:,.2f}\n"
                    f"💵 Nakit: ₺{total_c:,.2f} | 📈 Fonlar: ₺{f_val:,.2f}{p_txt}\n"
                    f"ZBJ Para Piyasası fonun bu gece risksiz nema üretmeye devam edecek. İyi akşamlar!"
                )
                last_daily_notif = cur_date_str

        # TELEGRAM GÜNCELLEMELERİNİ ÇEK (LONG POLLING - Optimize 15sn)
        offset = cfg.get("last_update_id", 0) + 1
        updates = tg_api_call("getUpdates", {"offset": offset, "timeout": 15}, token=token, http_timeout=20.0)
        
        if updates and isinstance(updates, list):
            max_id = offset - 1
            auth_chats = set(cfg.get("authorized_chat_ids", []))

            for u in updates:
                up_id = _handle_single_update(u, auth_chats)
                if up_id is not None and up_id > max_id:
                    max_id = up_id

            cfg["last_update_id"] = max_id
            save_config(cfg)
            
        time.sleep(1)

def start_bot_worker():
    global _WORKER_THREAD, _RUNNING
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _RUNNING = True
    _WORKER_THREAD = threading.Thread(target=bot_polling_loop, daemon=True)
    _WORKER_THREAD.start()

def stop_bot_worker():
    global _RUNNING
    _RUNNING = False

def get_bot_status() -> Dict[str, Any]:
    global _BOT_INFO_CACHE, _BOT_INFO_TIME
    cfg = load_config()
    token = cfg.get("bot_token", "").strip()
    is_active = bool(_WORKER_THREAD and _WORKER_THREAD.is_alive())
    bot_info = None
    if token:
        now = time.time()
        if _BOT_INFO_CACHE and (now - _BOT_INFO_TIME < 600):
            bot_info = _BOT_INFO_CACHE
        else:
            bot_info = tg_api_call("getMe", token=token, http_timeout=4.0)
            if bot_info:
                _BOT_INFO_CACHE = bot_info
                _BOT_INFO_TIME = now
        
    return {
        "is_running": is_active,
        "token_configured": bool(token),
        "bot_info": bot_info,
        "authorized_chats": cfg.get("authorized_chat_ids", []),
        "friday_alerts": True,
        "daily_summary": True
    }
