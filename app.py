import os
import io
import csv
import json
import time
import uuid
import datetime
import urllib.request
import threading
import re
import hmac
import hashlib
import secrets
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

import db
import market_service
from market_service import now_tr, fetch_live_market
from monte_carlo import run_monte_carlo
import news_service
import telegram_bot
import ai_advisor
import settlement_engine
import tax_engine
import auto_allocator
import league_service
import quant_engine
import alpha_hunter
import tefas_analyzer

def _start_cloud_keepalive():
    if not os.environ.get("RENDER"):
        return
    ext_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if not ext_url:
        return
    ext_url = ext_url.rstrip("/") + "/api/health"

    def _ping_loop():
        while True:
            time.sleep(660)  # 11 dk (Render 15 dk uyku penceresinin altı)
            try:
                req = urllib.request.Request(ext_url, headers={"User-Agent": "FinansTerminal-KeepAlive/1.0"})
                with urllib.request.urlopen(req, timeout=8.0):
                    pass
            except Exception:
                pass

    threading.Thread(target=_ping_loop, daemon=True).start()


def _prewarm_quant_history():
    try:
        time.sleep(2.0)
        quant_engine.get_deep_market_history(force=False)
        alpha_hunter.scan_alpha_universe(force_refresh=False)
    except Exception:
        pass


def _start_valor_watcher():
    def _loop():
        time.sleep(15.0)
        while True:
            try:
                pending = db.get_pending_orders("BEKLIYOR")
                if pending:
                    # Render aktifse yerel Telegram bildirimi çift mesaj atmasın; yalnızca auto-settle çalışsın
                    allow_tg = bool(os.environ.get("RENDER") or os.environ.get("FORCE_LOCAL_TELEGRAM_POLL"))
                    if not allow_tg:
                        try:
                            allow_tg = not telegram_bot.is_cloud_instance_active()
                        except Exception:
                            allow_tg = False
                    notify_cb = (lambda txt, kb: telegram_bot.send_message(txt, reply_markup=kb)) if allow_tg else None
                    settlement_engine.run_valor_automation_tick(
                        notify_fn=notify_cb,
                        refresh_tefas=False,
                        source="web_bg",
                    )
            except Exception as e:
                print(f"[Valor BG Watcher Err]: {e}")
            time.sleep(120)

    threading.Thread(target=_loop, daemon=True).start()


_SERVER_BOOT_ID = uuid.uuid4().hex[:10]


@asynccontextmanager
async def lifespan(app: FastAPI):
    market_service.start_market_poller()
    telegram_bot.start_bot_worker()
    _start_valor_watcher()
    _start_cloud_keepalive()
    threading.Thread(target=_prewarm_quant_history, daemon=True).start()
    try:
        yield
    finally:
        db.flush_cloud_sync()

app = FastAPI(
    title="Finans Paneli — Kişisel Varlık Terminali",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(DATA_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
SECURITY_CONFIG_FILE = os.path.join(DATA_DIR, "security_config.json")


def _get_asset_version() -> str:
    """Compute cache-busting asset version from static files mtime + boot id."""
    latest = 0
    for root_dir, _, files in os.walk(STATIC_DIR):
        for fname in files:
            if fname.endswith((".js", ".css", ".svg")):
                try:
                    mt = int(os.path.getmtime(os.path.join(root_dir, fname)))
                    if mt > latest:
                        latest = mt
                except OSError:
                    pass
    return f"{_SERVER_BOOT_ID}-{latest}" if latest else _SERVER_BOOT_ID

_SEC_LOCK = threading.Lock()
_SEC_CACHE: Optional[dict] = None
_SEC_MTIME: float = 0.0
_SESSION_MAX_AGE_S = 7 * 86400
_MAX_BODY_BYTES = 2 * 1024 * 1024

def get_client_ip(request: Request) -> str:
    cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf_ip:
        return cf_ip
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"

def is_https_request(request: Request) -> bool:
    proto = request.headers.get("X-Forwarded-Proto", "").lower()
    return proto == "https" or request.url.scheme == "https"

def hash_pin_value(pin: str, salt: Optional[str] = None) -> str:
    s = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), s.encode("utf-8"), 200_000).hex()
    return f"pbkdf2_sha256${s}${dk}"

def verify_pin_value(pin: str, cfg: dict) -> bool:
    env_pin = os.environ.get("MASTER_PIN", "").strip()
    if env_pin:
        return hmac.compare_digest(pin, env_pin)
    pin_hash = str(cfg.get("pin_hash", "")).strip()
    if pin_hash.startswith("pbkdf2_sha256$"):
        parts = pin_hash.split("$", 2)
        if len(parts) == 3:
            expected = hash_pin_value(pin, salt=parts[1])
            return hmac.compare_digest(expected, pin_hash)
    plain = str(cfg.get("master_pin", "")).strip()
    if plain:
        return hmac.compare_digest(pin, plain)
    return False

def load_security_config() -> dict:
    global _SEC_CACHE, _SEC_MTIME
    with _SEC_LOCK:
        if os.path.exists(SECURITY_CONFIG_FILE):
            try:
                mtime = os.path.getmtime(SECURITY_CONFIG_FILE)
                if _SEC_CACHE is not None and mtime == _SEC_MTIME:
                    cfg = dict(_SEC_CACHE)
                else:
                    with open(SECURITY_CONFIG_FILE, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    _SEC_CACHE = dict(cfg)
                    _SEC_MTIME = mtime
            except Exception:
                cfg = dict(_SEC_CACHE) if _SEC_CACHE else {}
        else:
            init_pin = os.environ.get("MASTER_PIN") or "1234"
            cfg = {
                "pin_hash": hash_pin_value(init_pin),
                "session_secret": os.environ.get("SESSION_SECRET") or secrets.token_hex(32),
                "session_version": 1,
                "auth_enabled": True
            }
            with open(SECURITY_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            _SEC_CACHE = dict(cfg)
            _SEC_MTIME = os.path.getmtime(SECURITY_CONFIG_FILE)

        # Düz metin PIN kalıntısını güvenli PBKDF2 özetine otomatik geçir
        if cfg.get("master_pin") and not cfg.get("pin_hash"):
            cfg["pin_hash"] = hash_pin_value(str(cfg["master_pin"]).strip())
            cfg.pop("master_pin", None)
            try:
                with open(SECURITY_CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
                _SEC_CACHE = dict(cfg)
                _SEC_MTIME = os.path.getmtime(SECURITY_CONFIG_FILE)
            except Exception:
                pass

        if "session_version" not in cfg:
            cfg["session_version"] = 1
        env_secret = os.environ.get("SESSION_SECRET", "").strip()
        if env_secret:
            cfg["session_secret"] = env_secret
        return cfg

def save_security_config(cfg: dict):
    global _SEC_CACHE, _SEC_MTIME
    with _SEC_LOCK:
        clean_cfg = dict(cfg)
        clean_cfg.pop("master_pin", None)
        with open(SECURITY_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_cfg, f, indent=2)
        _SEC_CACHE = dict(clean_cfg)
        _SEC_MTIME = os.path.getmtime(SECURITY_CONFIG_FILE)

def create_session_token(secret: str, version: int = 1) -> str:
    ts = str(int(time.time()))
    payload = f"{ts}.{int(version)}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def verify_session_token(token: str, secret: str, expected_version: int = 1) -> bool:
    if not token or not secret or token.count(".") not in (1, 2):
        return False
    parts = token.split(".")
    try:
        if len(parts) == 3:
            ts_str, ver_str, sig = parts
            if int(ver_str) != int(expected_version):
                return False
            payload = f"{ts_str}.{ver_str}"
        elif len(parts) == 2:
            # Eski v1 oturumların sadece version==1 ise geçerliliği sürsün
            if int(expected_version) != 1:
                return False
            ts_str, sig = parts
            payload = ts_str
        else:
            return False
        ts = int(ts_str)
        if time.time() - ts > _SESSION_MAX_AGE_S or time.time() < ts - 60:
            return False
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False

def is_request_authenticated(request: Request) -> bool:
    cfg = load_security_config()
    if not cfg.get("auth_enabled", True):
        return True
    
    token = request.cookies.get("finans_session")
    secret = cfg.get("session_secret", "")
    ver = int(cfg.get("session_version", 1))
    if token and verify_session_token(token, secret, ver):
        return True
        
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        header_token = auth_header[7:].strip()
        if verify_session_token(header_token, secret, ver):
            return True
            
    return False

def _is_same_origin_mutation(request: Request) -> bool:
    sec_fetch = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if sec_fetch == "cross-site":
        return False
    host = (request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or "").split(",")[0].strip().lower()
    origin = (request.headers.get("Origin") or "").strip().lower()
    if origin:
        origin_host = origin.split("://", 1)[-1].split("/", 1)[0]
        if host and origin_host != host:
            return False
    referer = (request.headers.get("Referer") or "").strip().lower()
    if not origin and referer:
        ref_host = referer.split("://", 1)[-1].split("/", 1)[0]
        if host and ref_host != host:
            return False
    return True

# ======================================================================
# GÜVENLİK DUVARI, RATE LIMITING, CSRF & AUTH MIDDLEWARE
# ======================================================================
class SecurityFirewallMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.request_counts: Dict[str, List[float]] = {}
        self._last_prune: float = time.time()
        self._lock = threading.Lock()
        
    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        req_id = uuid.uuid4().hex[:12]
        client_ip = get_client_ip(request)
        now = time.time()
        path = request.url.path
        method = request.method.upper()

        # 0. Gövde Boyutu Koruması (2 MB DoS Limiti)
        cl = request.headers.get("Content-Length")
        if cl:
            try:
                if int(cl) > _MAX_BODY_BYTES:
                    return JSONResponse(status_code=413, content={"error": "İstek boyutu güvenlik sınırını aşıyor."})
            except ValueError:
                pass
        
        # 1. Rate Limiting (Dakikada max 180 genel / login için ayrıca sıkı kalkan)
        with self._lock:
            if now - self._last_prune > 300.0:
                stale_ips = [ip for ip, ts_list in self.request_counts.items() if not ts_list or (now - ts_list[-1] > 120.0)]
                for ip in stale_ips:
                    del self.request_counts[ip]
                self._last_prune = now

            timestamps = [t for t in self.request_counts.get(client_ip, []) if now - t < 60.0]
            if len(timestamps) >= 180:
                return JSONResponse(status_code=429, content={"error": "Too Many Requests - Güvenlik Duvarı Sınırı Aşıldı"})
            timestamps.append(now)
            self.request_counts[client_ip] = timestamps
        
        # 2. Yetkilendirme Koruması (PIN ile Oturum Kontrolü)
        if path.startswith("/api/") and path not in ["/api/auth/login", "/api/auth/status", "/api/health", "/api/telegram/webhook"]:
            if not is_request_authenticated(request):
                return JSONResponse(
                    status_code=401,
                    content={"error": "Yetkisiz erişim. Lütfen terminale PIN şifresiyle giriş yapınız."}
                )

        # 3. CSRF / Origin Koruması (Durum Değiştiren İsteklerde)
        if method in ("POST", "PUT", "PATCH", "DELETE") and path != "/api/telegram/webhook":
            if not _is_same_origin_mutation(request):
                return JSONResponse(
                    status_code=403,
                    content={"error": "CSRF / Çapraz kaynaklı istek güvenlik duvarı tarafından engellendi."}
                )

        # 4. İsteği işlet
        response = await call_next(request)
        
        # 5. Katı Güvenlik Başlıkları, İzlenebilirlik & Akıllı Önbellek Politikası
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000:.2f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), usb=()"
        if path.startswith("/static/") or path in ("/icon.svg", "/manifest.json"):
            response.headers["Cache-Control"] = "public, max-age=86400"
        else:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "font-src 'self' data: https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        if is_https_request(request):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        return response

app.add_middleware(SecurityFirewallMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=800)

@app.get("/api/health")
def health_check():
    mkt = fetch_live_market()
    return {
        "status": "ok",
        "version": "2.4.0",
        "boot_id": _SERVER_BOOT_ID,
        "time_tr": now_tr().strftime("%Y-%m-%d %H:%M:%S"),
        "market_updated_at": mkt.get("updated_at"),
        "data_quality": mkt.get("data_quality", {}),
    }


@app.get("/api/preferences")
def get_ui_preferences():
    raw = db.get_setting("ui_preferences")
    prefs = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                prefs = parsed
        except Exception:
            prefs = {}
    return {"status": "ok", "preferences": prefs}


class UiPreferencesPayload(BaseModel):
    preferences: Dict[str, Any]


@app.post("/api/preferences")
def save_ui_preferences(req: UiPreferencesPayload):
    clean = dict(req.preferences or {})
    if len(clean) > 200:
        raise HTTPException(status_code=400, detail="Çok fazla tercih anahtarı.")
    db.set_setting("ui_preferences", json.dumps(clean, ensure_ascii=False))
    return {"status": "ok", "count": len(clean), "preferences": clean}


@app.get("/api/market")
def get_market(refresh: bool = False):
    if refresh:
        market_service.refresh_live_market_now()
    return fetch_live_market()


@app.get("/api/league")
def get_tefas_league_api(refresh: bool = False):
    return league_service.get_tefas_league_table(force_refresh=refresh)

@app.get("/api/portfolio")
def get_portfolio(refresh: bool = False):
    if refresh:
        market_service.refresh_live_market_now()
    market = fetch_live_market()
    accounts = db.get_all_accounts()
    funds = db.get_all_funds()
    pending_orders = db.get_pending_orders("BEKLIYOR")
    pending_total = db.get_pending_total_try()
    
    total_cash = db.get_total_cash()
    total_val = total_cash + pending_total
    funds_detail = []
    
    stopaj_rates = tax_engine.get_stopaj_rates()
    for code, item in funds.items():
        shares = item.get("shares", 0.0)
        buy_p = item.get("buy_price", 0.0)
        m_fund = market["funds"].get(code, {})
        curr_p = m_fund.get("price", buy_p)
        cur_val = shares * curr_p
        cost = shares * buy_p
        profit = cur_val - cost
        profit_pct = (profit / cost * 100.0) if cost > 0 else 0.0
        tax_info = tax_engine.calculate_fund_tax_breakdown(code, shares, buy_p, curr_p, stopaj_rates)
        
        total_val += cur_val
        funds_detail.append({
            "code": code,
            "name": item.get("name", code),
            "shares": shares,
            "buy_price": buy_p,
            "current_price": curr_p,
            "official_price": m_fund.get("official_price", curr_p),
            "accrued_price_estimated": bool(m_fund.get("accrued_price_estimated", False)),
            "accrued_gap_days": int(m_fund.get("accrued_gap_days", 0) or 0),
            "tefas_date": m_fund.get("tefas_date", ""),
            "current_value": round(cur_val, 2),
            "profit_try": round(profit, 2),
            "profit_pct": round(profit_pct, 2),
            "stopaj_pct": tax_info["stopaj_pct"],
            "estimated_tax_try": tax_info["estimated_tax_try"],
            "net_exit_value_try": tax_info["net_exit_value_try"],
            "net_profit_try": tax_info["net_profit_try"],
            "net_profit_pct": tax_info["net_profit_pct"],
        })

    # Valördeki bekleyen emirlere canlı TEFAS takvim/fiyat bilgisini ekle
    pending_orders = settlement_engine.enrich_pending_orders(pending_orders, market.get("funds", {}))
    tax_summary = tax_engine.build_portfolio_tax_summary(funds_detail, market)

    try:
        db.record_net_worth_snapshot(total_val, total_cash, max(0.0, total_val - total_cash))
    except Exception:
        pass

    burn_stats = db.get_monthly_burn_stats()
    ppf_rate = market_service.get_live_ppf_daily_rate()
    pers_st = db.get_persistence_status()

    return {
        "total_value_try": round(total_val, 2),
        "cash_try": round(total_cash, 2),
        "pending_total_try": round(pending_total, 2),
        "pending_orders": pending_orders,
        "valor_config": settlement_engine.get_valor_config(),
        "tax_summary": tax_summary,
        "accounts": list(accounts.values()),
        "funds": funds_detail,
        "monthly_burn_try": burn_stats["monthly_burn_try"],
        "has_burn_history": burn_stats["has_burn_history"],
        "ppf_daily_rate": ppf_rate,
        "persistence": {
            "boot_id": _SERVER_BOOT_ID,
            "turso_configured": pers_st.get("turso_configured", False),
            "ephemeral_warning": pers_st.get("ephemeral_warning", False),
        },
        "updated_at": market.get("updated_at")
    }

# ======================================================================
# HESAP BAKIYELERİ YÖNETİM ENDPOINT'LERİ (SQLITE ACID)
# ======================================================================
@app.get("/api/accounts")
def get_accounts():
    return {"accounts": db.get_all_accounts()}

class UpdateAccountRequest(BaseModel):
    id: str
    balance: float
    name: Optional[str] = None

@app.post("/api/accounts/update")
def update_account(req: UpdateAccountRequest):
    acc_id = db.normalize_account_id(req.id, "5001")
    accounts = db.get_all_accounts()
    old_balance = accounts.get(acc_id, {}).get("balance", 0.0)
    new_balance = round(float(req.balance), 2)
    diff = round(new_balance - old_balance, 2)
    
    acc = db.update_account(acc_id, new_balance, req.name)
    
    # Deftere otomatik bakiye senkronizasyon kaydı düş
    if abs(diff) >= 0.01:
        tx_type = "GİRİŞ" if diff > 0 else "ÇIKIŞ"
        acc_name = acc.get("name", acc_id)
        tx = {
            "id": f"tx-adj-{uuid.uuid4().hex[:6]}",
            "date": now_tr().strftime("%Y-%m-%d %H:%M:%S"),
            "type": tx_type,
            "category": "Bakiye Senkronizasyonu",
            "amount_try": abs(diff),
            "shares": 0,
            "price": 0,
            "code": "TRY",
            "account_id": acc_id,
            "to_account_id": None,
            "description": f"{acc_name} bakiye güncelleme (₺{old_balance:.2f} → ₺{new_balance:.2f})"
        }
        db.insert_transaction(tx)
        
    return {"status": "ok", "account": acc}

@app.post("/api/accounts/recalculate-from-ledger")
def recalculate_accounts_from_ledger_api():
    """Defterdeki (transactions) tüm işlemleri kronolojik oynatarak 5001/5004 nakit bakiyelerini defterle eşitler."""
    balances = db.recalculate_accounts_from_ledger(push_cloud=True)
    return {"status": "ok", "balances": balances, "accounts": list(db.get_all_accounts().values())}


@app.delete("/api/accounts/{acc_id}")
def delete_account_route(acc_id: str):
    success = db.delete_account(acc_id.strip())
    return {"status": "ok" if success else "not_found", "account_id": acc_id}

@app.get("/api/transactions")
def get_transactions():
    return {"transactions": db.get_all_transactions()}

class NewTransactionRequest(BaseModel):
    type: str  # ALIM, SATIM, GİRİŞ, ÇIKIŞ, VIRMAN
    category: Optional[str] = "İşlem"
    amount_try: float
    shares: Optional[float] = 0.0
    code: Optional[str] = "TRY"
    account_id: Optional[str] = "5001"
    to_account_id: Optional[str] = "5004"
    description: Optional[str] = ""
    is_pending: Optional[bool] = False

@app.post("/api/transactions")
def add_transaction(req: NewTransactionRequest):
    accounts = db.get_all_accounts()
    funds = db.get_all_funds()
    market = fetch_live_market()
    
    tx_id = f"tx-{uuid.uuid4().hex[:8]}"
    now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
    t_type = req.type.upper().strip()
    amt = round(abs(float(req.amount_try)), 2)
    if amt <= 0:
        raise HTTPException(status_code=400, detail="İşlem tutarı sıfırdan büyük olmalıdır.")

    fcode = (req.code or "TRY").upper().strip()
    if t_type == "ALIM" and fcode not in market.get("funds", {}):
        market_service.ensure_fund_in_market(fcode)
        market = fetch_live_market()
    default_acc = "5004" if t_type in ["ALIM", "SATIM"] else "5001"
    acc_id = db.normalize_account_id(req.account_id, default_acc)
    to_acc_id = db.normalize_account_id(req.to_account_id, "5004")

    clean_desc = (req.description or "").strip()
    if not clean_desc:
        if t_type in ["ALIM", "SATIM"]:
            clean_desc = f"{fcode} Fon {'Alımı' if t_type == 'ALIM' else 'Satışı'}"
        elif t_type in ["VIRMAN", "TRANSFER"]:
            clean_desc = f"{acc_id} → {to_acc_id} Virman"
        else:
            clean_desc = f"{(req.category or 'İşlem').strip()} ({t_type})"
    
    if acc_id not in accounts:
        acc_id = list(accounts.keys())[0]
        
    curr_price = float(market["funds"].get(fcode, {}).get("price") or 0.0)
    explicit_shares = round(abs(float(req.shares)), 4) if (req.shares and req.shares > 0) else 0.0
    lot_step = market_service.get_fund_lot_step(fcode)
    leftover_try = 0.0

    # Valörde (henüz işleme girmemiş) fon alım emri
    if t_type == "ALIM" and req.is_pending:
        math_res = market_service.compute_fund_order_math(
            code=fcode,
            amount_try=amt,
            unit_price=curr_price,
            explicit_shares=explicit_shares,
            is_sell=False,
        )
        if not math_res["ok"]:
            raise HTTPException(status_code=400, detail=math_res["error"])
        est_sh = math_res["shares"]
        pending_amt = math_res["effective_amt"]
        exec_price = math_res["exec_price"]
        leftover_try = math_res["leftover_try"]
        fund_name = market["funds"].get(fcode, {}).get("name", fcode)
        sched = settlement_engine.compute_valor_schedule(fcode, now_tr().strftime("%Y-%m-%d %H:%M:%S"))
        order = db.create_pending_order(
            code=fcode,
            amount_try=pending_amt,
            estimated_shares=est_sh,
            estimated_price=exec_price,
            account_id=acc_id,
            fund_name=fund_name,
            expected_settlement=sched["expected_text"],
            note=clean_desc or f"{fcode} Alım Emri (Valörde • {sched['t_badge']})",
            deduct_cash=True,
            record_tx=True,
        )
        if explicit_shares > 0 and fcode in market_service.PPF_ACCRUAL_CODES:
            m_f = market.get("funds", {}).get(fcode, {})
            if m_f.get("accrued_price_estimated") and m_f.get("accrued_target_date") and exec_price > 0:
                market_service.save_bank_calibration(
                    fcode,
                    m_f["accrued_target_date"],
                    exec_price,
                    official_price=m_f.get("official_price"),
                    gap_days=int(m_f.get("accrued_gap_days") or 0),
                )
        try:
            krs_note = f"\n• Kasada Kalan Küsürat: ₺{leftover_try:,.2f}" if leftover_try >= 0.01 else ""
            telegram_bot.send_message(
                f"⏳ *VALÖRDE FON EMRİ (WEB)*\n"
                f"• Fon: {fcode} (Pay Katı: {lot_step})\n"
                f"• Tutar: ₺{pending_amt:,.2f} (~{est_sh:g} tahmini pay){krs_note}\n"
                f"• Beklenen Takas: {sched['expected_text']}\n"
                f"• Not: TEFAS fiyatı kesinleşince otomatik uyarı alacaksın."
            )
        except Exception:
            pass
        return {
            "status": "ok",
            "is_pending": True,
            "leftover_try": leftover_try,
            "lot_step": lot_step,
            "pending_order": settlement_engine.enrich_pending_order(order, market.get("funds", {})),
        }

    if t_type == "ALIM":
        math_res = market_service.compute_fund_order_math(
            code=fcode,
            amount_try=amt,
            unit_price=curr_price,
            explicit_shares=explicit_shares,
            is_sell=False,
        )
        if not math_res["ok"]:
            raise HTTPException(status_code=400, detail=math_res["error"])
        final_shares = math_res["shares"]
        effective_amt = math_res["effective_amt"]
        exec_price = math_res["exec_price"]
        leftover_try = math_res["leftover_try"]
    elif t_type == "SATIM":
        cur_shares = funds.get(fcode, {}).get("shares", 0.0)
        math_res = market_service.compute_fund_order_math(
            code=fcode,
            amount_try=amt,
            unit_price=curr_price,
            explicit_shares=explicit_shares,
            is_sell=True,
            held_shares=cur_shares,
        )
        if not math_res["ok"]:
            raise HTTPException(status_code=400, detail=math_res["error"])
        final_shares = math_res["shares"]
        effective_amt = math_res["effective_amt"]
        exec_price = math_res["exec_price"]
    else:
        final_shares = 0.0
        effective_amt = amt
        exec_price = 0.0

    if t_type in ("ALIM", "SATIM") and explicit_shares > 0 and fcode in market_service.PPF_ACCRUAL_CODES:
        m_f = market.get("funds", {}).get(fcode, {})
        if m_f.get("accrued_price_estimated") and m_f.get("accrued_target_date") and exec_price > 0:
            market_service.save_bank_calibration(
                fcode,
                m_f["accrued_target_date"],
                exec_price,
                official_price=m_f.get("official_price"),
                gap_days=int(m_f.get("accrued_gap_days") or 0),
            )

    new_tx = {
        "id": tx_id,
        "date": now_str,
        "type": t_type,
        "category": req.category.strip() if req.category else "İşlem",
        "amount_try": effective_amt,
        "shares": final_shares if t_type in ["ALIM", "SATIM"] else 0,
        "price": exec_price if t_type in ["ALIM", "SATIM"] else 0,
        "code": fcode,
        "account_id": acc_id,
        "to_account_id": to_acc_id if t_type in ["VIRMAN", "TRANSFER"] else None,
        "description": clean_desc
    }
    
    # DEFTER ETKİSİNİ VE BAKİYE/FON GÜNCELLEMESİNİ TEK ACID İŞLEMDE YANSIT:
    fund_name = market["funds"].get(fcode, {}).get("name", fcode) if t_type == "ALIM" else None
    db.apply_and_insert_transaction(new_tx, fund_name=fund_name, weighted_avg=True)

    # Telegram'a canlı bildirim gönder
    try:
        if t_type in ["ALIM", "SATIM"]:
            krs_line = f"\n• Artan Küsürat: ₺{leftover_try:,.2f} (Kasada kaldı)" if leftover_try >= 0.01 else ""
            telegram_bot.send_message(
                f"🔔 *YENİ İŞLEM (WEB)*\n"
                f"• İşlem: {t_type} ({fcode})\n"
                f"• Tutar: ₺{effective_amt:,.2f} ({final_shares:g} pay){krs_line}\n"
                f"• Açıklama: {clean_desc}"
            )
        else:
            telegram_bot.send_message(
                f"🔔 *YENİ HAREKET (WEB)*\n"
                f"• Tür: {t_type}\n"
                f"• Tutar: ₺{effective_amt:,.2f}\n"
                f"• Açıklama: {clean_desc}"
            )
    except Exception:
        pass

    return {"status": "ok", "transaction": new_tx, "leftover_try": leftover_try, "lot_step": lot_step}

class UpdateTransactionRequest(BaseModel):
    date: Optional[str] = None
    type: Optional[str] = None
    category: Optional[str] = None
    amount_try: Optional[float] = None
    shares: Optional[float] = None
    code: Optional[str] = None
    account_id: Optional[str] = None
    to_account_id: Optional[str] = None
    description: Optional[str] = None


@app.put("/api/transactions/{tx_id}")
def edit_transaction(tx_id: str, req: UpdateTransactionRequest):
    updates = req.dict(exclude_unset=True)
    if "amount_try" in updates and updates["amount_try"] is not None:
        if float(updates["amount_try"]) <= 0:
            raise HTTPException(status_code=400, detail="İşlem tutarı sıfırdan büyük olmalıdır.")
    updated = db.update_transaction_atomic(tx_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı")
    return {"status": "ok", "transaction": updated, "message": "İşlem güncellendi ve kasa bakiyeleri atomik olarak eşitlendi."}


class SettlePendingRequest(BaseModel):
    order_id: str
    settled_price: Optional[float] = None
    settled_shares: Optional[float] = None


class PendingOrderIdRequest(BaseModel):
    order_id: str


class MoveFundToPendingRequest(BaseModel):
    code: str
    note: Optional[str] = ""


@app.get("/api/pending-orders")
def list_pending_orders():
    market = fetch_live_market()
    orders = settlement_engine.enrich_pending_orders(db.get_pending_orders("BEKLIYOR"), market.get("funds", {}))
    return {
        "pending_orders": orders,
        "pending_total_try": db.get_pending_total_try(),
        "valor_config": settlement_engine.get_valor_config(),
    }


@app.post("/api/pending-orders/settle")
def settle_pending_order_api(req: SettlePendingRequest):
    orders = db.get_pending_orders("BEKLIYOR")
    target = next((o for o in orders if o["id"] == req.order_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Bekleyen emir bulunamadı.")
    market = fetch_live_market()
    enriched = settlement_engine.enrich_pending_order(target, market.get("funds", {}))
    confirmed_p = enriched.get("valor_schedule", {}).get("confirmed_price")
    live_p = market["funds"].get(target["code"], {}).get("price")
    use_price = req.settled_price if (req.settled_price and req.settled_price > 0) else (confirmed_p or live_p)
    try:
        res = db.settle_pending_order(req.order_id, settled_price=use_price, settled_shares=req.settled_shares)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pending-orders/cancel")
def cancel_pending_order_api(req: PendingOrderIdRequest):
    try:
        return db.cancel_pending_order(req.order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/pending-orders/move-from-fund")
def move_fund_to_pending_api(req: MoveFundToPendingRequest):
    try:
        return db.move_active_fund_to_pending(req.code, note=req.note or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ======================================================================
# MODÜL 1: OTOMATİK VALÖR AYARLARI & CANLI TEFAS KONTROLÜ
# ======================================================================
@app.get("/api/valor/config")
def get_valor_config_api():
    return settlement_engine.get_valor_config()


@app.post("/api/valor/config")
def save_valor_config_api(req: Dict[str, Any]):
    return settlement_engine.save_valor_config(req)


@app.post("/api/valor/check")
def check_valor_now_api():
    allow_tg = bool(os.environ.get("RENDER") or os.environ.get("FORCE_LOCAL_TELEGRAM_POLL"))
    if not allow_tg:
        try:
            allow_tg = not telegram_bot.is_cloud_instance_active()
        except Exception:
            allow_tg = False
    notify_cb = (lambda txt, kb: telegram_bot.send_message(txt, reply_markup=kb)) if allow_tg else None
    return settlement_engine.run_valor_automation_tick(
        notify_fn=notify_cb,
        refresh_tefas=True,
        source="web",
    )


# ======================================================================
# MODÜL 2: STOPAJ (VERGİ) & NET ÇIKIŞ / REEL KIYASLAMA MOTORU
# ======================================================================
@app.get("/api/tax/summary")
def get_tax_summary_api():
    return tax_engine.build_portfolio_tax_summary()


@app.post("/api/tax/rates")
def save_tax_rates_api(req: Dict[str, Any]):
    return {"rates": tax_engine.save_stopaj_rates(req)}


@app.post("/api/portfolio/calibrate-bank")
def calibrate_bank_portfolio_api(req: Dict[str, Any]):
    """Ziraat Mobil'deki maliyet + Top K/Z veya güncel değerle aktif fonları kuruşu kuruşuna eşler."""
    items = req.get("items") if isinstance(req.get("items"), list) else [req]
    funds = db.get_all_funds()
    market_funds = market_service.fetch_live_market().get("funds", {})
    updated = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").upper().strip()
        if not code or code not in funds:
            continue
        f_db = funds[code]
        shares = float(f_db.get("shares") or 0.0)
        if shares <= 0:
            continue
        buy_p = float(f_db.get("buy_price") or 0.0)
        cost_in = raw.get("cost_try")
        if cost_in is not None and str(cost_in).strip() != "":
            cost_val = float(cost_in)
            if cost_val > 0:
                buy_p = round(cost_val / shares, 6)
                db.update_fund(code, shares, buy_price=buy_p)

        m_f = market_funds.get(code, {})
        target_p = 0.0
        if raw.get("unit_price") is not None and float(raw.get("unit_price") or 0.0) > 0:
            target_p = float(raw["unit_price"])
        elif raw.get("current_value_try") is not None and str(raw.get("current_value_try")).strip() != "":
            target_p = float(raw["current_value_try"]) / shares
        elif raw.get("profit_try") is not None and str(raw.get("profit_try")).strip() != "":
            base_cost = float(cost_in) if (cost_in is not None and str(cost_in).strip() != "" and float(cost_in) > 0) else (shares * buy_p)
            target_p = (base_cost + float(raw["profit_try"])) / shares

        cal_saved = {}
        if target_p > 0:
            tdate = str(m_f.get("accrued_target_date") or m_f.get("tefas_date") or now_tr().date().isoformat())
            cal_saved = market_service.save_bank_calibration(
                code,
                tdate,
                round(target_p, 6),
                official_price=m_f.get("official_price"),
                gap_days=int(m_f.get("accrued_gap_days") or 0),
            )
        updated.append({"code": code, "buy_price": buy_p, "calibrated": cal_saved})
    return {"status": "ok", "updated": updated}


# ======================================================================
# MODÜL 3: MAAŞ / KYK AKILLI DAĞITIM SİHİRBAZI
# ======================================================================
@app.get("/api/allocator/config")
def get_allocator_config_api():
    return auto_allocator.get_allocator_config()


@app.post("/api/allocator/config")
def save_allocator_config_api(req: Dict[str, Any]):
    return auto_allocator.save_allocator_config(req)


@app.post("/api/allocator/preview")
def preview_allocator_api(req: Dict[str, Any]):
    return auto_allocator.preview_allocation(
        income_try=req.get("income_try"),
        living_reserve_try=req.get("living_reserve_try"),
        weights=req.get("weights"),
    )


@app.post("/api/allocator/execute")
def execute_allocator_api(req: Dict[str, Any]):
    try:
        res = auto_allocator.execute_allocation(
            income_try=float(req.get("income_try", 0.0)),
            living_reserve_try=float(req.get("living_reserve_try", 0.0)),
            weights=req.get("weights"),
            record_income=bool(req.get("record_income", True)),
            source_note=str(req.get("source_note") or "KYK / Gelir Akıllı Dağıtım"),
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/transactions/export.csv")
def export_transactions_csv():
    txs = db.get_all_transactions()
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Tarih", "Tür", "Kategori", "Tutar (TL)", "Fon/Kod", "Pay", "Birim Fiyat", "Kaynak Hesap", "Hedef Hesap", "Açıklama"])
    for t in txs:
        writer.writerow([
            t.get("date", ""),
            t.get("type", ""),
            t.get("category", ""),
            f"{float(t.get('amount_try', 0.0)):.2f}",
            t.get("code", "TRY"),
            f"{float(t.get('shares', 0.0)):g}",
            f"{float(t.get('price', 0.0)):.6f}",
            t.get("account_id", ""),
            t.get("to_account_id") or "",
            t.get("description", ""),
        ])
    filename = f"finans_defteri_{now_tr().date().strftime('%Y%m%d')}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: str):
    target_tx = db.delete_transaction_atomic(tx_id)
    if not target_tx:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı")

    return {"status": "ok", "message": "İşlem defterden silindi ve SQLite veritabanı atomik olarak geri alındı."}

@app.post("/api/transactions/undo")
def undo_last_transaction():
    reverted_tx = db.revert_last_transaction()
    if not reverted_tx:
        return {"status": "error", "message": "Geri alınacak son işlem bulunamadı."}
    return {
        "status": "ok",
        "message": f"Son işlem ({reverted_tx.get('description', reverted_tx.get('id'))}) başarıyla geri alındı.",
        "reverted_transaction": reverted_tx
    }

# ======================================================================
# CANLI HABER VE TELEGRAM BOT ENDPOINT'LERİ
# ======================================================================
@app.get("/api/news")
def get_live_news():
    return {"status": "ok", "news": news_service.fetch_live_news()}

@app.get("/api/telegram/status")
def get_telegram_status():
    return telegram_bot.get_bot_status()

class TelegramConfigRequest(BaseModel):
    bot_token: str
    enabled: bool = True

@app.post("/api/telegram/config")
def update_telegram_config(req: TelegramConfigRequest):
    cfg = telegram_bot.load_config()
    cfg["bot_token"] = req.bot_token.strip()
    cfg["enabled"] = req.enabled
    telegram_bot.save_config(cfg)
    if cfg["bot_token"]:
        telegram_bot.start_bot_worker()
    return {"status": "ok", "bot_status": telegram_bot.get_bot_status()}

@app.post("/api/telegram/test")
def test_telegram_message():
    success = telegram_bot.send_message("🚀 Finans Terminali Test Bildirimi: Telegram entegrasyonu başarıyla aktif edildi!")
    return {"status": "ok" if success else "failed", "message_sent": success}

class SimulateTelegramRequest(BaseModel):
    text: str

@app.post("/api/telegram/simulate")
def simulate_telegram(req: SimulateTelegramRequest):
    reply = telegram_bot.process_incoming_text(chat_id=999999, user_text=req.text)
    return {"status": "ok", "reply": reply}


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    secret_expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not secret_expected and os.environ.get("RENDER"):
        raise HTTPException(status_code=403, detail="Webhook gizli anahtarı tanımlı değil")
    if secret_expected:
        secret_got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
        if not hmac.compare_digest(secret_got, secret_expected):
            raise HTTPException(status_code=403, detail="Geçersiz webhook token")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz JSON")
    return telegram_bot.process_webhook_update(payload)


# ======================================================================
# 10.000 YOLLU DİNAMİK MONTE CARLO QUANT SİMÜLASYONU ENDPOINT'İ
# ======================================================================
@app.get("/api/simulation/monte-carlo")
def get_monte_carlo(monthly_deposit: float = 5500.0, initial: Optional[float] = None, target: float = 650000.0):
    market = fetch_live_market()
    funds = db.get_all_funds()
    pending_orders = db.get_pending_orders("BEKLIYOR")
    total_cash = db.get_total_cash()

    ppf_val = (
        funds.get("ZBJ", {}).get("shares", 0.0) * market["funds"].get("ZBJ", {}).get("price", 5.63)
        + funds.get("TZL", {}).get("shares", 0.0) * market["funds"].get("TZL", {}).get("price", 0.1497)
    )
    tca_val = funds.get("TCA", {}).get("shares", 0.0) * market["funds"].get("TCA", {}).get("price", 0.7556)
    aft_val = funds.get("AFT", {}).get("shares", 0.0) * market["funds"].get("AFT", {}).get("price", 0.8993)

    for po in pending_orders:
        code = str(po.get("code", "")).upper()
        amt = float(po.get("amount_try", 0.0))
        if code in ("ZBJ", "TZL"):
            ppf_val += amt
        elif code == "TCA":
            tca_val += amt
        elif code == "AFT":
            aft_val += amt

    live_total = total_cash + ppf_val + tca_val + aft_val

    init_val = float(initial) if initial is not None else round(live_total, 2)
    weights = {"PPF": ppf_val, "TCA": tca_val, "AFT": aft_val, "CASH": total_cash} if live_total >= 100.0 else None

    return run_monte_carlo(
        monthly_deposit=monthly_deposit,
        initial_wealth=init_val,
        target_wealth=target,
        weights=weights,
    )

# ======================================================================
# 4 LLM CANLI KONSENSÜS, MÜZAKERE & DERİN DÜŞÜNME (CHAIN-OF-THOUGHT)
# ======================================================================
@app.get("/api/council/live-verdict")
@app.post("/api/council/live-verdict")
def get_live_council_verdict(topic: Optional[str] = None, use_llm: bool = False):
    market = fetch_live_market()
    funds = db.get_all_funds()
    pending_orders = db.get_pending_orders("BEKLIYOR")
    pending_total = db.get_pending_total_try()
    news_items = news_service.fetch_live_news()
    top_headline = news_items[0]["title"] if news_items else "Likit nema ve repo getirileri stabil"
    second_headline = news_items[1]["title"] if len(news_items) > 1 else "BIST ve döviz dengesi korunuyor"

    funds_val = 0.0
    fund_metrics = {}
    for code, item in funds.items():
        sh = item.get("shares", 0.0)
        curr_p = market["funds"].get(code, {}).get("price", item.get("buy_price", 1.0))
        val = sh * curr_p
        funds_val += val
        fund_metrics[code] = {
            "shares": sh,
            "price": curr_p,
            "value": round(val, 2),
            "name": item.get("name", code)
        }

    total_cash = db.get_total_cash()
    total_nav = funds_val + total_cash + pending_total

    cash_pct = round((total_cash / total_nav * 100.0) if total_nav > 0 else 100.0, 1)
    
    zbj_val = fund_metrics.get("ZBJ", {}).get("value", 0.0)
    tzl_val = fund_metrics.get("TZL", {}).get("value", 0.0)
    pending_tca = sum(float(po.get("amount_try", 0.0)) for po in pending_orders if po.get("code") == "TCA")
    pending_aft = sum(float(po.get("amount_try", 0.0)) for po in pending_orders if po.get("code") == "AFT")
    pending_ppf = sum(float(po.get("amount_try", 0.0)) for po in pending_orders if po.get("code") in ("ZBJ", "TZL"))
    tca_val = fund_metrics.get("TCA", {}).get("value", 0.0) + pending_tca
    aft_val = fund_metrics.get("AFT", {}).get("value", 0.0) + pending_aft
    total_ppf = zbj_val + tzl_val + pending_ppf

    ppf_pct = round((total_ppf / total_nav * 100.0) if total_nav > 0 else 0.0, 1)
    tca_pct = round((tca_val / total_nav * 100.0) if total_nav > 0 else 0.0, 1)
    aft_pct = round((aft_val / total_nav * 100.0) if total_nav > 0 else 0.0, 1)

    ppf_rate = market_service.get_live_ppf_daily_rate()
    daily_nema_est = total_ppf * ppf_rate
    burn_stats = db.get_monthly_burn_stats()
    macro = market.get("macro", {})
    funds_mkt = market.get("funds", {})
    tca_m = funds_mkt.get("TCA", {})
    aft_m = funds_mkt.get("AFT", {})
    zbj_m = funds_mkt.get("ZBJ", {})

    if not topic or not topic.strip():
        topic = "Canlı Portföy Durumu: ZBJ Nema, TCA Altın ve Büyüme Stratejisi"

    t_clean = topic.lower().strip()

    # Rakam Çıkarma (8k, 8000, 4000 vb.)
    amount = None
    k_match = re.search(r'(\d+(?:[.,]\d+)?)\s*k\b', t_clean)
    if k_match:
        try:
            amount = float(k_match.group(1).replace(',', '.')) * 1000.0
        except ValueError:
            pass
            
    if not amount:
        num_matches = re.findall(r'(\d+(?:[.,]\d+)?)', t_clean)
        for n in num_matches:
            try:
                val = float(n.replace(',', '.'))
                if val >= 50:
                    amount = val
                    break
            except ValueError:
                pass

    zbj_p = market["funds"].get("ZBJ", {}).get("price", 5.624192)
    tzl_p = market["funds"].get("TZL", {}).get("price", 0.149674)
    tca_p = market["funds"].get("TCA", {}).get("price", 0.755613)
    aft_p = market["funds"].get("AFT", {}).get("price", 0.899322)

    is_kyk = any(w in t_clean for w in ["kyk", "kredi", "burs", "öğrenci"])
    is_gold = any(w in t_clean for w in ["altın", "tca", "gram"])
    is_tech = any(w in t_clean for w in ["aft", "teknoloji", "yabancı", "dolar", "nasdaq", "nvidia", "apple"])
    is_kusurat = any(w in t_clean for w in ["küsürat", "kusurat", "kesinti", "10.20", "nerede", "kayıp", "yandı", "çaldı"])
    is_borc = any(w in t_clean for w in ["borç", "kredi kartı", "avans", "faiz"])

    amt = amount if amount else (8000.0 if is_kyk else total_nav)
    amt_fmt = f"₺{amt:,.2f}"

    # 1. QWEN 3.5 QUANT (Matematiksel Hesap & Günlük Kazanç)
    q_nema_day = total_ppf * ppf_rate
    q_nema_month = q_nema_day * 30.5
    idle_loss_month = total_cash * ppf_rate * 30.5

    accounts_dict = db.get_all_accounts()
    acc_5001_bal = accounts_dict.get("5001", {}).get("balance", 0.0)
    acc_5004_bal = accounts_dict.get("5004", {}).get("balance", 0.0)
    zbj_sh = fund_metrics.get("ZBJ", {}).get("shares", 0.0)
    tzl_sh = fund_metrics.get("TZL", {}).get("shares", 0.0)
    tca_sh = fund_metrics.get("TCA", {}).get("shares", 0.0)
    aft_sh = fund_metrics.get("AFT", {}).get("shares", 0.0)

    qwen_think = (
        f"[SAYISAL ANALİZ MOTORU // CANLI VERİ]\n"
        f"1. Konu: '{topic}' | Toplam Birikim: ₺{total_nav:,.2f}\n"
        f"2. Nakit Kasası: ₺{total_cash:,.2f} (5001: ₺{acc_5001_bal:,.2f}, 5004: ₺{acc_5004_bal:,.2f})\n"
        f"3. Gerçek Fon Payları: {int(zbj_sh):,} ZBJ (₺{zbj_val:,.2f}) + {int(tca_sh):,} TCA (₺{tca_val:,.2f}) + {int(tzl_sh):,} TZL (₺{tzl_val:,.2f}) + {int(aft_sh):,} AFT (₺{aft_val:,.2f})\n"
        f"4. Canlı Günlük Getiri Oranı: ZBJ/TZL 5G ort. %{ppf_rate*100:.3f}/gün -> Günde ~₺{q_nema_day:.2f} (Aylık ~₺{q_nema_month:.2f})\n"
        f"5. 30G Zirveye Göre Konum: TCA %{tca_m.get('drawdown_from_high_pct', 0):+.2f}, AFT %{aft_m.get('drawdown_from_high_pct', 0):+.2f}"
    )

    if is_kyk:
        qwen_verdict = (
            f"KYK / Bütçe Planı ({amt_fmt}): Bu tutarın %55'ini ({amt*0.55:,.0f} TL) günlük ortalama %{ppf_rate*100:.2f} getiriyle çalışan ZBJ'ye koyarsan ayda ~₺{amt*0.55*ppf_rate*30.5:.1f} düzenli kazanç üretir. "
            f"Kalan %25 AFT (30G zirveden %{aft_m.get('drawdown_from_high_pct', 0):+.1f}) ve %20 TCA Altın ile hem enflasyondan korunur hem büyüme yakalarsın."
        )
    elif is_kusurat:
        qwen_verdict = (
            f"Kayıp / Kesinti Yok (%0): TEFAS fonları tam pay adetleriyle alınır (virgüllü pay satılmaz). "
            f"Örneğin tam pay bedeli alındıktan sonra artan kuruşlar doğrudan 5004 vadesiz hesabında (şu an ₺{acc_5004_bal:,.2f}) nakit kalır."
        )
    else:
        idle_note = f" Vadesizde duran ₺{total_cash:,.0f} tutarını ZBJ'ye çeksen ayda +₺{idle_loss_month:.1f} ek kazanç gelir." if total_cash >= 100 else ""
        qwen_verdict = (
            f"Sayısal Özet: Toplam ₺{total_nav:,.2f} birikimin %{ppf_pct:.1f}'i günlük faiz fonlarında (günde +₺{q_nema_day:.2f}), "
            f"%{tca_pct:.1f}'i Altın'da (7G %{tca_m.get('weekly_chg', tca_m.get('weekly_yield_pct', 0)):+.2f}), %{aft_pct:.1f}'i Teknoloji'de.{idle_note}"
        )

    # 2. DEEPSEEK-R1 RISK (Güvence & Acil Durum Tamponu)
    liquid_cushion = total_cash + total_ppf
    monthly_burn = burn_stats.get("monthly_burn_try", 0.0)
    runway_txt = f"{liquid_cushion / monthly_burn:.1f} ay" if burn_stats.get("has_burn_history") and monthly_burn > 0 else "harcama kaydı arttıkça hesaplanacak"
    ds_think = (
        f"[GÜVENCE VE RİSK ANALİZİ]\n"
        f"1. Hazır Nakit Güvencesi (Nakit + ZBJ/TZL): ₺{liquid_cushion:,.2f} (Güvence süresi: {runway_txt})\n"
        f"2. Enflasyon Kalkanı: Altın (TCA) ağırlığı %{tca_pct:.1f} (₺{tca_val:,.2f}).\n"
        f"3. Yoğunlaşma Kontrolü: {'Tek fonda yüksek yoğunlaşma var, yeni girişlerde dengeleme faydalı.' if max(ppf_pct, tca_pct, aft_pct) > 55 else 'Varlık dağılımı dengeli görünüyor.'}\n"
        f"4. Güncel Haber Etkisi: '{top_headline}'"
    )

    if is_kyk:
        ds_verdict = (
            f"Güvenlik Kalkanı: {amt_fmt} bütçenin tamamını uzun vadeli fona bağlama. "
            f"₺{amt*0.15:,.0f} acil durum için 5001'de nakit kalsın; kalan ₺{amt*0.85:,.0f} aynı gün nakde dönen ZBJ (%55) ve Altın (%30) arasında bölünsün."
        )
    else:
        conc_msg = "Tek bir varlıkta (%55+) ağırlık birikmiş; yeni parayı daha zayıf taraflara dağıtmak riski azaltır." if max(ppf_pct, tca_pct, aft_pct) > 55 else "Acil nakit tamponun ve risk dağılımın güvenli sınırda."
        ds_verdict = (
            f"Güvence Durumu: Hızlı ulaşılabilir paran ₺{liquid_cushion:,.2f} ({runway_txt}). "
            f"Altın oranı %{tca_pct:.1f}, günlük faiz fonu oranı %{ppf_pct:.1f}. {conc_msg}"
        )

    # 3. LLAMA 3.3 / CLAUDE MACRO (Küresel Makro & Piyasa Trendi)
    usd_val = float(macro.get("usd_try", 0.0))
    bist_val = float(macro.get("bist100", 0.0))
    bist_chg = float(macro.get("bist100_chg_pct", 0.0))
    ndx_val = float(macro.get("nasdaq100", 0.0))
    ndx_chg = float(macro.get("nasdaq_chg_pct", 0.0))
    ndx_w = float(macro.get("nasdaq_weekly_pct", 0.0))

    macro_think = (
        f"[KÜRESEL VE YEREL PİYASA TAKİBİ]\n"
        f"1. Kur & Altın: Dolar ₺{usd_val:.2f} (1G %{macro.get('usd_chg_pct', 0):+.2f}), Gram Altın ₺{macro.get('gram_altin_try', 0):,.1f}.\n"
        f"2. Nasdaq 100 (AFT Yönü): {ndx_val:,.0f} (1G %{ndx_chg:+.2f} • 7G %{ndx_w:+.2f}) | AFT 30G değişim: %{aft_m.get('monthly_yield_pct', 0):+.2f}.\n"
        f"3. BIST 100: {bist_val:,.0f} (1G %{bist_chg:+.2f}) | Gündem: '{second_headline}'."
    )

    aft_hint = (
        f"Nasdaq son günlerde sakin/geri çekilmiş görünüyor (1G %{ndx_chg:+.2f}, AFT 30G zirveden %{aft_m.get('drawdown_from_high_pct', 0):+.1f}); ufak eklemeler mantıklı olabilir."
        if (ndx_chg < -0.5 or aft_m.get("drawdown_from_high_pct", 0) <= -2.0)
        else f"Nasdaq 7 günde %{ndx_w:+.2f} seyrinde; AFT'de acele etmeden geri çekilmeleri beklemek daha güvenli."
    )
    macro_verdict = (
        f"Piyasa Yorumu: Dolar ₺{usd_val:.2f}, BIST 100 {bist_val:,.0f} (%{bist_chg:+.2f}) seviyesinde. "
        f"ZBJ günlük ~%{zbj_m.get('avg_daily_nema_pct', ppf_rate*100):.2f} getiriyle nakdi enflasyona karşı tutarken; {aft_hint}"
    )

    # 4. FURKAN FİNANS AI (Sade Ortak Özet)
    furkan_think = (
        f"[ORTAK KARAR SENTEZİ]\n"
        f"1. Gerçek Varlıkların: TCA {int(tca_sh):,} pay (₺{tca_val:,.2f}) + ZBJ {int(zbj_sh):,} pay (₺{zbj_val:,.2f}) + TZL {int(tzl_sh):,} pay (₺{tzl_val:,.2f}) + AFT {int(aft_sh):,} pay (₺{aft_val:,.2f}).\n"
        f"2. Vadesiz Nakit: ₺{total_cash:,.2f} (5001: ₺{acc_5001_bal:,.2f} | 5004: ₺{acc_5004_bal:,.2f}).\n"
        f"3. Güncel Toplam: ₺{total_nav:,.2f} (~${total_nav/max(1.0, usd_val):,.1f})."
    )

    if is_kusurat:
        furkan_verdict = (
            f"Sade Açıklama:\n"
            f"1. Hiçbir gizli kesinti yok; fonlar yalnızca tam sayı (1, 2, 10 pay gibi) olarak satın alınır.\n"
            f"2. Tam payların toplam bedeli kesildikten sonra artan kuruşlar 5004 vadesiz hesabında (şu an ₺{acc_5004_bal:,.2f}) nakit olarak kalır."
        )
        consensus_steps = [
            "Banka ekstra kesintisi: %0",
            f"TCA Altın: {int(tca_sh):,} Pay = ₺{tca_val:,.2f}",
            f"5004 Kalan Nakit: ₺{acc_5004_bal:,.2f}"
        ]
    else:
        tca_tag = "indirime yakın" if tca_m.get("drawdown_from_high_pct", 0) <= -1.2 else "dengeli"
        aft_tag = "fırsat bölgesinde" if aft_m.get("drawdown_from_high_pct", 0) <= -2.0 else "izlemede"
        furkan_verdict = (
            f"Sade Özetin:\n"
            f"1. Toplam Birikimin: ₺{total_nav:,.2f} (Günlük kazanç hızı ~₺{q_nema_day:.2f}/gün).\n"
            f"2. Altın (TCA): {int(tca_sh):,} pay (₺{tca_val:,.2f} • %{tca_pct:.1f}) — 30G zirveden %{tca_m.get('drawdown_from_high_pct', 0):+.1f} ({tca_tag}).\n"
            f"3. Başak + Likit (ZBJ/TZL): ₺{total_ppf:,.2f} (%{ppf_pct:.1f}) — her gece otomatik büyüyor.\n"
            f"4. Teknoloji (AFT): {int(aft_sh):,} pay (₺{aft_val:,.2f}) — {aft_tag}; nakdin ₺{total_cash:,.2f}."
        )
        consensus_steps = [
            f"TCA Altın: {int(tca_sh):,} Pay (₺{tca_val:,.2f} • 7G %{tca_m.get('weekly_yield_pct', 0):+.2f})",
            f"ZBJ Başak: {int(zbj_sh):,} Pay (₺{zbj_val:,.2f} • Günlük ~%{zbj_m.get('avg_daily_nema_pct', ppf_rate*100):.2f})",
            f"AFT Teknoloji: {int(aft_sh):,} Pay (₺{aft_val:,.2f} • 7G %{aft_m.get('weekly_yield_pct', 0):+.2f})"
        ]

    ai_cfg = ai_advisor.get_ai_config()
    only_gemini = bool(ai_cfg.get("gemini_api_key"))

    return {
        "status": "ok",
        "topic": topic,
        "metrics": {
            "total_nav": round(total_nav, 2),
            "total_cash": round(total_cash, 2),
            "funds_val": round(funds_val, 2),
            "zbj_val": round(zbj_val, 2),
            "tzl_val": round(tzl_val, 2),
            "tca_val": round(tca_val, 2),
            "aft_val": round(aft_val, 2),
            "cash_pct": cash_pct,
            "ppf_pct": ppf_pct,
            "tca_pct": tca_pct,
            "aft_pct": aft_pct,
            "daily_nema_est": round(daily_nema_est, 2)
        },
        "news": news_items,
        "council": {
            "qwen3": {
                "name": "Matematik Uzmanı (Gemini 3 Flash)" if only_gemini else "Matematik Uzmanı (Kural / AI)",
                "short_code": "G3" if only_gemini else "M1",
                "role": "Büyüme & Sayısal Arbitraj Motoru",
                "badge": "GEMINI 3" if only_gemini else "SAYISAL",
                "color": "cyan",
                "execution_time": "Hazır" if only_gemini else "0.1s",
                "think": qwen_think,
                "analysis": qwen_verdict,
                "verdict": qwen_verdict
            },
            "deepseek": {
                "name": "Risk & Valör Sentineli (Gemini 3 Flash)" if only_gemini else "Risk & Valör Sentineli (Kural / AI)",
                "short_code": "G3" if only_gemini else "R1",
                "role": "Sermaye Koruma & Defans Sentineli",
                "badge": "GEMINI 3" if only_gemini else "GÜVENLİK",
                "color": "amber",
                "execution_time": "Hazır" if only_gemini else "0.1s",
                "think": ds_think,
                "analysis": ds_verdict,
                "verdict": ds_verdict
            },
            "claude_macro": {
                "name": "Makro & Haber Analisti (Gemini Flash-Lite)" if only_gemini else "Ekonomi Uzmanı (Kural / AI)",
                "short_code": "GL" if only_gemini else "MK",
                "role": "Küresel Makro & Büyüme Analisti",
                "badge": "GEMINI 3" if only_gemini else "PİYASA",
                "color": "purple",
                "execution_time": "Hazır" if only_gemini else "0.1s",
                "think": macro_think,
                "analysis": macro_verdict,
                "verdict": macro_verdict
            },
            "furkan_ai": {
                "name": "Baş Stratejist Sentezi (Gemini Flash-Lite)" if only_gemini else "Kişisel Danışman Sentezi",
                "short_code": "GS" if only_gemini else "FA",
                "role": "Yürütücü Lider & Baş Konsensüs Stratejisti",
                "badge": "SENTEZ",
                "color": "emerald",
                "execution_time": "Hazır" if only_gemini else "0.1s",
                "think": furkan_think,
                "analysis": furkan_verdict,
                "verdict": furkan_verdict
            },
            "consensus": {
                "name": "Mala Anlatır Gibi Konsey Kararı",
                "role": "Uzman Ajanların Ortak Sentezi",
                "badge": "FINAL DECISION",
                "summary": furkan_verdict,
                "steps": consensus_steps
            }
        }
    }

@app.post("/api/council/debate")
def run_council_debate(req: dict):
    topic = (req.get("topic") or "").strip() or "Canlı Portföy Durumu: ZBJ Nema, Valörde Bekleyen Emirler, TCA Altın ve Büyüme Stratejisi"
    base_res = get_live_council_verdict(topic=topic, use_llm=True)
    llm_res = ai_advisor.run_council_llm_debate(topic=topic)
    if llm_res.get("live_llm"):
        c = base_res["council"]
        role_titles = {
            "qwen3": "Matematik Uzmanı",
            "deepseek": "Risk & Valör Sentineli",
            "claude_macro": "Makro & Haber Analisti",
        }
        def _short_code(mname: str) -> str:
            low = str(mname or "").lower()
            if "gemini-3" in low: return "G3"
            if "flash-lite" in low: return "GL"
            if "gemini" in low: return "GM"
            if "deepseek" in low: return "R1"
            if "qwen" in low: return "Q3"
            if "llama" in low: return "L3"
            return "AI"

        for key in ("qwen3", "deepseek", "claude_macro"):
            r = llm_res.get(key)
            if r:
                m_used = r.get("model_used", "")
                c[key]["name"] = f"{role_titles[key]} ({m_used})"
                c[key]["short_code"] = _short_code(m_used)
                c[key]["execution_time"] = f"{r.get('elapsed_s', 1.5)}s"
                c[key]["badge"] = m_used[:28]
                if r.get("thoughts"):
                    c[key]["think"] = r["thoughts"]
                c[key]["analysis"] = r["answer"]
                c[key]["verdict"] = r["answer"]

        chair = llm_res.get("chair")
        if chair:
            cm_used = chair.get("model_used", "")
            c["furkan_ai"]["name"] = f"Baş Stratejist ({cm_used})"
            c["furkan_ai"]["short_code"] = _short_code(cm_used)
            c["furkan_ai"]["execution_time"] = f"{chair.get('elapsed_s', 1.2)}s"
            c["furkan_ai"]["badge"] = f"SENTEZ • {cm_used}"[:28]
            if chair.get("thoughts"):
                c["furkan_ai"]["think"] = chair["thoughts"]
            c["furkan_ai"]["analysis"] = chair["answer"]
            c["furkan_ai"]["verdict"] = chair["answer"]
            c["consensus"]["summary"] = chair["answer"]
            c["consensus"]["badge"] = "CANLI KONSEY KARARI"
        base_res["live_llm_used"] = True
    else:
        base_res["live_llm_used"] = False
    base_res["llm_reason"] = llm_res.get("llm_reason", "unknown")
    base_res["gemini_configured"] = bool(ai_advisor.get_ai_config().get("gemini_api_key"))
    return base_res

@app.post("/api/council/send-to-telegram")
def send_council_to_telegram(req: dict):
    topic = req.get("topic", "Konsey Kararı")
    verdict = req.get("verdict", "")
    if not verdict:
        return {"status": "error", "message": "Gönderilecek karar metni boş."}
    
    msg_text = (
        f"🏛️ *FİNANS KONSEYİ (4 LLM) NİHAİ KARARI*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *Tartışılan Konu:* {topic}\n\n"
        f"{verdict}\n\n"
        f"📱 *Tarih:* {now_tr().strftime('%d.%m.%Y %H:%M')}\n"
        f"🎯 *Hedef:* 4 Yıl Sonra Araba Peşinatı & Sıfır Borç!"
    )
    ok = telegram_bot.send_message(msg_text)
    if ok:
        return {"status": "ok", "message": "Konsey kararı Telegram botuna iletildi!"}
    else:
        return {"status": "error", "message": "Telegram mesajı iletilemedi. Bot bağlı mı?"}

class ChatRequest(BaseModel):
    message: str
    model: str = "auto"
    history: Optional[List[Dict[str, str]]] = None


@app.post("/api/chat")
def chat_with_ai(req: ChatRequest):
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")

    res = ai_advisor.ask_chat_engine(
        user_prompt=user_msg,
        model_pref=(req.model or "auto").strip(),
        history=req.history,
    )
    return {
        "reply": res.get("answer", ""),
        "thinking": res.get("thoughts", ""),
        "model_used": res.get("model_used", req.model),
        "provider": res.get("provider", "ai-advisor"),
        "elapsed_s": res.get("elapsed_s", 0.0),
    }


@app.get("/api/ai/config")
def get_ai_config_api():
    return ai_advisor.get_ai_config_public()


@app.post("/api/ai/config")
def save_ai_config_api(req: Dict[str, Any]):
    ai_advisor.save_ai_config(req)
    return {"status": "ok", "config": ai_advisor.get_ai_config_public()}


@app.post("/api/ai/test")
def test_ai_engine_api(req: Optional[Dict[str, Any]] = None):
    pref = (req or {}).get("model", "auto")
    res = ai_advisor.ask_chat_engine(
        user_prompt="Kısaca toplam portföyümü, valördeki emirleri ve bugün dikkat etmem gereken 1 şeyi söyle.",
        model_pref=pref,
    )
    return {
        "status": "ok" if res.get("provider") != "offline-rules" else "fallback",
        "provider": res.get("provider"),
        "model_used": res.get("model_used"),
        "elapsed_s": res.get("elapsed_s"),
        "preview": (res.get("answer") or "")[:360],
        "has_thoughts": bool(res.get("thoughts")),
    }

@app.get("/api/backup/download")
def download_backup():
    backup_data = db.export_full_backup()
    filename = f"finans_backup_{now_tr().date().strftime('%Y%m%d')}.json"
    return JSONResponse(
        content=backup_data,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/backup/restore")
async def restore_backup(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz JSON dosyası.")
    try:
        counts = db.import_full_backup(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "counts": counts, "message": "Yedek veritabanına başarıyla geri yüklendi!"}


@app.post("/api/backup/reconcile")
async def reconcile_backup(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz JSON verisi.")
    backup = payload.get("backup") if isinstance(payload, dict) else None
    deleted_ids = payload.get("deleted_ids") if isinstance(payload, dict) else None
    if not isinstance(backup, dict):
        raise HTTPException(status_code=400, detail="Eksik backup verisi.")
    try:
        result = db.reconcile_missing_backup(backup, deleted_ids=deleted_ids if isinstance(deleted_ids, list) else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", **result}


@app.post("/api/sync/pull-from-render")
def pull_live_data_from_render():
    """Uzak bulut sunucusundan (salt-okunur GET) güncel yedeği çekip yerel finans.db'ye eşitler."""
    import requests as _req
    render_url = os.environ.get("RENDER_REMOTE_URL", "").strip().rstrip("/")
    if not render_url:
        raise HTTPException(status_code=400, detail="RENDER_REMOTE_URL ortam değişkeni tanımlı değil.")
    cfg = load_security_config()
    secret = str(cfg.get("session_secret") or "").strip()
    ver = int(cfg.get("session_version", 1))
    if not secret:
        raise HTTPException(status_code=500, detail="Yerel oturum anahtarı (session_secret) bulunamadı.")
    tok = create_session_token(secret, ver)
    try:
        resp = _req.get(
            f"{render_url}/api/backup/download",
            cookies={"finans_session": tok},
            timeout=20.0,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Render bulut sunucusuna bağlanılamadı: {exc}")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Render yedek okuma hatası (HTTP {resp.status_code}).")
    try:
        remote_data = resp.json()
        counts = db.import_full_backup(remote_data, push_cloud=False, replace_history=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Render verisi işlenemedi: {exc}")
    holdings = remote_data.get("holdings") or {}
    active_funds = sum(1 for f in (holdings.get("funds") or {}).values() if float(f.get("shares") or 0) > 0)
    return {
        "status": "ok",
        "source": render_url,
        "remote_created_at": remote_data.get("created_at"),
        "cash_try": holdings.get("cash_try", 0.0),
        "active_funds": active_funds,
        "counts": counts,
        "message": "Render bulut verileri yerel veritabanına güvenle eşitlendi (Render'a dokunulmadı).",
    }


@app.get("/api/system/persistence")
def get_system_persistence(request: Request):
    st = db.get_persistence_status()
    base = str(request.base_url).rstrip("/")
    st["health_url"] = f"{base}/api/health"
    st["boot_id"] = _SERVER_BOOT_ID
    return st


@app.get("/api/portfolio/history")
def get_portfolio_history(days: int = 30):
    market = fetch_live_market()
    macro = market.get("macro", {})
    usd_try = max(1.0, float(macro.get("usd_try", 42.0)))
    gram_try = max(1.0, float(macro.get("gram_altin_try", 6000.0)))

    # Bugünün canlı değerini her sorguda güncel tut (grafik anlık yansısın)
    funds = db.get_all_funds()
    total_cash = db.get_total_cash()
    pending_total = db.get_pending_total_try()
    funds_val = sum(
        float(item.get("shares", 0.0)) * float(market["funds"].get(code, {}).get("price", item.get("buy_price", 0.0)))
        for code, item in funds.items()
    )
    live_total = round(total_cash + funds_val + pending_total, 2)
    live_invested = round(funds_val + pending_total, 2)
    try:
        db.ensure_net_worth_backfill(market_service.fetch_live_market().get("funds", {}))
        db.record_net_worth_snapshot(live_total, round(total_cash, 2), live_invested)
    except Exception:
        pass

    pts = db.get_net_worth_history(days=days)
    enriched_pts = []
    for p in pts:
        t_try = float(p.get("total_try", 0.0))
        enriched_pts.append({
            **p,
            "usd_eq": round(t_try / usd_try, 2),
            "gold_gr": round(t_try / gram_try, 3),
        })

    # Base-100 Benchmark Race Series (Portföy vs ZBJ vs Gram Altın vs Dolar)
    race_100: Dict[str, Any] = {"dates": [], "portfolio": [], "zbj": [], "gold": [], "usd": []}
    if enriched_pts:
        def _series_map(items: Any) -> Dict[str, float]:
            out: Dict[str, float] = {}
            for it in (items or []):
                if isinstance(it, dict) and it.get("date") and float(it.get("price") or 0) > 0:
                    out[str(it["date"])[:10]] = float(it["price"])
            return out

        usd_map = _series_map(macro.get("usd_series"))
        gold_map = _series_map(macro.get("gram_altin_series"))
        zbj_raw = (market.get("funds") or {}).get("ZBJ", {}).get("prices_by_date") or {}
        zbj_map = {str(k)[:10]: float(v) for k, v in zbj_raw.items() if float(v or 0) > 0}

        def _val_on_or_before(mp: Dict[str, float], d: str, fallback: float) -> float:
            if not mp:
                return fallback
            if d in mp:
                return mp[d]
            prior = [k for k in mp.keys() if k <= d]
            if prior:
                return mp[max(prior)]
            return mp[min(mp.keys())]

        d0 = str(enriched_pts[0].get("date", ""))[:10]
        p0 = max(1e-6, float(enriched_pts[0].get("total_try") or 1.0))
        z0 = max(1e-6, _val_on_or_before(zbj_map, d0, 1.0))
        g0 = max(1e-6, _val_on_or_before(gold_map, d0, gram_try))
        u0 = max(1e-6, _val_on_or_before(usd_map, d0, usd_try))

        for pt in enriched_pts:
            dk = str(pt.get("date", ""))[:10]
            race_100["dates"].append(dk)
            race_100["portfolio"].append(round((float(pt.get("total_try") or p0) / p0) * 100.0, 2))
            race_100["zbj"].append(round((_val_on_or_before(zbj_map, dk, z0) / z0) * 100.0, 2))
            race_100["gold"].append(round((_val_on_or_before(gold_map, dk, g0) / g0) * 100.0, 2))
            race_100["usd"].append(round((_val_on_or_before(usd_map, dk, u0) / u0) * 100.0, 2))

    change_try = 0.0
    change_pct = 0.0
    if len(enriched_pts) >= 2 and enriched_pts[0]["total_try"] > 0:
        change_try = round(enriched_pts[-1]["total_try"] - enriched_pts[0]["total_try"], 2)
        change_pct = round((change_try / enriched_pts[0]["total_try"]) * 100.0, 2)

    return {
        "status": "ok",
        "days": days,
        "points": enriched_pts,
        "race_100": race_100,
        "change_try": change_try,
        "change_pct": change_pct,
        "live_breakdown": {
            "total_try": live_total,
            "cash_try": round(total_cash, 2),
            "settled_funds_try": round(funds_val, 2),
            "pending_total_try": round(pending_total, 2),
            "usd_try": usd_try,
            "gram_altin_try": gram_try,
            "updated_at": market.get("updated_at") or now_tr().strftime("%H:%M:%S"),
        },
    }


@app.get("/api/portfolio/rebalance")
def get_portfolio_rebalance(
    new_cash: float = 2500.0,
    ppf_target: float = 45.0,
    tca_target: float = 35.0,
    aft_target: float = 20.0,
):
    market = fetch_live_market()
    funds = db.get_all_funds()
    total_target = max(1.0, ppf_target + tca_target + aft_target)
    t_ppf = ppf_target / total_target
    t_tca = tca_target / total_target
    t_aft = aft_target / total_target

    zbj_p = market["funds"].get("ZBJ", {}).get("price", 5.63)
    tzl_p = market["funds"].get("TZL", {}).get("price", 0.1497)
    tca_p = market["funds"].get("TCA", {}).get("price", 0.7556)
    aft_p = market["funds"].get("AFT", {}).get("price", 0.8993)

    cur_ppf = (funds.get("ZBJ", {}).get("shares", 0.0) * zbj_p) + (funds.get("TZL", {}).get("shares", 0.0) * tzl_p)
    cur_tca = funds.get("TCA", {}).get("shares", 0.0) * tca_p
    cur_aft = funds.get("AFT", {}).get("shares", 0.0) * aft_p

    pending_orders = db.get_pending_orders("BEKLIYOR")
    pending_included = 0.0
    for po in pending_orders:
        code = str(po.get("code", "")).upper()
        amt = float(po.get("amount_try", 0.0))
        pending_included += amt
        if code in ("ZBJ", "TZL"):
            cur_ppf += amt
        elif code == "TCA":
            cur_tca += amt
        elif code == "AFT":
            cur_aft += amt

    cur_funds_total = cur_ppf + cur_tca + cur_aft

    add_cash = max(0.0, float(new_cash))
    post_total = cur_funds_total + add_cash

    # Hedefe göre eksikler (sadece pozitif farklar üzerinden yeni nakdi dağıt)
    deficits = {
        "PPF": max(0.0, (post_total * t_ppf) - cur_ppf),
        "TCA": max(0.0, (post_total * t_tca) - cur_tca),
        "AFT": max(0.0, (post_total * t_aft) - cur_aft),
    }
    sum_def = sum(deficits.values())
    if add_cash > 0 and sum_def > 0:
        alloc_ppf = round(add_cash * (deficits["PPF"] / sum_def), 2)
        alloc_tca = round(add_cash * (deficits["TCA"] / sum_def), 2)
        alloc_aft = round(max(0.0, add_cash - alloc_ppf - alloc_tca), 2)
    else:
        alloc_ppf = round(add_cash * t_ppf, 2)
        alloc_tca = round(add_cash * t_tca, 2)
        alloc_aft = round(max(0.0, add_cash - alloc_ppf - alloc_tca), 2)

    items = [
        {
            "bucket": "PPF",
            "code": "ZBJ",
            "label": "ZBJ / TZL (Para Piyasası)",
            "current_value": round(cur_ppf, 2),
            "current_pct": round((cur_ppf / cur_funds_total * 100.0) if cur_funds_total > 0 else 0.0, 1),
            "target_pct": round(t_ppf * 100.0, 1),
            "recommended_try": alloc_ppf,
            "est_shares": int(alloc_ppf / zbj_p) if zbj_p > 0 else 0,
            "unit_price": zbj_p,
        },
        {
            "bucket": "TCA",
            "code": "TCA",
            "label": "TCA (Altın Katılım)",
            "current_value": round(cur_tca, 2),
            "current_pct": round((cur_tca / cur_funds_total * 100.0) if cur_funds_total > 0 else 0.0, 1),
            "target_pct": round(t_tca * 100.0, 1),
            "recommended_try": alloc_tca,
            "est_shares": int(alloc_tca / tca_p) if tca_p > 0 else 0,
            "unit_price": tca_p,
        },
        {
            "bucket": "AFT",
            "code": "AFT",
            "label": "AFT (Yabancı Teknoloji)",
            "current_value": round(cur_aft, 2),
            "current_pct": round((cur_aft / cur_funds_total * 100.0) if cur_funds_total > 0 else 0.0, 1),
            "target_pct": round(t_aft * 100.0, 1),
            "recommended_try": alloc_aft,
            "est_shares": int(alloc_aft / aft_p) if aft_p > 0 else 0,
            "unit_price": aft_p,
        },
    ]
    return {
        "status": "ok",
        "current_funds_total": round(cur_funds_total, 2),
        "pending_included_try": round(pending_included, 2),
        "new_cash": round(add_cash, 2),
        "items": items,
    }


@app.get("/api/analytics/cashflow")
def get_cashflow_analytics(month: Optional[str] = None):
    txs = db.get_all_transactions()
    months = sorted({str(t.get("date", ""))[:7] for t in txs if len(str(t.get("date", ""))) >= 7}, reverse=True)
    target_month = (month or "").strip()
    if not target_month:
        target_month = now_tr().strftime("%Y-%m")

    filtered = txs if target_month == "ALL" else [t for t in txs if str(t.get("date", "")).startswith(target_month)]

    inflow = 0.0
    outflow = 0.0
    invested = 0.0
    sold = 0.0
    cat_map: Dict[str, float] = {}

    for t in filtered:
        tt = str(t.get("type", "")).upper().strip()
        amt = float(t.get("amount_try", 0.0))
        cat = str(t.get("category") or "Diğer").strip()
        if tt == "GİRİŞ":
            inflow += amt
        elif tt == "ÇIKIŞ":
            outflow += amt
            cat_map[cat] = round(cat_map.get(cat, 0.0) + amt, 2)
        elif tt == "ALIM":
            invested += amt
        elif tt == "SATIM":
            sold += amt

    savings_rate = round(((inflow - outflow) / inflow) * 100.0, 1) if inflow > 0 else 0.0
    categories = [
        {"category": k, "amount": v, "pct": round((v / outflow * 100.0) if outflow > 0 else 0.0, 1)}
        for k, v in sorted(cat_map.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "status": "ok",
        "month": target_month,
        "available_months": months,
        "total_inflow": round(inflow, 2),
        "total_outflow": round(outflow, 2),
        "total_invested": round(invested, 2),
        "total_sold": round(sold, 2),
        "net_cashflow": round(inflow - outflow, 2),
        "savings_rate_pct": max(-999.0, min(100.0, savings_rate)),
        "expense_by_category": categories,
    }


class RecurringRuleRequest(BaseModel):
    id: Optional[str] = None
    title: str
    type: str = "GİRİŞ"
    category: str = "Sabit Akış"
    amount_try: float
    account_id: str = "5001"
    day_of_month: int = 1
    enabled: bool = True


@app.get("/api/recurring")
def list_recurring_rules():
    return {"status": "ok", "rules": db.get_recurring_rules()}


@app.post("/api/recurring")
def save_recurring_rule(req: RecurringRuleRequest):
    if req.amount_try <= 0:
        raise HTTPException(status_code=400, detail="Tutar sıfırdan büyük olmalıdır.")
    rule = db.upsert_recurring_rule(req.dict())
    return {"status": "ok", "rule": rule}


@app.delete("/api/recurring/{rule_id}")
def remove_recurring_rule(rule_id: str):
    ok = db.delete_recurring_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Kural bulunamadı.")
    return {"status": "ok"}


@app.post("/api/recurring/{rule_id}/execute")
def run_recurring_rule_now(rule_id: str):
    rule = next((r for r in db.get_recurring_rules() if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="Kural bulunamadı.")
    tx = telegram_bot.execute_recurring_rule(rule)
    return {"status": "ok", "transaction": tx, "message": f"{rule['title']} deftere işlendi!"}


class PriceAlertRequest(BaseModel):
    code: str
    condition: str = "BELOW"
    threshold: float
    note: str = ""


@app.get("/api/alerts")
def list_price_alerts():
    return {"status": "ok", "alerts": db.get_price_alerts()}


@app.post("/api/alerts")
def add_price_alert(req: PriceAlertRequest):
    if req.threshold <= 0:
        raise HTTPException(status_code=400, detail="Eşik değeri sıfırdan büyük olmalıdır.")
    cond = req.condition.upper().strip()
    if cond not in ("BELOW", "ABOVE", "DROP_PCT", "DRAWDOWN_PCT"):
        raise HTTPException(status_code=400, detail="Geçersiz alarm koşulu.")
    alert = db.create_price_alert(req.code, cond, req.threshold, req.note)
    return {"status": "ok", "alert": alert}


@app.delete("/api/alerts/{alert_id}")
def remove_price_alert(alert_id: str):
    ok = db.delete_price_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alarm bulunamadı.")
    return {"status": "ok"}


class KykHelperConfigRequest(BaseModel):
    prior_kyk_try: float = 0.0
    monthly_kyk_try: float = 4000.0
    total_months_plan: int = 44


def _load_kyk_helper_cfg() -> Dict[str, Any]:
    raw = db.get_setting("kyk_helper_config", "")
    defaults = {"prior_kyk_try": 0.0, "monthly_kyk_try": 4000.0, "total_months_plan": 44}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                defaults["prior_kyk_try"] = max(0.0, float(parsed.get("prior_kyk_try", 0.0)))
                defaults["monthly_kyk_try"] = max(100.0, float(parsed.get("monthly_kyk_try", 4000.0)))
                defaults["total_months_plan"] = max(1, min(96, int(parsed.get("total_months_plan", 44))))
        except Exception:
            pass
    return defaults


@app.post("/api/helpers/kyk-config")
def save_kyk_helper_config(req: KykHelperConfigRequest):
    cfg = {
        "prior_kyk_try": round(max(0.0, float(req.prior_kyk_try)), 2),
        "monthly_kyk_try": round(max(100.0, float(req.monthly_kyk_try)), 2),
        "total_months_plan": max(1, min(96, int(req.total_months_plan))),
    }
    db.set_setting("kyk_helper_config", json.dumps(cfg, ensure_ascii=False), sync_cloud=True)
    return {"status": "ok", "config": cfg}


@app.get("/api/helpers/summary")
def get_helpers_summary():
    now_dt = now_tr()
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    market = fetch_live_market()
    funds_m = market.get("funds", {})
    txs = db.get_all_transactions()
    pending_orders = db.get_pending_orders()
    accounts = db.get_all_accounts()
    db_funds = db.get_all_funds()

    # 1. TEFAS 13:30 Kesim & Canlı Valör Takvimi
    is_biz = settlement_engine.is_tr_business_day(now_dt.date())
    cutoff_dt = now_dt.replace(hour=13, minute=30, second=0, microsecond=0)
    if is_biz and now_dt < cutoff_dt:
        seconds_to_cutoff = int((cutoff_dt - now_dt).total_seconds())
        cutoff_state = "OPEN"
    else:
        next_biz = settlement_engine.next_tr_business_day(now_dt.date(), 1 if is_biz else 0)
        next_open = datetime.datetime.combine(next_biz, datetime.time(9, 0, 0))
        seconds_to_cutoff = max(0, int((next_open - now_dt).total_seconds()))
        cutoff_state = "AFTER_CUTOFF" if is_biz else "WEEKEND_OR_HOLIDAY"

    schedules = []
    for fcode in ["ZBJ", "TZL", "TCA", "AFT"]:
        sched = settlement_engine.compute_valor_schedule(fcode, now_str)
        if fcode == "ZBJ" and is_biz and not sched["after_cutoff"] and now_dt.weekday() == 4:
            nema_note = "Cuma 13:30 öncesi! Alırsan Cuma+Cmt+Paz 3 gecelik nema yakalar."
        elif fcode in ("ZBJ", "TZL") and not sched["after_cutoff"]:
            nema_note = "Bugün hesaba geçer; ilk gecelik nema ertesi sabah yansır."
        elif fcode == "TZL" and is_biz and now_dt.hour < 16:
            nema_note = "16:00'ya kadar Ziraat içi anında alım/satım (T+0)."
        else:
            nema_note = f"{sched['settle_label']} günü (~{sched['typical_window']}) hesabına işlenir."
        schedules.append({
            "code": fcode,
            "t_badge": sched["t_badge"],
            "cutoff": sched["cutoff"],
            "after_cutoff": sched["after_cutoff"],
            "price_label": sched["price_label"],
            "settle_label": sched["settle_label"],
            "typical_window": sched["typical_window"],
            "nema_note": nema_note,
        })

    # 3. Aylık Bütçe Karnesi (Bu Ay YYYY-MM)
    month_key = now_dt.strftime("%Y-%m")
    tr_months = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    month_label = f"{tr_months[now_dt.month - 1]} {now_dt.year}"
    days_in_month = ((now_dt.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)).day
    days_left = max(1, days_in_month - now_dt.day + 1)

    m_income = 0.0
    m_expense = 0.0
    m_buy = 0.0
    m_sell = 0.0
    cat_spend: Dict[str, float] = {}
    recorded_kyk_try = 0.0
    recorded_kyk_count = 0

    for tx in txs:
        dt_s = str(tx.get("date", ""))[:7]
        ttype = str(tx.get("type", "")).upper().strip()
        amt = abs(float(tx.get("amount_try") or 0.0))
        cat = str(tx.get("category") or "Diğer").strip() or "Diğer"
        desc_low = f"{cat} {tx.get('description', '')}".lower()

        if ttype == "GİRİŞ" and "kyk" in desc_low:
            recorded_kyk_try += amt
            recorded_kyk_count += 1

        if dt_s != month_key:
            continue
        if ttype == "GİRİŞ":
            m_income += amt
        elif ttype == "ÇIKIŞ":
            m_expense += amt
            cat_spend[cat] = cat_spend.get(cat, 0.0) + amt
        elif ttype == "ALIM":
            m_buy += amt
        elif ttype == "SATIM":
            m_sell += amt

    pending_buy_month = sum(
        abs(float(po.get("amount_try") or 0.0))
        for po in pending_orders
        if str(po.get("created_at", ""))[:7] == month_key
    )
    net_invested = round(max(0.0, m_buy + pending_buy_month - m_sell), 2)
    ref_income = m_income if m_income > 0 else 15000.0  # 11K Kira + 4K KYK aylık baz referans
    savings_rate_pct = round((net_invested / m_income) * 100.0, 1) if m_income > 0 else 0.0
    target_invest_try = round(ref_income * 0.35, 2)
    spending_cap_try = round(ref_income * 0.65, 2)
    remaining_free_try = round(max(0.0, spending_cap_try - m_expense), 2)
    daily_allowance_try = round(remaining_free_try / days_left, 2)
    top_cats = [
        {"category": k, "amount_try": round(v, 2)}
        for k, v in sorted(cat_spend.items(), key=lambda x: -x[1])[:4]
    ]

    # 5. KYK Kaldıraç & Borç Karşılama Sayacı
    kyk_cfg = _load_kyk_helper_cfg()
    prior_kyk = kyk_cfg["prior_kyk_try"]
    monthly_kyk = kyk_cfg["monthly_kyk_try"]
    total_kyk_received = round(recorded_kyk_try + prior_kyk, 2)
    projected_total_kyk = round(8000.0 + max(0, kyk_cfg["total_months_plan"] - 2) * monthly_kyk, 2)

    cash_val = sum(max(0.0, float(a.get("balance") or 0.0)) for a in accounts.values())
    ppf_val = 0.0
    funds_val = 0.0
    for fcode, fobj in db_funds.items():
        sh = float(fobj.get("shares") or 0.0)
        lp = float(funds_m.get(fcode, {}).get("price") or fobj.get("avg_cost") or 0.0)
        val = sh * lp
        funds_val += val
        if fcode in ("ZBJ", "TZL"):
            ppf_val += val
    pending_ppf = sum(float(po.get("amount_try") or 0.0) for po in pending_orders if str(po.get("code", "")).upper() in ("ZBJ", "TZL"))
    pending_all = sum(float(po.get("amount_try") or 0.0) for po in pending_orders)
    total_wealth = round(cash_val + funds_val + pending_all, 2)

    ppf_rate = market_service.get_live_ppf_daily_rate()
    monthly_ppf_nema = round((ppf_val + pending_ppf) * ppf_rate * 30.5, 2)
    nema_installment_cover_pct = round((monthly_ppf_nema / max(1.0, monthly_kyk)) * 100.0, 1)
    target_ppf_for_free_kyk = round(monthly_kyk / max(1e-6, ppf_rate * 30.5), 2)
    debt_covered_pct = round((total_wealth / total_kyk_received) * 100.0, 1) if total_kyk_received > 0 else 100.0

    return {
        "status": "ok",
        "now_tr": now_str,
        "valor_clock": {
            "is_business_day": is_biz,
            "cutoff_state": cutoff_state,
            "seconds_to_cutoff": seconds_to_cutoff,
            "schedules": schedules,
        },
        "monthly_budget": {
            "month_key": month_key,
            "month_label": month_label,
            "day_of_month": now_dt.day,
            "days_in_month": days_in_month,
            "days_left": days_left,
            "has_recorded_income": m_income > 0,
            "income_try": round(m_income, 2),
            "ref_income_try": round(ref_income, 2),
            "expense_try": round(m_expense, 2),
            "net_invested_try": net_invested,
            "savings_rate_pct": savings_rate_pct,
            "target_savings_pct": 35.0,
            "target_invest_try": target_invest_try,
            "spending_cap_try": spending_cap_try,
            "remaining_free_try": remaining_free_try,
            "daily_allowance_try": daily_allowance_try,
            "top_categories": top_cats,
        },
        "kyk_leverage": {
            "config": kyk_cfg,
            "recorded_kyk_try": round(recorded_kyk_try, 2),
            "recorded_kyk_count": recorded_kyk_count,
            "total_kyk_received_try": total_kyk_received,
            "projected_4yr_kyk_try": projected_total_kyk,
            "total_wealth_try": total_wealth,
            "ppf_val_try": round(ppf_val + pending_ppf, 2),
            "monthly_ppf_nema_try": monthly_ppf_nema,
            "nema_installment_cover_pct": nema_installment_cover_pct,
            "target_ppf_for_free_kyk_try": target_ppf_for_free_kyk,
            "debt_covered_pct": debt_covered_pct,
        },
    }


@app.get("/manifest.json")
def pwa_manifest():
    return JSONResponse({
        "name": "Finans Paneli",
        "short_name": "Finans",
        "description": "Canlı TEFAS Portföy, Monte Carlo & Defter Terminali",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#07090f",
        "theme_color": "#07090f",
        "icons": [
            {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
        ],
    })


@app.get("/icon.svg")
def pwa_icon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
        '<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0%" stop-color="#0b1120"/><stop offset="100%" stop-color="#060913"/></linearGradient>'
        '<linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0%" stop-color="#818cf8"/><stop offset="100%" stop-color="#10b981"/></linearGradient></defs>'
        '<rect width="128" height="128" rx="28" fill="url(#bg)"/>'
        '<rect x="6" y="6" width="116" height="116" rx="24" fill="none" stroke="rgba(129,140,248,0.35)" stroke-width="3"/>'
        '<path d="M28 84 L52 60 L70 72 L98 38" fill="none" stroke="url(#lg)" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M84 38 H98 V52" fill="none" stroke="#10b981" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/sw.js")
def pwa_sw():
    js = (
        "self.addEventListener('install', (e) => self.skipWaiting());\n"
        "self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));\n"
        "self.addEventListener('fetch', (e) => {\n"
        "  if (e.request.method !== 'GET' || e.request.url.includes('/api/')) return;\n"
        "  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));\n"
        "});\n"
    )
    return Response(content=js, media_type="application/javascript")

@app.get("/api/quant/radar")
def get_quant_radar_api(budget_try: float = 6000.0, stress_mode: str = "live", refresh: bool = False):
    return quant_engine.build_quant_radar(
        budget_try=budget_try,
        stress_mode=stress_mode,
        force_refresh=refresh,
    )

@app.get("/api/alpha/scan")
def get_alpha_scan_api(refresh: bool = False):
    return alpha_hunter.scan_alpha_universe(force_refresh=refresh)

@app.get("/api/alpha/asset/{symbol}")
def get_alpha_asset_api(symbol: str):
    res = alpha_hunter.analyze_single_asset(symbol)
    if not res:
        raise HTTPException(status_code=404, detail=f"{symbol} için yeterli geçmiş veri bulunamadı.")
    return res

@app.get("/api/tefas/analyze/{code}")
def get_tefas_fund_analysis_api(code: str, refresh: bool = False):
    res = tefas_analyzer.analyze_tefas_fund(code, force_refresh=refresh)
    if not res:
        raise HTTPException(status_code=404, detail=f"{code.upper()} kodlu TEFAS fonu bulunamadı.")
    return res

@app.get("/api/tefas/switch-sim")
def get_tefas_switch_sim_api(
    from_code: str,
    to_code: str,
    amount_try: Optional[float] = None,
    valor_days: Optional[int] = None,
):
    res = tefas_analyzer.simulate_fund_switch(from_code, to_code, amount_try=amount_try, valor_days=valor_days)
    if not res:
        raise HTTPException(status_code=400, detail="Fon değişimi hesaplanamadı (aynı fon veya eksik TEFAS verisi).")
    return res


def _build_opportunity_item(
    code: str,
    m_fund: Dict[str, Any],
    q_fund: Dict[str, Any],
    macro: Dict[str, Any],
    ppf_rate: float,
    owned_shares: float = 0.0,
    pending_amt: float = 0.0,
) -> Dict[str, Any]:
    name = m_fund.get("name") or code
    category = str(m_fund.get("category") or "")
    cat_up = f"{code} {name} {category}".upper()
    price = float(m_fund.get("price") or 0.0)
    chg = float(m_fund.get("daily_yield_pct") or 0.0)
    w_pct = float(m_fund.get("weekly_yield_pct") or 0.0)
    m_pct = float(m_fund.get("monthly_yield_pct") or 0.0)
    dd = float(m_fund.get("drawdown_from_high_pct") or 0.0)
    ndx_chg = float(macro.get("nasdaq_chg_pct") or 0.0)

    reg = (q_fund or {}).get("regime") or {}
    h_score = reg.get("health_score")
    is_knife = reg.get("state") == "FALLING_KNIFE"
    is_owned = (owned_shares > 0) or (pending_amt > 0)

    is_ppf = code in ("ZBJ", "TZL") or ("PARA PİYASASI" in cat_up) or ("PARA PIYASASI" in cat_up) or ("LİKİT" in cat_up)
    is_gold = code in ("TCA", "KZL") or ("ALTIN" in cat_up) or ("KIYMETLİ MADEN" in cat_up)

    if is_ppf:
        daily_rate_pct = float(m_fund.get("avg_daily_nema_pct") or chg or (ppf_rate * 100.0))
        signal = f"Güvenli Kazanç (~%{daily_rate_pct:.2f}/gün)"
        badge_color = "cyan"
        regime_level = "GREEN"
        h_score = h_score if h_score is not None else 96
        advice = (
            f"Son 30 günde +%{m_pct:.2f} düzenli nema getirisi; piyasa dalgalı olduğunda paranın güvenli kalesi."
            if code == "ZBJ"
            else f"Son 30 günde +%{m_pct:.2f} likit getiri; gün içinde ihtiyaç anında hızlıca nakde döner."
        )
    elif is_gold:
        is_dip = (not is_knife) and (chg < -0.15 or dd <= -1.5)
        signal = (
            f"Zayıf Trend ({h_score or 20}/100)" if is_knife
            else ("Uygun Alım (İndirimli)" if is_dip else (reg.get("badge") or "Dengeli Bölge"))
        )
        badge_color = "rose" if is_knife else ("emerald" if is_dip else "slate")
        regime_level = reg.get("level", "GREEN")
        advice = (
            f"Altın 50G ortalamanın altında zayıf seyrediyor (Sağlık {h_score}/100); acele etmeden kademeli savunma eklemesi yap."
            if is_knife
            else (
                f"Altın 30G zirvesinden %{abs(dd):.1f} geride (7G {w_pct:+.2f}%); makassız altın eklemek için elverişli seviye."
                if is_dip
                else f"Altın sakin seyrediyor (7G {w_pct:+.2f}%, zirveye göre {dd:+.1f}%)."
            )
        )
    else:
        is_dip = (not is_knife) and (chg <= -0.8 or dd <= -2.5 or ("AFT" in code and ndx_chg <= -1.0))
        signal = (
            f"Bekle & İzle ({h_score or 15}/100)" if is_knife
            else ("Dipten Alım Fırsatı" if is_dip else ("Hızlı Yükseliş / Bekle" if chg >= 1.5 else (reg.get("badge") or "Dengeli Bölge")))
        )
        badge_color = "rose" if is_knife else ("amber" if is_dip else ("purple" if chg >= 1.5 else "slate"))
        regime_level = reg.get("level", "GREEN")
        advice = (
            f"Trend aşağı yönlü (Sağlık {h_score}/100, 30G zirveden %{abs(dd):.1f} geride). Yeni alımı durdurup dönüş sinyali beklemek daha güvenli."
            if is_knife
            else (
                f"30G zirveden %{abs(dd):.1f} aşağıda (7G {w_pct:+.2f}%); ana trend sağlamken kademeli eklenebilir."
                if is_dip
                else (reg.get("action_short") or f"30G getiri {m_pct:+.2f}% • 7G {w_pct:+.2f}%")
            )
        )

    return {
        "code": code,
        "name": name,
        "category": category or "Yatırım Fonu",
        "price": price,
        "change_pct": chg,
        "weekly_pct": w_pct,
        "monthly_pct": m_pct,
        "drawdown_pct": dd,
        "health_score": h_score,
        "regime_level": regime_level,
        "signal": signal,
        "badge_color": badge_color,
        "advice": advice,
        "is_owned": is_owned,
        "shares": owned_shares,
    }


@app.get("/api/market/opportunity-radar")
def get_opportunity_radar_api():
    market = fetch_live_market()
    funds = market.get("funds", {})
    macro = market.get("macro", {})
    ppf_rate = market_service.get_live_ppf_daily_rate()
    db_funds = db.get_all_funds() or {}
    pending_orders = db.get_pending_orders("BEKLIYOR") or []

    pending_by_code: Dict[str, float] = {}
    for po in pending_orders:
        pc = str(po.get("code") or "").upper().strip()
        if pc:
            pending_by_code[pc] = pending_by_code.get(pc, 0.0) + float(po.get("amount_try") or 0.0)

    q_funds: Dict[str, Any] = {}
    q_overall: Dict[str, Any] = {}
    try:
        q_snap = quant_engine.build_quant_radar(budget_try=6000.0, stress_mode="live", force_refresh=False)
        q_funds = q_snap.get("funds", {})
        q_overall = q_snap.get("overall_regime", {})
    except Exception:
        pass

    # Önce kullanıcının elindeki/valördeki fonları, ardından tüm izlenen TEFAS fonlarını dinamik sırala
    preferred_order = ["TCA", "AFT", "TI3", "ZBJ", "TZL", "KZL"]
    all_codes_raw = (
        [c for c, v in db_funds.items() if float((v or {}).get("shares", 0.0)) > 0]
        + list(pending_by_code.keys())
        + preferred_order
        + list(funds.keys())
    )
    ordered_codes: List[str] = []
    seen_codes = set()
    for c in all_codes_raw:
        cu = str(c or "").upper().strip()
        if cu and cu != "TRY" and cu not in seen_codes and (cu in funds or cu in db_funds):
            seen_codes.add(cu)
            ordered_codes.append(cu)

    items = []
    for code in ordered_codes:
        m_fund = funds.get(code) or {}
        if not m_fund and code in db_funds:
            m_fund = {
                "name": db_funds[code].get("name", code),
                "price": db_funds[code].get("buy_price", 1.0),
                "category": "Yatırım Fonu",
            }
        owned_sh = float((db_funds.get(code) or {}).get("shares", 0.0))
        items.append(
            _build_opportunity_item(
                code=code,
                m_fund=m_fund,
                q_fund=q_funds.get(code, {}),
                macro=macro,
                ppf_rate=ppf_rate,
                owned_shares=owned_sh,
                pending_amt=pending_by_code.get(code, 0.0),
            )
        )

    return {
        "status": "ok",
        "updated_at": market.get("updated_at", ""),
        "data_quality": market.get("data_quality", {}),
        "overall_regime": q_overall,
        "macro": macro,
        "items": items,
    }

# ======================================================================
# BRUTE-FORCE KORUMA MOTORU (Kalıcı IP Ban + Global Kalkan & Telegram Alarmı)
# ======================================================================
FAILED_LOGIN_ATTEMPTS: Dict[str, List[float]] = {}
BANNED_IPS: Dict[str, float] = {}
GLOBAL_FAILED_LOGINS: List[float] = []
GLOBAL_LOCK_UNTIL: float = 0.0
KNOWN_LOGIN_IPS: set = set()
_AUTH_STATE_LOADED: bool = False
_AUTH_LOCK = threading.Lock()
_WEAK_PINS = {"0000", "000000", "1111", "111111", "1234", "123456", "654321", "999999"}

def _load_persisted_auth_state():
    global _AUTH_STATE_LOADED
    if _AUTH_STATE_LOADED:
        return
    _AUTH_STATE_LOADED = True
    try:
        raw = db.get_setting("auth_security_state")
        if raw:
            data = json.loads(raw)
            now = time.time()
            for ip, until in (data.get("banned_ips") or {}).items():
                if float(until) > now:
                    BANNED_IPS[ip] = float(until)
            for ip in (data.get("known_ips") or [])[-30:]:
                KNOWN_LOGIN_IPS.add(str(ip))
    except Exception:
        pass

def _save_persisted_auth_state():
    try:
        now = time.time()
        payload = {
            "banned_ips": {ip: u for ip, u in BANNED_IPS.items() if u > now},
            "known_ips": list(KNOWN_LOGIN_IPS)[-30:],
        }
        db.set_setting("auth_security_state", json.dumps(payload), sync_cloud=False)
    except Exception:
        pass

def _prune_auth_buckets(now: float):
    global GLOBAL_FAILED_LOGINS
    GLOBAL_FAILED_LOGINS = [t for t in GLOBAL_FAILED_LOGINS if now - t < 600.0]
    if len(BANNED_IPS) > 100 or len(FAILED_LOGIN_ATTEMPTS) > 200:
        expired_bans = [ip for ip, until in BANNED_IPS.items() if now >= until]
        for ip in expired_bans:
            del BANNED_IPS[ip]
        stale_attempts = [
            ip for ip, ts_list in FAILED_LOGIN_ATTEMPTS.items()
            if not any(now - t < 900.0 for t in ts_list)
        ]
        for ip in stale_attempts:
            del FAILED_LOGIN_ATTEMPTS[ip]

@app.get("/api/auth/status")
def get_auth_status(request: Request):
    is_auth = is_request_authenticated(request)
    return {"authenticated": is_auth, "auth_enabled": True}

@app.post("/api/auth/login")
def post_auth_login(req: dict, request: Request, response: Response):
    global GLOBAL_LOCK_UNTIL
    client_ip = get_client_ip(request)
    now = time.time()
    ua = (request.headers.get("User-Agent") or "Bilinmeyen Cihaz")[:80]

    with _AUTH_LOCK:
        _load_persisted_auth_state()
        _prune_auth_buckets(now)
        if now < GLOBAL_LOCK_UNTIL:
            rem_min = int((GLOBAL_LOCK_UNTIL - now) / 60) + 1
            return JSONResponse(
                status_code=429,
                content={"status": "error", "message": f"Yoğun hatalı giriş tespit edildi. Sistem koruması {rem_min} dakika aktif."}
            )
        # 1. IP Ban Kontrolü
        if client_ip in BANNED_IPS:
            ban_until = BANNED_IPS[client_ip]
            if now < ban_until:
                rem_min = int((ban_until - now) / 60) + 1
                return JSONResponse(
                    status_code=429,
                    content={"status": "error", "message": f"Çok fazla hatalı deneme yapıldı. Güvenlik nedeniyle IP adresiniz {rem_min} dakika kilitlendi."}
                )
            else:
                del BANNED_IPS[client_ip]
                FAILED_LOGIN_ATTEMPTS.pop(client_ip, None)

    pin = str(req.get("pin", "")).strip()
    cfg = load_security_config()
    if not (os.environ.get("MASTER_PIN", "").strip() or cfg.get("pin_hash") or cfg.get("master_pin")):
        return JSONResponse(status_code=500, content={"status": "error", "message": "Güvenlik yapılandırması (MASTER_PIN) eksik."})
    
    # Sabit zamanlı güvenli karşılaştırma (PBKDF2 / Timing Attack Koruması)
    if verify_pin_value(pin, cfg):
        is_new_ip = False
        with _AUTH_LOCK:
            FAILED_LOGIN_ATTEMPTS.pop(client_ip, None)
            if client_ip not in KNOWN_LOGIN_IPS and client_ip not in ("127.0.0.1", "::1", "localhost"):
                KNOWN_LOGIN_IPS.add(client_ip)
                _save_persisted_auth_state()
                is_new_ip = True
        if is_new_ip:
            try:
                telegram_bot.send_message(
                    f"🔐 *YENİ CİHAZ / IP GİRİŞİ*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 *IP:* `{client_ip}`\n"
                    f"📱 *Cihaz:* `{ua}`\n"
                    f"🕒 *Saat:* {now_tr().strftime('%d.%m.%Y %H:%M')}\n"
                    f"_Bu sen değilsen terminalden 'Tüm Oturumları Kapat' yap ve PIN değiştir._"
                )
            except Exception:
                pass
        secret = cfg.get("session_secret", "")
        ver = int(cfg.get("session_version", 1))
        token = create_session_token(secret, ver)
        response.set_cookie(
            key="finans_session",
            value=token,
            max_age=_SESSION_MAX_AGE_S,
            httponly=True,
            samesite="strict",
            path="/",
            secure=is_https_request(request)
        )
        return {"status": "ok", "message": "Giriş başarılı! Terminal kilidi açıldı."}
    else:
        trigger_ip_ban = False
        trigger_global_ban = False
        with _AUTH_LOCK:
            attempts = [t for t in FAILED_LOGIN_ATTEMPTS.get(client_ip, []) if now - t < 900.0]
            attempts.append(now)
            FAILED_LOGIN_ATTEMPTS[client_ip] = attempts
            attempt_count = len(attempts)

            GLOBAL_FAILED_LOGINS.append(now)
            if len(GLOBAL_FAILED_LOGINS) >= 15:
                GLOBAL_LOCK_UNTIL = now + (15 * 60)
                trigger_global_ban = True

            if attempt_count >= 5:
                ban_mins = 1440 if attempt_count >= 8 else 30
                BANNED_IPS[client_ip] = now + (ban_mins * 60)
                _save_persisted_auth_state()
                trigger_ip_ban = True

        if trigger_global_ban:
            try:
                telegram_bot.send_message(
                    f"🚨 *KRİTİK GÜVENLİK ALARMI // DAĞITIK BRUTE FORCE*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ 10 dakika içinde 15+ başarısız giriş denemesi!\n"
                    f"🛡 Terminal girişleri tüm IP'ler için 15 dakika kilitlendi."
                )
            except Exception:
                pass
            return JSONResponse(
                status_code=429,
                content={"status": "error", "message": "Yoğun erişim denemesi tespit edildi. Girişler 15 dakika boyunca kilitlendi."}
            )

        if trigger_ip_ban:
            try:
                telegram_bot.send_message(
                    f"🚨 *GÜVENLİK ALARMI // BRUTE FORCE SALDIRISI*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 *Saldırgan IP:* `{client_ip}`\n"
                    f"⚠️ *Olay:* Arka arkaya {attempt_count} kez yanlış şifre denendi!\n"
                    f"🛡 *Eylem:* IP adresi {30 if attempt_count < 8 else 1440} dakika kilitlendi."
                )
            except Exception:
                pass
            return JSONResponse(
                status_code=429,
                content={"status": "error", "message": "Çok fazla hatalı şifre girildi. IP adresiniz geçici olarak kilitlendi."}
            )
        else:
            remaining = 5 - attempt_count
            return JSONResponse(
                status_code=401,
                content={"status": "error", "message": f"Hatalı şifre! Kalan deneme hakkınız: {remaining}"}
            )

@app.post("/api/auth/logout")
def post_auth_logout(response: Response):
    response.delete_cookie("finans_session", path="/")
    return {"status": "ok", "message": "Güvenli çıkış yapıldı. Terminal kilitlendi."}

@app.post("/api/auth/revoke-all")
def post_auth_revoke_all(request: Request, response: Response):
    if not is_request_authenticated(request):
        return JSONResponse(status_code=401, content={"status": "error", "message": "Yetkisiz işlem."})
    cfg = load_security_config()
    cfg["session_version"] = int(cfg.get("session_version", 1)) + 1
    if not os.environ.get("SESSION_SECRET", "").strip():
        cfg["session_secret"] = secrets.token_hex(32)
    save_security_config(cfg)
    response.delete_cookie("finans_session", path="/")
    try:
        telegram_bot.send_message(
            f"🛑 *TÜM OTURUMLAR SONLANDIRILDI*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 {now_tr().strftime('%d.%m.%Y %H:%M')} tarihinde açık tüm cihazlardaki oturumlar geçersiz kılındı."
        )
    except Exception:
        pass
    return {"status": "ok", "message": "Tüm cihazlardaki aktif oturumlar anında sonlandırıldı!"}

@app.post("/api/auth/change-pin")
def post_auth_change_pin(req: dict, request: Request, response: Response):
    if not is_request_authenticated(request):
        return JSONResponse(status_code=401, content={"status": "error", "message": "Yetkisiz işlem."})

    if os.environ.get("MASTER_PIN", "").strip():
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Sunucuda MASTER_PIN ortam değişkeni aktif. Şifreyi Render Panel > Environment üzerinden değiştirmelisiniz."}
        )
    
    old_pin = str(req.get("old_pin", "")).strip()
    new_pin = str(req.get("new_pin", "")).strip()
    
    if len(new_pin) < 6:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Yeni şifre güvenlik için en az 6 karakter olmalıdır."})
    if new_pin in _WEAK_PINS or len(set(new_pin)) <= 1:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Çok basit/tahmin edilebilir bir şifre seçilemez."})
        
    cfg = load_security_config()
    if not verify_pin_value(old_pin, cfg):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Eski şifre hatalı!"})
        
    cfg["pin_hash"] = hash_pin_value(new_pin)
    cfg.pop("master_pin", None)
    cfg["session_version"] = int(cfg.get("session_version", 1)) + 1
    if not os.environ.get("SESSION_SECRET", "").strip():
        cfg["session_secret"] = secrets.token_hex(32)
    save_security_config(cfg)

    new_token = create_session_token(cfg["session_secret"], int(cfg["session_version"]))
    response.set_cookie(
        key="finans_session",
        value=new_token,
        max_age=_SESSION_MAX_AGE_S,
        httponly=True,
        samesite="strict",
        path="/",
        secure=is_https_request(request)
    )
    try:
        telegram_bot.send_message(
            f"🔐 *GÜVENLİK BİLDİRİMİ*\nMaster terminal şifresi {now_tr().strftime('%d.%m.%Y %H:%M')} tarihinde güncellendi ve diğer cihaz oturumları kapatıldı."
        )
    except Exception:
        pass
    return {"status": "ok", "message": "Şifre hashlenerek güncellendi ve diğer cihazlardaki oturumlar sonlandırıldı!"}

@app.post("/api/telegram/trigger-alert")
def trigger_telegram_alert(req: dict):
    alert_type = req.get("type", "friday")
    if alert_type == "friday":
        msg = telegram_bot.build_friday_alert_text()
    elif alert_type == "night":
        msg = telegram_bot.build_night_nema_text()
    elif alert_type == "opportunity":
        msg = telegram_bot.build_opportunity_radar_text()
    elif alert_type == "quant":
        msg = telegram_bot.build_quant_regime_alert_text()
    elif alert_type == "alpha":
        msg = telegram_bot.build_alpha_hunter_text(force_refresh=True)
    elif alert_type == "weekly":
        msg = telegram_bot.build_weekly_summary_text()
    else:
        return {"status": "error", "message": "Geçersiz alarm türü."}
        
    ok = telegram_bot.send_message(msg)
    if ok:
        return {"status": "ok", "message": f"{alert_type.capitalize()} alarmı Telegram botuna iletildi!"}
    else:
        return {"status": "error", "message": "Telegram mesajı iletilemedi. Bot bağlı mı?"}

@app.get("/api/ai/status")
def get_ai_status():
    return {
        "status": "ok",
        "info": ai_advisor.get_ai_status_text(),
        "context": ai_advisor.get_portfolio_context()
    }

@app.post("/api/ai/save-key")
def save_ai_key(req: dict):
    key = req.get("key", "").strip()
    msg = ai_advisor.save_ai_key_from_telegram(key)
    return {"status": "ok", "message": msg}

@app.post("/api/shutdown")
def shutdown_server():
    if os.environ.get("RENDER"):
        raise HTTPException(status_code=403, detail="Render canlı sunucusu web üzerinden kapatılamaz.")

    telegram_bot.stop_bot_worker()

    def kill():
        time.sleep(0.5)
        os._exit(0)
        
    threading.Thread(target=kill, daemon=True).start()
    return {"status": "ok", "message": "Finans sunucusu güvenle kapatıldı. Arka planda hiçbir işlem çalışmıyor."}

@app.get("/api/bootstrap")
def get_bootstrap_bundle(request: Request, days: int = 30, refresh: bool = False):
    """Tek HTTP isteğinde ana terminal verilerini toplu döndürür."""
    if refresh:
        market_service.refresh_live_market_now()
    market = fetch_live_market()
    portfolio = get_portfolio(refresh=False)
    try:
        tx_res = get_transactions()
    except Exception:
        tx_res = {"transactions": []}
    try:
        history = get_portfolio_history(days=days)
    except Exception:
        history = {"status": "error", "points": []}
    try:
        opp_radar = get_opportunity_radar_api()
    except Exception:
        opp_radar = {"items": []}
    try:
        persistence = get_system_persistence(request)
    except Exception:
        persistence = {"boot_id": _SERVER_BOOT_ID}
    try:
        preferences = get_ui_preferences().get("preferences", {})
    except Exception:
        preferences = {}
    alpha_cached = alpha_hunter._load_disk_cache() or {}
    return {
        "status": "ok",
        "boot_id": _SERVER_BOOT_ID,
        "market": market,
        "portfolio": portfolio,
        "transactions": tx_res.get("transactions", []),
        "history": history,
        "opportunity_radar": opp_radar,
        "alpha_summary": {
            "universe_count": alpha_cached.get("universe_count", 0),
            "macro_regime": alpha_cached.get("macro_regime", "GREEN"),
            "opportunities": (alpha_cached.get("opportunities") or [])[:3],
            "falling_knives": (alpha_cached.get("falling_knives") or [])[:4],
        },
        "persistence": persistence,
        "preferences": preferences,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    template_path = os.path.join(DATA_DIR, "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("__ASSET_V__", _get_asset_version())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False, ws="none")
