import os
import json
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional, Tuple, List

try:
    import db
    import news_service
    import market_service
    import quant_engine
except ImportError:
    from finans_dashboard import db, news_service, market_service, quant_engine

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DATA_DIR, "telegram_config.json")


# ======================================================================
# AI SAĞLAYICI KONFİGÜRASYONU (ENV + SQLITE/TURSO + TELEGRAM_CONFIG)
# ======================================================================
def _read_telegram_file_cfg() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


def get_ai_config() -> Dict[str, str]:
    db_cfg: Dict[str, Any] = {}
    try:
        raw = db.get_setting("ai_config")
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                db_cfg = parsed
    except Exception:
        pass

    file_cfg = _read_telegram_file_cfg()

    def _pick(env_key: str, cfg_key: str, default: str = "") -> str:
        return (
            os.environ.get(env_key, "").strip()
            or str(db_cfg.get(cfg_key, "") or "").strip()
            or str(file_cfg.get(cfg_key, "") or "").strip()
            or default
        )

    return {
        "gemini_api_key": _pick("GEMINI_API_KEY", "gemini_api_key"),
        "provider_pref": _pick("AI_PROVIDER_PREF", "provider_pref", "auto"),
    }


def save_ai_config(updates: Dict[str, Any]) -> Dict[str, str]:
    current = get_ai_config()
    allowed = ("gemini_api_key", "provider_pref")
    for k in allowed:
        if k in updates and updates[k] is not None:
            val = str(updates[k]).strip()
            if val == "__KEEP__":
                continue
            current[k] = val

    try:
        db.set_setting("ai_config", json.dumps(current, ensure_ascii=False), sync_cloud=True)
    except Exception:
        pass

    try:
        fcfg = _read_telegram_file_cfg()
        for k in ("gemini_api_key", "provider_pref"):
            fcfg[k] = current.get(k, "")
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(fcfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    return current


def _mask_key(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 10:
        return "***"
    return f"{val[:5]}...{val[-4:]}"


def get_local_ollama_models(base_url: str = "http://127.0.0.1:11434") -> List[str]:
    try:
        url = base_url.rstrip("/") + "/api/tags"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def get_ai_config_public() -> Dict[str, Any]:
    cfg = get_ai_config()
    is_render = bool(os.environ.get("RENDER"))
    local_models = [] if is_render else get_local_ollama_models()

    active_cloud = []
    if cfg["gemini_api_key"]:
        active_cloud.append("Gemini 3 Flash (Thinking)")

    return {
        "is_render": is_render,
        "provider_pref": cfg.get("provider_pref") or "auto",
        "gemini_configured": bool(cfg["gemini_api_key"]),
        "gemini_masked": _mask_key(cfg["gemini_api_key"]),
        "local_ollama_available": len(local_models) > 0,
        "local_models": local_models,
        "active_cloud_providers": active_cloud,
        "cloud_ready": len(active_cloud) > 0,
    }


# ======================================================================
# CANLI PORTFÖY VE PİYASA BAĞLAMI (VALÖR DAHİL)
# ======================================================================
def get_portfolio_context() -> Dict[str, Any]:
    accounts = db.get_all_accounts()
    funds = db.get_all_funds()
    total_cash = db.get_total_cash()
    pending_orders = db.get_pending_orders("BEKLIYOR")
    pending_total_try = db.get_pending_total_try()

    market = market_service.fetch_live_market()
    live_prices = market_service.get_live_fund_snapshot()
    macro = market.get("macro", {})
    ppf_rate = market_service.get_live_ppf_daily_rate()

    total_funds_val = 0.0
    fund_details = {}
    for code, f in funds.items():
        sh = float(f.get("shares", 0.0) or 0.0)
        bp = float(f.get("buy_price", 0.0) or 0.0)
        lp = live_prices.get(code, {})
        p = float(lp.get("price", bp or 1.0))
        val = round(sh * p, 2)
        cost = round(sh * bp, 2)
        pnl = round(val - cost, 2) if sh > 0 else 0.0
        total_funds_val += val
        fund_details[code] = {
            "shares": sh,
            "buy_price": bp,
            "price": p,
            "value": val,
            "profit_try": pnl,
            "chg": lp.get("chg", 0.0),
            "weekly_chg": lp.get("weekly_chg", 0.0),
            "monthly_chg": lp.get("monthly_chg", 0.0),
            "drawdown_from_high_pct": lp.get("drawdown_from_high_pct", 0.0),
            "dist_from_avg_pct": lp.get("dist_from_avg_pct", 0.0),
            "category_rank": lp.get("category_rank"),
            "category_total": lp.get("category_total"),
            "name": f.get("name", code),
        }

    pending_tca_val = sum(float(po.get("amount_try", 0.0)) for po in pending_orders if po.get("code") == "TCA")
    pending_aft_val = sum(float(po.get("amount_try", 0.0)) for po in pending_orders if po.get("code") == "AFT")
    pending_ppf_val = sum(float(po.get("amount_try", 0.0)) for po in pending_orders if po.get("code") in ("ZBJ", "TZL"))

    total_wealth = round(total_cash + total_funds_val + pending_total_try, 2)
    b5001 = accounts.get("5001", {}).get("balance", 0.0)
    b5004 = accounts.get("5004", {}).get("balance", 0.0)

    zbj_val = fund_details.get("ZBJ", {}).get("value", 0.0)
    tzl_val = fund_details.get("TZL", {}).get("value", 0.0)
    tca_val = fund_details.get("TCA", {}).get("value", 0.0)
    aft_val = fund_details.get("AFT", {}).get("value", 0.0)
    daily_nema = round((zbj_val + tzl_val) * ppf_rate, 2)
    monthly_nema = round(daily_nema * 30.5, 2)

    weights = {
        "cash_pct": round((total_cash / total_wealth * 100.0) if total_wealth > 0 else 0.0, 1),
        "ppf_pct": round((((zbj_val + tzl_val + pending_ppf_val) / total_wealth) * 100.0) if total_wealth > 0 else 0.0, 1),
        "tca_pct": round((((tca_val + pending_tca_val) / total_wealth) * 100.0) if total_wealth > 0 else 0.0, 1),
        "aft_pct": round((((aft_val + pending_aft_val) / total_wealth) * 100.0) if total_wealth > 0 else 0.0, 1),
    }

    burn_stats = db.get_monthly_burn_stats()
    weekly_stats = db.get_weekly_summary_stats()
    recent_txs = db.get_all_transactions()[:6]

    news_items = news_service.fetch_live_news()
    headlines = [n.get("title", "") for n in news_items[:6]] if news_items else []

    return {
        "user": "Yatırımcı",
        "accounts": accounts,
        "b5001": b5001,
        "b5004": b5004,
        "total_cash": total_cash,
        "funds": fund_details,
        "pending_orders": pending_orders,
        "pending_total_try": pending_total_try,
        "total_funds_val": round(total_funds_val, 2),
        "total_wealth": total_wealth,
        "weights": weights,
        "ppf_daily_rate": ppf_rate,
        "daily_nema": daily_nema,
        "monthly_nema": monthly_nema,
        "burn_stats": burn_stats,
        "weekly_stats": weekly_stats,
        "recent_txs": recent_txs,
        "macro": macro,
        "live_prices": live_prices,
        "headlines": headlines,
        "updated_at": market.get("updated_at") or market_service.now_tr().strftime("%H:%M:%S"),
    }


def build_ai_system_prompt(ctx: Dict[str, Any], role_override: Optional[str] = None) -> str:
    headlines_txt = "\n".join(f"• {h}" for h in ctx["headlines"]) if ctx["headlines"] else "Piyasalar sakin."
    m = ctx.get("macro", {})
    lp = ctx["live_prices"]
    w = ctx.get("weights", {})
    ws = ctx.get("weekly_stats", {})
    bs = ctx.get("burn_stats", {})
    pending = ctx.get("pending_orders", [])

    def _fund_line(code: str, label: str) -> str:
        item = lp.get(code, {})
        rk = f" • TEFAS Sıra: {item['category_rank']}/{item['category_total']}" if item.get("category_rank") and item.get("category_total") else ""
        return (
            f"- {code} ({label}): ₺{item.get('price', 0):.4f} | "
            f"1G %{item.get('chg', 0):+.2f} | 7G %{item.get('weekly_chg', 0):+.2f} | 30G %{item.get('monthly_chg', 0):+.2f} | "
            f"30G Zirveye Göre %{item.get('drawdown_from_high_pct', 0):+.2f}{rk}"
        )

    pending_block = "Yok."
    if pending:
        lines = []
        for po in pending:
            lines.append(
                f"  • {po.get('code')} Alım Emri: ₺{float(po.get('amount_try', 0)):,.2f} "
                f"(~{float(po.get('estimated_shares', 0)):,.0f} pay tahmini, {po.get('expected_settlement', 'Valörde')}). "
                f"NOT: Bu emir henüz işleme girmediği (valörde olduğu) için piyasa fiyat düşüşünden gerçekleşmiş kâr/zarar yazmaz."
            )
        pending_block = "\n" + "\n".join(lines)

    tx_lines = []
    for t in ctx.get("recent_txs", [])[:5]:
        tx_lines.append(f"  • [{str(t.get('date', ''))[:16]}] {t.get('type')} {t.get('code')}: ₺{float(t.get('amount_try', 0)):,.2f} ({t.get('description', '')})")
    tx_block = "\n".join(tx_lines) if tx_lines else "  • Kayıt yok"

    intro = role_override or (
        "Sen kullanıcının kişisel, üst düzey yapay zeka finans ve portföy danışmanısın.\n"
        "Kullanıcı finansal terimlere boğulmadan sade, dürüst, derinlemesine düşünülmüş ama gerçek verilere dayalı kararlar almak isteyen genç bir yatırımcıdır."
    )

    return f"""{intro}

Şu Anki Tarih/Saat (TR): {market_service.now_tr().strftime('%d.%m.%Y %H:%M')}

GERÇEK PORTFÖY DURUMU (SQLite ACID + TEFAS Canlı Veri):
- 5001 Ziraat Ana / KYK: ₺{ctx['b5001']:,.2f} | 5004 Yatırım Vadesiz: ₺{ctx['b5004']:,.2f}
- Sabit Gelir Profili: Kira ~₺11.000/ay + Aylık KYK Kredisi ₺4.000/ay (ilk ay 2 aylık toplu ₺8.000 KYK).
- Boştaki Vadesiz Nakit: ₺{ctx['total_cash']:,.2f} (%{w.get('cash_pct', 0):.1f})
- İşleme Girmiş Aktif Fonlar (Toplam ₺{ctx['total_funds_val']:,.2f}):
  • ZBJ (2958 Başak PPF): {ctx['funds'].get('ZBJ', {}).get('shares', 0):g} pay (~₺{ctx['funds'].get('ZBJ', {}).get('value', 0):,.2f} | K/Z: ₺{ctx['funds'].get('ZBJ', {}).get('profit_try', 0):+.2f})
  • TZL (Para Piyasası): {ctx['funds'].get('TZL', {}).get('shares', 0):g} pay (~₺{ctx['funds'].get('TZL', {}).get('value', 0):,.2f} | K/Z: ₺{ctx['funds'].get('TZL', {}).get('profit_try', 0):+.2f})
  • TCA (Altın Katılım): {ctx['funds'].get('TCA', {}).get('shares', 0):g} pay (~₺{ctx['funds'].get('TCA', {}).get('value', 0):,.2f} | K/Z: ₺{ctx['funds'].get('TCA', {}).get('profit_try', 0):+.2f})
  • AFT (Teknoloji): {ctx['funds'].get('AFT', {}).get('shares', 0):g} pay (~₺{ctx['funds'].get('AFT', {}).get('value', 0):,.2f} | K/Z: ₺{ctx['funds'].get('AFT', {}).get('profit_try', 0):+.2f})
- Valörde / İşleme Girmeyi Bekleyen Emirler (Toplam ₺{ctx.get('pending_total_try', 0):,.2f}):{pending_block}
- Toplam Net Varlık (Nakit + Aktif Fonlar + Valörde Bekleyen): ₺{ctx['total_wealth']:,.2f} (~${ctx['total_wealth']/max(1.0, float(m.get('usd_try', 50))):,.1f} • ~{ctx['total_wealth']/max(1.0, float(m.get('gram_altin_try', 6000))):.2f} gr Altın)
- Aktif PPF Günlük Nema Hızı (%{ctx.get('ppf_daily_rate', 0.0011)*100:.3f}/gün): ~₺{ctx['daily_nema']:.2f}/gün (~₺{ctx['monthly_nema']:.2f}/ay)
- Son 7 Gün Harcama: ₺{ws.get('spent_7d', 0):,.2f} | 90 Günlük Aylık Ortalama Gider: ₺{bs.get('monthly_burn_try', 0):,.2f}

Son İşlemler:
{tx_block}

Küresel ve Yerel Canlı Makro Piyasa:
- Dolar/TL: ₺{m.get('usd_try', 0):.2f} (1G %{m.get('usd_chg_pct', 0):+.2f} | 7G %{m.get('usd_weekly_pct', 0):+.2f}) • Euro/TL: ₺{m.get('eur_try', 0):.2f}
- Ons Altın: ${m.get('ons_altin_usd', 0):,.1f} (%{m.get('ons_chg_pct', 0):+.2f}) • Gram Altın: ₺{m.get('gram_altin_try', 0):,.1f}
- BIST 100: {m.get('bist100', 0):,.0f} (1G %{m.get('bist100_chg_pct', 0):+.2f} | 7G %{m.get('bist100_weekly_pct', 0):+.2f})
- Nasdaq 100 (AFT Yönü): {m.get('nasdaq100', 0):,.0f} (1G %{m.get('nasdaq_chg_pct', 0):+.2f} | 7G %{m.get('nasdaq_weekly_pct', 0):+.2f})

TEFAS Canlı Fon Trendleri (35 Günlük Seri):
{_fund_line('ZBJ', 'Günlük Faiz / Başak')}
{_fund_line('TZL', 'Günlük Faiz / Likit')}
{_fund_line('TCA', 'Altın')}
{_fund_line('AFT', 'Teknoloji')}

Erken Uyarı & Kantitatif Rejim Motoru (200G / VIX / RSI / 30G Projeksiyon):
- {quant_engine.get_compact_quant_summary()}

Son Dakika Ekonomi Haberleri:
{headlines_txt}

YANIT KURALLARI (ÇOK ÖNEMLİ):
1. Yanıt vermeden önce tüm sayıları, valör kurallarını (TEFAS 13:30 kesimi, T+1/T+2 valör, valördeki emirlerin henüz fiyat riski taşımadığı) ve risk/getiri dengesini adım adım analiz et.
2. Kullanıcının TAM OLARAK ne sorduğuna odaklan! Hazır şablon cümleler kurma; sorunun sebebini, arkasındaki piyasa/fon mantığını ve net önerini somut TL/pay rakamlarıyla açıkla.
3. Ağır finans jargonu kullanma; gerekirse yanında sade Türkçe karşılığını yaz ("valör = paranın fona işleme girme süresi" gibi).
4. Asla veride olmayan sahte fiyat veya tutar uydurma. Bilmediğin veya veride olmayan bir bilgi varsa açıkça belirt.
5. Dil: Akıcı, samimi, zeki ve net Türkçe.
"""


# ======================================================================
# ORTAK DÜŞÜNME PARSER'I (<think> + NATIVE REASONING)
# ======================================================================
def _split_thinking_text(raw: str) -> Tuple[str, str]:
    txt = (raw or "").strip()
    if not txt:
        return "", ""
    if "<think>" in txt and "</think>" in txt:
        parts = txt.split("</think>", 1)
        thoughts = parts[0].replace("<think>", "").strip()
        ans = parts[1].strip()
        return thoughts, ans
    if "<think>" in txt and "</think>" not in txt:
        # Model düşünürken token dolduysa bile kullanıcıya anlamlı içerik bırak
        cleaned = txt.replace("<think>", "").strip()
        return cleaned, cleaned
    if "</think>" in txt:
        parts = txt.split("</think>", 1)
        return parts[0].strip(), parts[1].strip()
    return "", txt


# ======================================================================
# DIŞ BULUT VE YEREL LLM ÇAĞRICILARI (EXTENDED REASONING)
# ======================================================================
def call_gemini_api(
    messages: List[Dict[str, str]],
    system_prompt: str,
    api_key: str,
    preferred_model: str = "gemini-3-flash-preview",
    thinking_budget: int = 2048,
) -> Optional[Dict[str, Any]]:
    models_to_try = [preferred_model]
    is_pro_req = "pro" in str(preferred_model).lower()
    fallback_chain = (
        ("gemini-3.1-pro-preview", "gemini-pro-latest", "gemini-3-flash-preview", "gemini-flash-lite-latest", "gemini-3.1-flash-lite-preview")
        if is_pro_req
        else ("gemini-3-flash-preview", "gemini-flash-lite-latest", "gemini-3.1-flash-lite-preview", "gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash")
    )
    for fallback_m in fallback_chain:
        if fallback_m not in models_to_try:
            models_to_try.append(fallback_m)

    contents = []
    for m in messages:
        role = "model" if m.get("role") == "assistant" else "user"
        txt = str(m.get("content", "")).strip()
        if txt:
            contents.append({"role": role, "parts": [{"text": txt}]})
    if not contents:
        return None

    for model in models_to_try:
        t0 = time.time()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        gen_cfg: Dict[str, Any] = {
            "temperature": 0.35,
            "maxOutputTokens": 4096,
        }
        if thinking_budget > 0 and "gemma" not in model.lower():
            gen_cfg["thinkingConfig"] = {
                "thinkingBudget": int(thinking_budget),
                "includeThoughts": True,
            }

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": gen_cfg,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=22.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                candidates = res.get("candidates", [])
                if not candidates:
                    continue
                parts = candidates[0].get("content", {}).get("parts", [])
                thought_chunks = []
                answer_chunks = []
                for p in parts:
                    ptext = str(p.get("text", "")).strip()
                    if not ptext:
                        continue
                    if p.get("thought"):
                        thought_chunks.append(ptext)
                    else:
                        answer_chunks.append(ptext)
                answer = "\n".join(answer_chunks).strip()
                thoughts = "\n".join(thought_chunks).strip()
                if answer:
                    return {
                        "answer": answer,
                        "thoughts": thoughts,
                        "model_used": model,
                        "provider": "gemini-cloud",
                        "elapsed_s": round(time.time() - t0, 2),
                    }
        except Exception:
            continue
    return None


def call_ollama_chat(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    system_prompt: str,
    api_key: str = "",
    timeout: float = 120.0,
    num_predict: int = 1200,
    num_ctx: int = 4096,
    keep_alive: str = "20m",
) -> Optional[Dict[str, Any]]:
    t0 = time.time()
    url = base_url.rstrip("/") + "/api/chat"
    full_msgs = [{"role": "system", "content": system_prompt}] + messages
    payload = {
        "model": model,
        "messages": full_msgs,
        "stream": False,
        "keep_alive": keep_alive,
        "options": {
            "temperature": 0.35,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
        },
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            msg = res.get("message") or {}
            native_think = str(msg.get("thinking") or "").strip()
            raw_content = str(msg.get("content") or res.get("response") or "").strip()
            tag_think, clean_ans = _split_thinking_text(raw_content)
            thoughts = native_think or tag_think
            answer = clean_ans or raw_content
            if not answer and thoughts:
                answer = f"_(Düşünme sınırı dolduğu için doğrudan analiz özeti aktarılıyor)_\n\n{thoughts}"
            if answer:
                return {
                    "answer": answer,
                    "thoughts": thoughts,
                    "model_used": model,
                    "provider": "ollama-local",
                    "elapsed_s": round(time.time() - t0, 2),
                }
    except Exception:
        pass
    return None


def to_tr_ascii(text: str) -> str:
    s = text.replace("İ", "i").replace("I", "ı").lower()
    mapping = {"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"}
    for tr, asc in mapping.items():
        s = s.replace(tr, asc)
    return s


# ======================================================================
# GÖMÜLÜ OTONOM FİNANSAL MOTOR (HİÇBİR LLM ERİŞİLEMEZSE SON YEDEK)
# ======================================================================
def generate_builtin_ai_verdict(prompt: str, ctx: Dict[str, Any], custom_amount: Optional[float] = None) -> str:
    p_ascii = to_tr_ascii(prompt)
    pending = ctx.get("pending_orders", [])
    pending_total = ctx.get("pending_total_try", 0.0)

    # 0. Valör / İşleme Girmedi / Zarar Görünüyor Sorusu
    if any(k in p_ascii for k in ["islem", "valor", "zarar", "gece", "bekliyor", "girmed"]):
        p_note = (
            f"Şu an valörde bekleyen **₺{pending_total:,.2f}** emrin görünüyor; işleme girene kadar gerçek kâr/zararın **₺0.00** kabul edilir."
            if pending_total > 0
            else "Aktif emirlerin hepsi işleme girmiş durumda."
        )
        return (
            f"⏳ *VALÖR & İŞLEM DURUMU AÇIKLAMASI*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{p_note}\n\n"
            f"• TEFAS fonlarında saat *13:30 sonrası* verilen alımlar aynı günün fiyatından değil, ertesi iş günü oluşan fiyattan işleme girer (T+1/T+2 valör).\n"
            f"• Bu sürede gece açıklanan dünkü fon fiyatındaki dalgalanmalar henüz senin emrinin maliyeti değildir.\n"
            f"• Fon hesabına geçtiğinde paneldeki *'İşleme Girdi'* butonuna basarak gerçek alış fiyatını kilitleyebilirsin."
        )

    # 1. Bütçe Dağılımı ve Yatırım Tavsiyesi
    recom_intent = any(k in p_ascii for k in [
        "napay", "ne yap", "ne al", "oner", "tavsiye", "nereye", "nasil yap",
        "dagilim", "bolustur", "kac pay", "alalim", "yatir"
    ])
    if recom_intent or custom_amount:
        target_amt = custom_amount or ctx["total_cash"]
        if target_amt < 10.0:
            target_amt = 1000.0

        tca_meta = ctx["live_prices"]["TCA"]
        aft_meta = ctx["live_prices"]["AFT"]
        zbj_meta = ctx["live_prices"]["ZBJ"]
        tca_chg = tca_meta["chg"]
        aft_chg = aft_meta["chg"]
        tca_dd = tca_meta.get("drawdown_from_high_pct", 0.0)
        aft_dd = aft_meta.get("drawdown_from_high_pct", 0.0)
        tca_w = tca_meta.get("weekly_chg", 0.0)
        aft_w = aft_meta.get("weekly_chg", 0.0)
        tca_p = tca_meta["price"]
        aft_p = aft_meta["price"]
        zbj_p = zbj_meta["price"]
        ppf_rate = ctx.get("ppf_daily_rate", 0.0011)
        weights = ctx.get("weights", {})
        ndx_chg = ctx.get("macro", {}).get("nasdaq_chg_pct", 0.0)

        is_gold_dip = tca_chg < -0.15 or tca_dd <= -1.5
        is_tech_dip = aft_chg <= -0.5 or aft_dd <= -2.5 or ndx_chg <= -0.8
        tca_heavy = weights.get("tca_pct", 0.0) >= 55.0

        if is_gold_dip and not is_tech_dip:
            tca_ratio = 0.25 if tca_heavy else 0.35
            aft_ratio = 0.0
            market_diag = (
                f"Altın (TCA) bugün %{tca_chg:+.2f}, 30g zirveden %{tca_dd:.2f} geride. "
                f"Teknoloji (AFT) 7 günde %{aft_w:+.2f} seyrinde."
            )
        elif is_tech_dip and not is_gold_dip:
            tca_ratio = 0.15
            aft_ratio = 0.35
            market_diag = (
                f"Teknoloji (AFT) bugün %{aft_chg:+.2f} (30g zirveden %{aft_dd:.2f} aşağıda, Nasdaq %{ndx_chg:+.2f}) — kademeli alım bölgesi."
            )
        elif is_gold_dip and is_tech_dip:
            tca_ratio = 0.25 if tca_heavy else 0.30
            aft_ratio = 0.35 if tca_heavy else 0.30
            market_diag = (
                f"İki büyüme varlığında da geri çekilme var: Altın 1G %{tca_chg:+.2f} / Zirveden %{tca_dd:.2f}, "
                f"Teknoloji 1G %{aft_chg:+.2f} / Zirveden %{aft_dd:.2f}."
            )
        else:
            tca_ratio = 0.15 if tca_heavy else 0.20
            aft_ratio = 0.20 if tca_heavy else 0.15
            market_diag = (
                f"Piyasalar dengeli (TCA 7G %{tca_w:+.2f}, AFT 7G %{aft_w:+.2f}). "
                f"Ana parayı günlük ~%{ppf_rate*100:.2f} kazandıran ZBJ'de tutarken ufak dengeleme yapıyoruz."
            )

        total_spent = 0.0
        steps = []
        if tca_ratio > 0:
            tca_shares = int((target_amt * tca_ratio) / tca_p)
            if tca_shares > 0:
                spent_tca = round(tca_shares * tca_p, 2)
                total_spent += spent_tca
                steps.append(f"🥇 *TCA Altın:* *{tca_shares:,} pay* (~₺{spent_tca:,.2f}) | ₺{tca_p:.4f}")

        if aft_ratio > 0:
            aft_shares = int((target_amt * aft_ratio) / aft_p)
            if aft_shares > 0:
                spent_aft = round(aft_shares * aft_p, 2)
                total_spent += spent_aft
                steps.append(f"🚀 *AFT Teknoloji:* *{aft_shares:,} pay* (~₺{spent_aft:,.2f}) | ₺{aft_p:.4f}")

        rem_zbj = target_amt - total_spent
        zbj_shares = int(rem_zbj / zbj_p)
        zbj_spent = round(zbj_shares * zbj_p, 2)
        total_spent += zbj_spent
        steps.append(f"🛡 *ZBJ Başak PPF:* *{zbj_shares:,} pay* (~₺{zbj_spent:,.2f}) | Günlük ~%{zbj_meta.get('chg', 0.10):+.2f}")

        leftover = round(target_amt - total_spent, 2)
        return (
            f"📊 *CANLI PORTFÖY ÖNERİSİ* _(Çevrimdışı Hesaplama — Cloud LLM Key ekleyerek tam yapay zeka sohbeti açabilirsin)_\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 *İncelenen Tutar:* ₺{target_amt:,.2f}\n"
            f"🔎 *Durum:* {market_diag}\n\n" +
            "\n".join(steps) + "\n\n"
            f"💵 *Artan Küsürat:* ₺{leftover:,.2f}"
        )

    return (
        f"🤖 *YAPAY ZEKA MODU (CLOUD ANAHTARI BEKLENİYOR)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Sorunu aldım: _{prompt}_\n\n"
        f"Şu an canlı veriyle toplam varlığın *₺{ctx['total_wealth']:,.2f}* "
        f"(Aktif Fon: ₺{ctx['total_funds_val']:,.2f} | Valörde: ₺{pending_total:,.2f} | Nakit: ₺{ctx['total_cash']:,.2f}).\n\n"
        f"⚡ Gerçek anlamda uzun düşünüp soruna özel sohbet edebilen LLM modunu aktifleştirmek için:\n"
        f"• *Telegram Bot* sekmesindeki **Cloud AI Ayarları** bölümüne ücretsiz bir **Gemini API Key** (`aistudio.google.com`) kaydetmen yeterli."
    )


# ======================================================================
# ANA ÇOK-SAĞLAYICILI AKIL YÜRÜTME (SOHBET + DERİN DÜŞÜNME)
# ======================================================================
def ask_chat_engine(
    user_prompt: str,
    model_pref: str = "auto",
    history: Optional[List[Dict[str, str]]] = None,
    custom_amount: Optional[float] = None,
) -> Dict[str, Any]:
    ctx = get_portfolio_context()
    cfg = get_ai_config()
    system_prompt = build_ai_system_prompt(ctx)

    msgs: List[Dict[str, str]] = []
    if history:
        for h in history[-6:]:
            role = "assistant" if h.get("role") == "assistant" else "user"
            content = str(h.get("content", "")).strip()
            if content:
                msgs.append({"role": role, "content": content[:1500]})
    msgs.append({"role": "user", "content": user_prompt})

    is_render = bool(os.environ.get("RENDER"))
    pref = (model_pref or cfg.get("provider_pref") or "auto").strip()

    def _try_cloud_chain(order: List[str]) -> Optional[Dict[str, Any]]:
        for p in order:
            if p == "gemini-pro" and cfg["gemini_api_key"]:
                res = call_gemini_api(msgs, system_prompt, cfg["gemini_api_key"], preferred_model="gemini-3.1-pro-preview", thinking_budget=4096)
                if res:
                    return res
            elif p == "gemini" and cfg["gemini_api_key"]:
                res = call_gemini_api(msgs, system_prompt, cfg["gemini_api_key"], preferred_model="gemini-3-flash-preview", thinking_budget=3072)
                if res:
                    return res
        return None

    # 1. Açık Cloud seçimi
    if pref.startswith("cloud:"):
        target = pref.split(":", 1)[1]
        base_order = [target, "gemini"]
        dedup = list(dict.fromkeys(base_order))
        res = _try_cloud_chain(dedup)
        if res:
            return res

    # 2. Yerel veya spesifik Ollama modeli seçilmişse
    local_explicit = (not pref.startswith("cloud")) and (pref not in ("auto", "cloud-auto", ""))
    if local_explicit and not is_render:
        local_models = get_local_ollama_models()
        if pref in local_models:
            res = call_ollama_chat("http://127.0.0.1:11434", pref, msgs, system_prompt, timeout=180.0, num_predict=1400, num_ctx=4096)
            if res:
                return res
        # Yerelde seçili model ismi yoksa kurulu ilk mantıklı modele geç
        if local_models:
            for cand in ("qwen3.5-uncensored:4b", "huihui_ai/qwen3-abliterated:8b", "huihui_ai/deepseek-r1-abliterated:8b", "huihui_ai/qwen3-abliterated:14b"):
                if cand in local_models:
                    res = call_ollama_chat("http://127.0.0.1:11434", cand, msgs, system_prompt, timeout=180.0, num_predict=1400, num_ctx=4096)
                    if res:
                        return res

    # 3. Otomatik rota: Cloud Gemini önce -> yerelde Ollama
    res = _try_cloud_chain(["gemini"])
    if res:
        return res

    if not is_render:
        local_models = get_local_ollama_models()
        for cand in (
            "qwen3.5-uncensored:4b",
            "huihui_ai/qwen3-abliterated:8b",
            "huihui_ai/deepseek-r1-abliterated:8b",
            "huihui_ai/qwen3-abliterated:14b",
            "furkan-finans-ai:latest",
        ):
            if cand in local_models:
                res = call_ollama_chat("http://127.0.0.1:11434", cand, msgs, system_prompt, timeout=180.0, num_predict=1400, num_ctx=4096)
                if res:
                    return res

    # 4. Son Çare Yerleşik Hesaplayıcı
    return {
        "answer": generate_builtin_ai_verdict(user_prompt, ctx, custom_amount=custom_amount),
        "thoughts": "Harici Cloud API Key veya aktif Ollama bağlantısı bulunamadığı için yerleşik kural tabanlı finans motoru çalıştı.",
        "model_used": "builtin-fallback",
        "provider": "offline-rules",
        "elapsed_s": 0.05,
    }


def _run_specialist_llm(
    role_title: str,
    focus_instruction: str,
    topic: str,
    ctx: Dict[str, Any],
    preferred_local_model: str,
    extra_user_context: str = "",
) -> Optional[Dict[str, Any]]:
    cfg = get_ai_config()
    is_render = bool(os.environ.get("RENDER"))
    sys_p = build_ai_system_prompt(
        ctx,
        role_override=(
            f"Sen Yatırım Konseyi'ndeki '{role_title}' uzmanısın.\n"
            f"Uzmanlık odağın: {focus_instruction}\n"
            f"Net, somut, sayılara dayalı ve sade Türkçe ile analiz yap; max 5-6 kısa madde veya net paragraf yaz."
        ),
    )
    user_q = f"Konsey Gündem Konusu: {topic}"
    if extra_user_context:
        user_q += f"\n\nDiğer Konsey Üyelerinin Görüşleri:\n{extra_user_context}"
    msgs = [{"role": "user", "content": user_q}]

    if cfg["gemini_api_key"]:
        res = call_gemini_api(msgs, sys_p, cfg["gemini_api_key"], preferred_model="gemini-3-flash-preview", thinking_budget=1024)
        if res:
            return res

    if not is_render:
        local_models = get_local_ollama_models()
        model_to_use = preferred_local_model if preferred_local_model in local_models else (local_models[0] if local_models else "")
        if model_to_use:
            res = call_ollama_chat("http://127.0.0.1:11434", model_to_use, msgs, sys_p, timeout=85.0, num_predict=1100)
            if res:
                return res
    return None


def run_council_llm_debate(topic: str, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """4 uzman LLM ajanı ile gerçek derin analiz ve konsensüs üretir."""
    if ctx is None:
        ctx = get_portfolio_context()

    cfg = get_ai_config()
    has_cloud = bool(cfg["gemini_api_key"])
    local_models = [] if os.environ.get("RENDER") else get_local_ollama_models()

    if not has_cloud and not local_models:
        return {"live_llm": False, "llm_reason": "missing_key", "gemini_configured": False}

    specs = [
        (
            "qwen3",
            "Sayısal Matematik & Getiri Uzmanı",
            "Sayılar, ağırlıklar, TL/pay hesabı, günlük nema, 30 günlük düşüş iskontosu ve matematiksel optimizasyon.",
            "huihui_ai/qwen3-abliterated:8b",
        ),
        (
            "deepseek",
            "Risk & Valör Koruma Sentineli",
            "Nakit tamponu, harcama güvencesi, valörde/işleme girmemiş emirler (TEFAS 13:30 kuralı), tek varlıkta yoğunlaşma riski ve güvenlik uyarısı.",
            "huihui_ai/deepseek-r1-abliterated:8b",
        ),
        (
            "claude_macro",
            "Küresel Makro & Haber Analisti",
            "Dolar/TL, Ons/Gram Altın, Nasdaq 100 (AFT), BIST 100 ve güncel ekonomi haberlerinin fonlara etkisi.",
            "qwen3.5-uncensored:4b",
        ),
    ]

    results: Dict[str, Optional[Dict[str, Any]]] = {}
    if has_cloud:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = {
                key: pool.submit(_run_specialist_llm, title, focus, topic, ctx, lmodel)
                for key, title, focus, lmodel in specs
            }
            for key, fut in futs.items():
                try:
                    results[key] = fut.result(timeout=28.0)
                except Exception:
                    results[key] = None
    else:
        for key, title, focus, lmodel in specs:
            results[key] = _run_specialist_llm(title, focus, topic, ctx, lmodel)

    if not any(results.values()):
        return {"live_llm": False, "llm_reason": "api_failed", "gemini_configured": has_cloud}

    summary_context = []
    for key, title, _, _ in specs:
        r = results.get(key)
        if r and r.get("answer"):
            summary_context.append(f"- {title}: {r['answer'][:700]}")

    chair_res = _run_specialist_llm(
        "Baş Stratejist & Ortak Karar Sentezleyici",
        "Yukarıdaki 3 uzmanın görüşünü sentezle. Kullanıcıya doğrudan, sade Türkçe ile adım adım ne yapması gerektiğini (TL/pay veya bekleme/valör kararı) net şekilde söyle.",
        topic,
        ctx,
        "huihui_ai/qwen3-abliterated:8b",
        extra_user_context="\n".join(summary_context),
    )
    if not chair_res and summary_context:
        chair_res = {
            "answer": "Uzman görüşlerinin özeti:\n\n" + "\n\n".join(summary_context),
            "thoughts": "",
            "model_used": "specialist-merge",
            "provider": "gemini-cloud" if has_cloud else "ollama-local",
            "elapsed_s": 0.1,
        }

    return {
        "live_llm": True,
        "llm_reason": "ok",
        "gemini_configured": has_cloud,
        "qwen3": results.get("qwen3"),
        "deepseek": results.get("deepseek"),
        "claude_macro": results.get("claude_macro"),
        "chair": chair_res,
    }


# ======================================================================
# TELEGRAM & TEK-ATIM DANIŞMAN GİRİŞİ
# ======================================================================
def ask_ai_advisor(user_prompt: str, custom_amount: Optional[float] = None) -> str:
    res = ask_chat_engine(user_prompt=user_prompt, model_pref="auto", custom_amount=custom_amount)
    ans = res.get("answer", "").strip()
    prov = res.get("provider", "")
    model = res.get("model_used", "")
    elapsed = res.get("elapsed_s", 0.0)

    if prov == "offline-rules":
        return ans

    badge_map = {
        "gemini-cloud": f"🧠 *GEMINI BULUT AI* (`{model}` • {elapsed}s)",
        "ollama-local": f"💻 *YEREL OLLAMA* (`{model}` • {elapsed}s)",
    }
    header = badge_map.get(prov, f"🤖 *YAPAY ZEKA* (`{model}` • {elapsed}s)")
    return f"{header}\n━━━━━━━━━━━━━━━━━━━━\n{ans}"


def save_ai_key_from_telegram(key_input: str) -> str:
    clean_key = key_input.strip()
    if clean_key.lower() in ["sil", "kaldir", "kaldır", "reset", "none"]:
        save_ai_config({"gemini_api_key": ""})
        return "🧹 *Gemini API anahtarı temizlendi.*"

    if clean_key.startswith("AIzaSy") or clean_key.startswith("AQ."):
        provider = "Google Gemini 3 Flash (Derin Düşünme / Thinking)"
    else:
        provider = "Google Gemini Cloud"
    save_ai_config({"gemini_api_key": clean_key})

    return (
        f"🚀 *GEMINI BULUT YAPAY ZEKA AKTİF EDİLDİ!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *Sağlayıcı:* {provider}\n"
        f"🔑 *Kayıt:* `{_mask_key(clean_key)}`\n"
        f"Hem web paneli, hem 4 LLM Konsey hem de Telegram botun artık derin düşünme (thinking) destekli bu motoru kullanıyor!"
    )


def get_ai_status_text() -> str:
    pub = get_ai_config_public()
    active_lines = []
    if pub["gemini_configured"]:
        active_lines.append(f"• Google Gemini 3 Thinking (`{pub['gemini_masked']}`)")
    if pub["local_ollama_available"]:
        active_lines.append(f"• Yerel Ollama ({len(pub['local_models'])} model hazır)")

    status_blk = "\n".join(active_lines) if active_lines else "• Aktif Gemini API Key yok (Yalnızca çevrimdışı kural motoru)"
    return (
        f"🤖 *YAPAY ZEKA ASİSTAN DURUMU*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_blk}\n\n"
        f"💡 *Ücretsiz Gemini Bağlamak İçin:*\n"
        f"• Google AI Studio (`aistudio.google.com`) anahtarını (`AIzaSy...` / `AQ...`)\n"
        f"`/api_key ANAHTAR` yazarak bota gönderebilir veya Web Panel > Telegram & Cloud AI sekmesinden kaydedebilirsin."
    )
