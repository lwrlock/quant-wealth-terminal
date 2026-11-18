import os
import sqlite3
import json
import uuid
import time
import threading
import atexit
import datetime
from typing import Dict, Any, List, Optional

try:
    import requests
except ImportError:
    requests = None

try:
    from market_service import now_tr
except ImportError:
    def now_tr() -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).replace(tzinfo=None)

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DATA_DIR, "finans.db")
HOLDINGS_FILE = os.path.join(DATA_DIR, "holdings.json")
TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.json")

_CLOUD_LOCK = threading.Lock()
_CLOUD_TIMER: Optional[threading.Timer] = None
_CLOUD_DIRTY: bool = False
_CLOUD_STATUS: Dict[str, Any] = {
    "last_sync_at": None,
    "last_restore_at": None,
    "last_error": None,
}
_LAST_SNAPSHOT_CACHE: Dict[str, Any] = {"date": None, "total": None, "ts": 0.0}


def normalize_account_id(acc_id: Optional[str], default: str = "5001") -> str:
    val = (acc_id or default).strip()
    if val == "5003":
        return "5004"
    return val or default


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA cache_size=-8000;")
    except Exception:
        pass
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    balance REAL NOT NULL DEFAULT 0.0,
                    bank TEXT NOT NULL DEFAULT 'Banka'
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS funds (
                    code TEXT PRIMARY KEY,
                    shares REAL NOT NULL DEFAULT 0.0,
                    buy_price REAL NOT NULL DEFAULT 0.0,
                    name TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount_try REAL NOT NULL,
                    shares REAL NOT NULL DEFAULT 0.0,
                    price REAL NOT NULL DEFAULT 0.0,
                    code TEXT NOT NULL DEFAULT 'TRY',
                    account_id TEXT NOT NULL DEFAULT '5001',
                    to_account_id TEXT,
                    description TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date DESC);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_updates (
                    update_id INTEGER PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS net_worth_history (
                    date TEXT PRIMARY KEY,
                    total_try REAL NOT NULL,
                    cash_try REAL NOT NULL DEFAULT 0.0,
                    funds_try REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recurring_rules (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'GİRİŞ',
                    category TEXT NOT NULL DEFAULT 'Sabit Akış',
                    amount_try REAL NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '5001',
                    day_of_month INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_triggered_month TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    threshold REAL NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_triggered_at TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_orders (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    fund_name TEXT NOT NULL DEFAULT '',
                    order_type TEXT NOT NULL DEFAULT 'ALIM',
                    amount_try REAL NOT NULL,
                    estimated_shares REAL NOT NULL DEFAULT 0.0,
                    estimated_price REAL NOT NULL DEFAULT 0.0,
                    account_id TEXT NOT NULL DEFAULT '5004',
                    status TEXT NOT NULL DEFAULT 'BEKLIYOR',
                    expected_settlement TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    settled_at TEXT
                );
            """)

            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM accounts;")
            if cur.fetchone()[0] == 0:
                accounts_data = {}
                funds_data = {}
                if os.path.exists(HOLDINGS_FILE):
                    try:
                        with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
                            h = json.load(f)
                            accounts_data = h.get("accounts", {})
                            funds_data = h.get("funds", {})
                    except Exception:
                        pass

                if not accounts_data:
                    accounts_data = {
                        "5001": {"id": "5001", "name": "Ana Banka Hesabı (Vadesiz)", "balance": 12500.0, "bank": "Demo Bank"},
                        "5004": {"id": "5004", "name": "Yatırım Hesabı (Vadesiz)", "balance": 4200.0, "bank": "Demo Bank"},
                        "5002": {"id": "5002", "name": "Acil Durum Yedeği", "balance": 5000.0, "bank": "Demo Bank"},
                        "cuzdan": {"id": "cuzdan", "name": "Fiziki Nakit / Cüzdan", "balance": 1800.0, "bank": "Nakit"},
                    }
                for acc in accounts_data.values():
                    conn.execute(
                        "INSERT OR REPLACE INTO accounts (id, name, balance, bank) VALUES (?, ?, ?, ?)",
                        (acc.get("id"), acc.get("name"), float(acc.get("balance", 0.0)), acc.get("bank", "Demo Bank")),
                    )

                if not funds_data:
                    funds_data = {
                        "ZBJ": {"shares": 3500.0, "buy_price": 5.45, "name": "Ziraat Başak Para Piyasası Fonu"},
                        "TZL": {"shares": 80000.0, "buy_price": 0.145, "name": "Ziraat Para Piyasası Fonu"},
                        "TCA": {"shares": 15000.0, "buy_price": 0.710, "name": "Ziraat Altın Katılım Fonu"},
                        "AFT": {"shares": 10000.0, "buy_price": 0.820, "name": "Ak Portföy Yeni Teknolojiler Fonu"},
                    }
                for code, f_item in funds_data.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO funds (code, shares, buy_price, name) VALUES (?, ?, ?, ?)",
                        (code, float(f_item.get("shares", 0.0)), float(f_item.get("buy_price", 0.0)), f_item.get("name", code)),
                    )

            cur.execute("SELECT COUNT(*) FROM transactions;")
            if cur.fetchone()[0] == 0:
                txs_data = []
                if os.path.exists(TRANSACTIONS_FILE):
                    try:
                        with open(TRANSACTIONS_FILE, "r", encoding="utf-8") as f:
                            txs_data = json.load(f)
                    except Exception:
                        txs_data = []
                if not txs_data:
                    today_s = now_tr().strftime("%Y-%m-%d %H:%M:%S")
                    txs_data = [
                        {"id": "tx-demo-01", "date": today_s, "type": "GİRİŞ", "category": "Başlangıç", "amount_try": 12500.0, "shares": 0.0, "price": 0.0, "code": "TRY", "account_id": "5001", "description": "Demo Ana Banka Bakiyesi"},
                        {"id": "tx-demo-02", "date": today_s, "type": "ALIM", "category": "Para Piyasası", "amount_try": 19075.0, "shares": 3500.0, "price": 5.45, "code": "ZBJ", "account_id": "5004", "description": "Demo ZBJ PPF Alımı"},
                        {"id": "tx-demo-03", "date": today_s, "type": "ALIM", "category": "Altın Fonu", "amount_try": 10650.0, "shares": 15000.0, "price": 0.71, "code": "TCA", "account_id": "5004", "description": "Demo TCA Altın Fonu Alımı"},
                        {"id": "tx-demo-04", "date": today_s, "type": "ALIM", "category": "Teknoloji", "amount_try": 8200.0, "shares": 10000.0, "price": 0.82, "code": "AFT", "account_id": "5004", "description": "Demo AFT Teknoloji Fonu Alımı"},
                    ]
                for t in txs_data:
                    conn.execute(
                        """INSERT OR REPLACE INTO transactions
                        (id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            t.get("id", f"tx-{uuid.uuid4().hex[:8]}"),
                            t.get("date", now_tr().strftime("%Y-%m-%d %H:%M:%S")),
                            t.get("type", "GİRİŞ"),
                            t.get("category", "İşlem"),
                            float(t.get("amount_try", 0.0)),
                            float(t.get("shares", 0.0)),
                            float(t.get("price", 0.0)),
                            t.get("code", "TRY"),
                            normalize_account_id(t.get("account_id"), "5001"),
                            normalize_account_id(t.get("to_account_id")) if t.get("to_account_id") else None,
                            t.get("description", ""),
                        ),
                    )

            conn.execute("DELETE FROM accounts WHERE id = '5003';")
    finally:
        conn.close()


init_db()


def get_all_accounts() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, balance, bank FROM accounts ORDER BY id ASC;")
        rows = cur.fetchall()
        return {row["id"]: dict(row) for row in rows}
    finally:
        conn.close()


def update_account(acc_id: str, balance: float, name: Optional[str] = None) -> Dict[str, Any]:
    acc_id = normalize_account_id(acc_id, "5001")
    conn = get_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, balance, bank FROM accounts WHERE id = ?;", (acc_id,))
            existing = cur.fetchone()
            if existing:
                new_name = name.strip() if name and name.strip() else existing["name"]
                conn.execute(
                    "UPDATE accounts SET balance = ?, name = ? WHERE id = ?;",
                    (round(float(balance), 2), new_name, acc_id),
                )
            else:
                new_name = name.strip() if name and name.strip() else f"Hesap {acc_id}"
                conn.execute(
                    "INSERT INTO accounts (id, name, balance, bank) VALUES (?, ?, ?, ?);",
                    (acc_id, new_name, round(float(balance), 2), "Banka/Kasa"),
                )
            cur.execute("SELECT id, name, balance, bank FROM accounts WHERE id = ?;", (acc_id,))
            res = dict(cur.fetchone())
    finally:
        conn.close()
    schedule_cloud_sync()
    return res


def delete_account(acc_id: str) -> bool:
    conn = get_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM accounts WHERE id = ?;", (acc_id,))
            deleted = cur.rowcount > 0
    finally:
        conn.close()
    if deleted:
        schedule_cloud_sync()
    return deleted


def get_all_funds() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT code, shares, buy_price, name FROM funds ORDER BY code ASC;")
        rows = cur.fetchall()
        return {row["code"]: dict(row) for row in rows}
    finally:
        conn.close()


def mark_update_processed(update_id: int) -> bool:
    """Telegram update_id değerini atomik olarak kaydeder; daha önce işlendiyse False döner."""
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO processed_updates (update_id, processed_at) VALUES (?, ?);",
                (int(update_id), now_tr().strftime("%Y-%m-%d %H:%M:%S")),
            )
            inserted = cur.rowcount > 0
            if inserted:
                conn.execute(
                    "DELETE FROM processed_updates WHERE update_id NOT IN (SELECT update_id FROM processed_updates ORDER BY update_id DESC LIMIT 500);"
                )
            return inserted
    finally:
        conn.close()


def update_fund(
    code: str,
    shares: float,
    buy_price: Optional[float] = None,
    name: Optional[str] = None,
    weighted_avg: bool = False,
):
    code = code.upper().strip()
    new_sh = round(max(0.0, float(shares)), 4)
    conn = get_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT code, shares, buy_price, name FROM funds WHERE code = ?;", (code,))
            existing = cur.fetchone()
            if existing:
                old_sh = float(existing["shares"] or 0.0)
                old_bp = float(existing["buy_price"] or 0.0)
                if buy_price is None:
                    new_bp = old_bp
                elif weighted_avg and new_sh > old_sh > 0:
                    added_sh = new_sh - old_sh
                    new_bp = round(((old_sh * old_bp) + (added_sh * float(buy_price))) / new_sh, 6)
                elif weighted_avg and old_sh <= 0 and new_sh > 0:
                    new_bp = round(float(buy_price), 6)
                else:
                    new_bp = float(buy_price)
                new_name = name if name else existing["name"]
                conn.execute(
                    "UPDATE funds SET shares = ?, buy_price = ?, name = ? WHERE code = ?;",
                    (new_sh, new_bp, new_name, code),
                )
            else:
                conn.execute(
                    "INSERT INTO funds (code, shares, buy_price, name) VALUES (?, ?, ?, ?);",
                    (code, new_sh, float(buy_price or 1.0), name or code),
                )
    finally:
        conn.close()
    schedule_cloud_sync()


def get_all_transactions() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description
            FROM transactions
            ORDER BY date DESC, rowid DESC;
        """)
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def insert_transaction(tx: Dict[str, Any]):
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO transactions (id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    tx["id"],
                    tx["date"],
                    tx["type"],
                    tx["category"],
                    tx["amount_try"],
                    tx["shares"],
                    tx["price"],
                    tx["code"],
                    normalize_account_id(tx["account_id"], "5001"),
                    normalize_account_id(tx["to_account_id"]) if tx.get("to_account_id") else None,
                    tx["description"],
                ),
            )
    finally:
        conn.close()
    schedule_cloud_sync()


def apply_and_insert_transaction(tx: Dict[str, Any], fund_name: Optional[str] = None, weighted_avg: bool = True):
    """İşlemi deftere ekler ve hesap/fon bakiyelerini tek bir ACID SQLite işleminde uygular."""
    conn = get_connection()
    try:
        with conn:
            _apply_tx_effects_in_conn(conn, tx, fund_name=fund_name, weighted_avg=weighted_avg)
            conn.execute(
                """
                INSERT INTO transactions (id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    tx["id"],
                    tx["date"],
                    tx["type"],
                    tx["category"],
                    tx["amount_try"],
                    tx["shares"],
                    tx["price"],
                    tx["code"],
                    normalize_account_id(tx["account_id"], "5001"),
                    normalize_account_id(tx["to_account_id"]) if tx.get("to_account_id") else None,
                    tx["description"],
                ),
            )
    finally:
        conn.close()
    schedule_cloud_sync()


def _apply_tx_effects_in_conn(
    conn: sqlite3.Connection,
    tx: Dict[str, Any],
    fund_name: Optional[str] = None,
    weighted_avg: bool = True,
):
    t_type = tx.get("type", "").upper().strip()
    amt = float(tx.get("amount_try", 0.0))
    shares = float(tx.get("shares", 0.0))
    price = float(tx.get("price", 0.0))
    code = tx.get("code", "TRY").upper().strip()
    acc_id = normalize_account_id(tx.get("account_id"), "5004" if t_type in ["ALIM", "SATIM"] else "5001")
    to_acc_id = normalize_account_id(tx.get("to_account_id")) if tx.get("to_account_id") else None

    cur = conn.cursor()
    cur.execute("SELECT balance FROM accounts WHERE id = ?;", (acc_id,))
    acc_row = cur.fetchone()
    curr_bal = float(acc_row[0]) if acc_row else 0.0

    if t_type == "GİRİŞ":
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (round(curr_bal + amt, 2), acc_id))
    elif t_type == "ÇIKIŞ":
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (max(0.0, round(curr_bal - amt, 2)), acc_id))
    elif t_type == "ALIM":
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (max(0.0, round(curr_bal - amt, 2)), acc_id))
        cur.execute("SELECT shares, buy_price, name FROM funds WHERE code = ?;", (code,))
        f_row = cur.fetchone()
        if f_row:
            old_sh = float(f_row["shares"] or 0.0)
            old_bp = float(f_row["buy_price"] or 0.0)
            new_sh = round(max(0.0, old_sh + shares), 4)
            if weighted_avg and price > 0 and new_sh > old_sh > 0:
                new_bp = round(((old_sh * old_bp) + (shares * price)) / new_sh, 6)
            elif price > 0 and old_sh <= 0 and new_sh > 0:
                new_bp = round(price, 6)
            else:
                new_bp = old_bp
            conn.execute("UPDATE funds SET shares = ?, buy_price = ? WHERE code = ?;", (new_sh, new_bp, code))
        else:
            conn.execute(
                "INSERT INTO funds (code, shares, buy_price, name) VALUES (?, ?, ?, ?);",
                (code, round(shares, 4), float(price or 1.0), fund_name or code),
            )
    elif t_type == "SATIM":
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (round(curr_bal + amt, 2), acc_id))
        cur.execute("SELECT shares FROM funds WHERE code = ?;", (code,))
        f_row = cur.fetchone()
        curr_sh = float(f_row[0]) if f_row else 0.0
        conn.execute("UPDATE funds SET shares = ? WHERE code = ?;", (max(0.0, round(curr_sh - shares, 4)), code))
    elif t_type in ["VIRMAN", "TRANSFER"]:
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (max(0.0, round(curr_bal - amt, 2)), acc_id))
        if to_acc_id:
            cur.execute("SELECT balance FROM accounts WHERE id = ?;", (to_acc_id,))
            to_row = cur.fetchone()
            if to_row:
                to_bal = float(to_row[0])
                conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (round(to_bal + amt, 2), to_acc_id))


def _reverse_tx_effects_in_conn(conn: sqlite3.Connection, tx: Dict[str, Any]):
    t_type = tx.get("type", "").upper().strip()
    amt = float(tx.get("amount_try", 0.0))
    shares = float(tx.get("shares", 0.0))
    code = tx.get("code", "TRY").upper().strip()
    acc_id = normalize_account_id(tx.get("account_id"), "5004" if t_type in ["ALIM", "SATIM"] else "5001")
    to_acc_id = normalize_account_id(tx.get("to_account_id")) if tx.get("to_account_id") else None

    cur = conn.cursor()
    cur.execute("SELECT balance FROM accounts WHERE id = ?;", (acc_id,))
    acc_row = cur.fetchone()
    curr_bal = float(acc_row[0]) if acc_row else 0.0

    if t_type == "GİRİŞ":
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (max(0.0, round(curr_bal - amt, 2)), acc_id))
    elif t_type == "ÇIKIŞ":
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (round(curr_bal + amt, 2), acc_id))
    elif t_type == "ALIM":
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (round(curr_bal + amt, 2), acc_id))
        cur.execute("SELECT shares FROM funds WHERE code = ?;", (code,))
        f_row = cur.fetchone()
        curr_sh = float(f_row[0]) if f_row else 0.0
        conn.execute("UPDATE funds SET shares = ? WHERE code = ?;", (max(0.0, round(curr_sh - shares, 4)), code))
    elif t_type == "SATIM":
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (max(0.0, round(curr_bal - amt, 2)), acc_id))
        cur.execute("SELECT shares FROM funds WHERE code = ?;", (code,))
        f_row = cur.fetchone()
        curr_sh = float(f_row[0]) if f_row else 0.0
        conn.execute("UPDATE funds SET shares = ? WHERE code = ?;", (round(curr_sh + shares, 4), code))
    elif t_type in ["VIRMAN", "TRANSFER"]:
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (round(curr_bal + amt, 2), acc_id))
        if to_acc_id:
            cur.execute("SELECT balance FROM accounts WHERE id = ?;", (to_acc_id,))
            to_row = cur.fetchone()
            if to_row:
                to_bal = float(to_row[0])
                conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (max(0.0, round(to_bal - amt, 2)), to_acc_id))


def _recalculate_cash_accounts_in_conn(conn: sqlite3.Connection) -> Dict[str, float]:
    """Tüm defteri (transactions) kronolojik sırayla oynatarak banka/kasa bakiyelerini kesin olarak yeniden hesaplar."""
    cur = conn.cursor()
    existing_rows = cur.execute("SELECT id FROM accounts;").fetchall()
    balances: Dict[str, float] = {str(r["id"]): 0.0 for r in existing_rows}
    tx_rows = cur.execute(
        "SELECT type, amount_try, account_id, to_account_id FROM transactions ORDER BY date ASC, rowid ASC;"
    ).fetchall()
    for tr in tx_rows:
        t_type = str(tr["type"] or "").upper().strip()
        amt = float(tr["amount_try"] or 0.0)
        acc_id = normalize_account_id(tr["account_id"], "5004" if t_type in ("ALIM", "SATIM") else "5001")
        to_acc_id = normalize_account_id(tr["to_account_id"]) if tr["to_account_id"] else None
        if acc_id not in balances:
            balances[acc_id] = 0.0
            conn.execute(
                "INSERT OR IGNORE INTO accounts (id, name, balance, bank) VALUES (?, ?, 0.0, 'Banka');",
                (acc_id, f"Hesap {acc_id}"),
            )
        if t_type in ("GİRİŞ", "GIRIS", "SATIM"):
            balances[acc_id] = round(balances[acc_id] + amt, 2)
        elif t_type in ("ÇIKIŞ", "CIKIS", "ALIM"):
            balances[acc_id] = max(0.0, round(balances[acc_id] - amt, 2))
        elif t_type in ("VIRMAN", "TRANSFER"):
            balances[acc_id] = max(0.0, round(balances[acc_id] - amt, 2))
            if to_acc_id:
                if to_acc_id not in balances:
                    balances[to_acc_id] = 0.0
                    conn.execute(
                        "INSERT OR IGNORE INTO accounts (id, name, balance, bank) VALUES (?, ?, 0.0, 'Banka');",
                        (to_acc_id, f"Hesap {to_acc_id}"),
                    )
                balances[to_acc_id] = round(balances[to_acc_id] + amt, 2)
    for aid, bal in balances.items():
        conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (round(bal, 2), aid))
    return balances


def recalculate_accounts_from_ledger(push_cloud: bool = True) -> Dict[str, float]:
    """Defterdeki tüm işlemleri baştan oynatarak hesap nakit bakiyelerini defterle %100 eşitler."""
    conn = get_connection()
    try:
        with conn:
            balances = _recalculate_cash_accounts_in_conn(conn)
    finally:
        conn.close()
    if push_cloud:
        schedule_cloud_sync()
    return balances


def update_transaction_atomic(tx_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Mevcut işlemi atomik olarak günceller; defter değiştiyse fon payını ve kasa bakiyelerini kesin olarak eşitler."""
    conn = get_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM transactions WHERE id = ?;", (tx_id,))
            row = cur.fetchone()
            if not row:
                return None
            old_tx = dict(row)
            new_tx = dict(old_tx)

            if updates.get("date") is not None and str(updates["date"]).strip():
                new_tx["date"] = str(updates["date"]).strip()
            if updates.get("category") is not None and str(updates["category"]).strip():
                new_tx["category"] = str(updates["category"]).strip()
            if updates.get("description") is not None:
                new_tx["description"] = str(updates["description"]).strip()

            financial_keys = ("type", "amount_try", "shares", "price", "code", "account_id", "to_account_id")
            if "type" in updates and updates["type"]:
                new_tx["type"] = str(updates["type"]).upper().strip()
            if "amount_try" in updates and updates["amount_try"] is not None:
                new_tx["amount_try"] = round(abs(float(updates["amount_try"])), 2)
            if "shares" in updates and updates["shares"] is not None:
                new_tx["shares"] = round(max(0.0, float(updates["shares"])), 4)
            if "price" in updates and updates["price"] is not None:
                new_tx["price"] = max(0.0, float(updates["price"]))
            if "code" in updates and updates["code"]:
                new_tx["code"] = str(updates["code"]).upper().strip()
            if "account_id" in updates and updates["account_id"]:
                new_tx["account_id"] = normalize_account_id(updates["account_id"], "5001")
            if "to_account_id" in updates:
                new_tx["to_account_id"] = normalize_account_id(updates["to_account_id"]) if updates["to_account_id"] else None

            if new_tx["type"] not in ("VIRMAN", "TRANSFER"):
                new_tx["to_account_id"] = None
            if new_tx["type"] not in ("ALIM", "SATIM"):
                new_tx["shares"] = 0.0
                new_tx["price"] = 0.0
                new_tx["code"] = "TRY"

            financial_changed = any(old_tx.get(k) != new_tx.get(k) for k in financial_keys)
            date_changed = old_tx.get("date") != new_tx.get("date")
            if financial_changed:
                _reverse_tx_effects_in_conn(conn, old_tx)
                _apply_tx_effects_in_conn(conn, new_tx, weighted_avg=False)

            conn.execute(
                """
                UPDATE transactions
                SET date = ?, type = ?, category = ?, amount_try = ?, shares = ?, price = ?,
                    code = ?, account_id = ?, to_account_id = ?, description = ?
                WHERE id = ?;
                """,
                (
                    new_tx["date"],
                    new_tx["type"],
                    new_tx["category"],
                    new_tx["amount_try"],
                    new_tx["shares"],
                    new_tx["price"],
                    new_tx["code"],
                    new_tx["account_id"],
                    new_tx["to_account_id"],
                    new_tx["description"],
                    tx_id,
                ),
            )
            if financial_changed or date_changed:
                _recalculate_cash_accounts_in_conn(conn)
    finally:
        conn.close()
    schedule_cloud_sync()
    return new_tx


def delete_transaction(tx_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    target_tx = None
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM transactions WHERE id = ?;", (tx_id,))
            row = cur.fetchone()
            if not row:
                return None
            target_tx = dict(row)
            conn.execute("DELETE FROM transactions WHERE id = ?;", (tx_id,))
    finally:
        conn.close()
    if target_tx:
        schedule_cloud_sync()
    return target_tx


def delete_transaction_atomic(tx_id: str) -> Optional[Dict[str, Any]]:
    """İşlemi siler, fon etkisini geri alır ve tüm kasa bakiyelerini defterden yeniden hesaplar."""
    conn = get_connection()
    target_tx = None
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM transactions WHERE id = ?;", (tx_id,))
            row = cur.fetchone()
            if not row:
                return None
            target_tx = dict(row)
            _reverse_tx_effects_in_conn(conn, target_tx)
            conn.execute("DELETE FROM transactions WHERE id = ?;", (tx_id,))
            _recalculate_cash_accounts_in_conn(conn)
    finally:
        conn.close()
    if target_tx:
        schedule_cloud_sync()
    return target_tx


def revert_last_transaction() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    tx = None
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM transactions ORDER BY date DESC, rowid DESC LIMIT 1;")
            row = cur.fetchone()
            if not row:
                return None
            tx = dict(row)
            _reverse_tx_effects_in_conn(conn, tx)
            conn.execute("DELETE FROM transactions WHERE id = ?;", (tx["id"],))
            _recalculate_cash_accounts_in_conn(conn)
    finally:
        conn.close()
    if tx:
        schedule_cloud_sync()
    return tx


def record_net_worth_snapshot(total_try: float, cash_try: float, funds_try: float, date_str: Optional[str] = None):
    """Günlük portföy toplam değerini idempotent olarak kaydeder ve buluta (Turso) senkronize eder."""
    now_dt = now_tr()
    d_str = date_str or now_dt.strftime("%Y-%m-%d")
    total_r = round(float(total_try), 2)
    cash_r = round(float(cash_try), 2)
    funds_r = round(float(funds_try), 2)
    now_ts = time.time()

    if total_r < 0:
        return

    if (
        _LAST_SNAPSHOT_CACHE.get("date") == d_str
        and _LAST_SNAPSHOT_CACHE.get("total") == total_r
        and (now_ts - _LAST_SNAPSHOT_CACHE.get("ts", 0.0)) < 60.0
    ):
        return

    should_sync = False
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT total_try FROM net_worth_history WHERE date = ?;", (d_str,))
        prev_row = cur.fetchone()
        is_new_date = prev_row is None
        prev_total = float(prev_row["total_try"]) if prev_row is not None else 0.0
        # Geçici 0.0 okumalarıyla mevcut geçmiş günü ezme
        if not is_new_date and total_r == 0.0 and prev_total > 0.0:
            return
        with conn:
            conn.execute(
                """
                INSERT INTO net_worth_history (date, total_try, cash_try, funds_try, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_try = excluded.total_try,
                    cash_try = excluded.cash_try,
                    funds_try = excluded.funds_try,
                    updated_at = excluded.updated_at;
                """,
                (d_str, total_r, cash_r, funds_r, now_dt.strftime("%Y-%m-%d %H:%M:%S")),
            )
        last_cloud_ts = float(_LAST_SNAPSHOT_CACHE.get("cloud_ts", 0.0))
        if is_new_date or (abs(total_r - prev_total) >= 0.50 and (now_ts - last_cloud_ts) >= 180.0):
            should_sync = True
            _LAST_SNAPSHOT_CACHE["cloud_ts"] = now_ts
        _LAST_SNAPSHOT_CACHE["date"] = d_str
        _LAST_SNAPSHOT_CACHE["total"] = total_r
        _LAST_SNAPSHOT_CACHE["ts"] = now_ts
    finally:
        conn.close()

    if should_sync:
        schedule_cloud_sync()


def _lookup_fund_price_on_or_before(fund_market: Optional[Dict[str, Any]], code: str, target_date: str, fallback: float) -> float:
    """Belirli bir tarih (veya öncesindeki en son iş günü) için TEFAS fon fiyatını döndürür."""
    info = (fund_market or {}).get(code) or {}
    p_map = info.get("prices_by_date") or {}
    if isinstance(p_map, dict) and p_map:
        exact = p_map.get(target_date)
        if exact is not None and float(exact or 0.0) > 0:
            return float(exact)
        earlier = [d for d in p_map.keys() if d <= target_date and float(p_map.get(d) or 0.0) > 0]
        if earlier:
            return float(p_map[max(earlier)])
        valid_keys = [d for d in p_map.keys() if float(p_map.get(d) or 0.0) > 0]
        if valid_keys:
            return float(p_map[min(valid_keys)])
    cur_p = float(info.get("price") or 0.0)
    return cur_p if cur_p > 0 else float(fallback or 0.0)


def ensure_net_worth_backfill(fund_market: Optional[Dict[str, Any]] = None) -> int:
    """Eksik geçmiş günlük varlık kayıtlarını işlem defteri + valör emirleri + TEFAS fiyatlarından geri doldurur."""
    today_dt = now_tr().date()
    today_str = today_dt.strftime("%Y-%m-%d")

    conn = get_connection()
    inserted = 0
    try:
        cur = conn.cursor()
        existing_dates = {
            str(r["date"])
            for r in cur.execute("SELECT date FROM net_worth_history;").fetchall()
            if r["date"]
        }
        txs = [dict(r) for r in cur.execute("SELECT * FROM transactions ORDER BY date DESC, rowid DESC;").fetchall()]
        pending = [dict(r) for r in cur.execute("SELECT * FROM pending_orders;").fetchall()]
        accounts = [dict(r) for r in cur.execute("SELECT balance FROM accounts;").fetchall()]
        funds = [dict(r) for r in cur.execute("SELECT code, shares, buy_price FROM funds;").fetchall()]

        candidate_dates = [str(tx.get("date") or "")[:10] for tx in txs if str(tx.get("date") or "")[:10]]
        candidate_dates.extend(
            str(po.get("created_at") or "")[:10] for po in pending if str(po.get("created_at") or "")[:10]
        )
        valid_dates = []
        for ds in candidate_dates:
            try:
                valid_dates.append(datetime.date.fromisoformat(ds))
            except ValueError:
                continue
        if not valid_dates:
            return 0

        earliest_dt = max(min(valid_dates), today_dt - datetime.timedelta(days=365))
        if earliest_dt >= today_dt:
            return 0

        # Eksik gün yoksa hızlıca çık
        missing_any = False
        probe = earliest_dt
        while probe < today_dt:
            if probe.strftime("%Y-%m-%d") not in existing_dates:
                missing_any = True
                break
            probe += datetime.timedelta(days=1)
        if not missing_any:
            return 0

        cur_cash = sum(float(a.get("balance") or 0.0) for a in accounts)
        cur_shares: Dict[str, float] = {
            str(f.get("code") or "").upper().strip(): float(f.get("shares") or 0.0)
            for f in funds
            if f.get("code")
        }
        buy_prices: Dict[str, float] = {
            str(f.get("code") or "").upper().strip(): float(f.get("buy_price") or 0.0)
            for f in funds
            if f.get("code")
        }

        unsettled_pool = [
            {
                "code": str(po.get("code") or "").upper().strip(),
                "order_type": str(po.get("order_type") or "ALIM").upper().strip(),
                "amount_try": float(po.get("amount_try") or 0.0),
                "created_date": str(po.get("created_at") or "")[:10],
                "matched": False,
            }
            for po in pending
            if str(po.get("status") or "").upper().strip() == "BEKLIYOR"
        ]

        tx_by_date: Dict[str, List[Dict[str, Any]]] = {}
        for tx in txs:
            ds = str(tx.get("date") or "")[:10]
            if ds:
                tx_by_date.setdefault(ds, []).append(tx)

        to_insert = []
        cur_day = today_dt
        while cur_day >= earliest_dt:
            d_str = cur_day.strftime("%Y-%m-%d")
            if cur_day < today_dt and d_str not in existing_dates:
                funds_val = 0.0
                for code, sh in cur_shares.items():
                    if sh > 0.0001:
                        px = _lookup_fund_price_on_or_before(fund_market, code, d_str, buy_prices.get(code, 0.0))
                        funds_val += sh * px
                pending_val = sum(
                    po["amount_try"]
                    for po in unsettled_pool
                    if po["order_type"] == "ALIM" and po["created_date"] and po["created_date"] <= d_str
                )
                cash_r = round(max(0.0, cur_cash), 2)
                inv_r = round(max(0.0, funds_val + pending_val), 2)
                tot_r = round(cash_r + inv_r, 2)
                to_insert.append((d_str, tot_r, cash_r, inv_r, f"{d_str} 23:59:00"))

            # Gün sonundan bir önceki gün sonuna geri sar
            for tx in tx_by_date.get(d_str, []):
                t_type = str(tx.get("type") or "").upper().strip()
                amt = float(tx.get("amount_try") or 0.0)
                sh = float(tx.get("shares") or 0.0)
                code = str(tx.get("code") or "").upper().strip()

                matched_pending = None
                if t_type in ("ALIM", "SATIM"):
                    for po in unsettled_pool:
                        if (
                            not po["matched"]
                            and po["order_type"] == t_type
                            and po["code"] == code
                            and po["created_date"] == d_str
                            and abs(po["amount_try"] - amt) < 0.05
                        ):
                            po["matched"] = True
                            matched_pending = po
                            break

                if t_type == "GİRİŞ":
                    cur_cash -= amt
                elif t_type == "ÇIKIŞ":
                    cur_cash += amt
                elif t_type == "ALIM":
                    cur_cash += amt
                    if matched_pending is None and code and code != "TRY":
                        cur_shares[code] = max(0.0, cur_shares.get(code, 0.0) - sh)
                elif t_type == "SATIM":
                    if matched_pending is None:
                        cur_cash -= amt
                    if code and code != "TRY":
                        cur_shares[code] = cur_shares.get(code, 0.0) + sh

            cur_day -= datetime.timedelta(days=1)

        if to_insert:
            with conn:
                for row_vals in to_insert:
                    cur.execute(
                        "INSERT OR IGNORE INTO net_worth_history (date, total_try, cash_try, funds_try, updated_at) VALUES (?, ?, ?, ?, ?);",
                        row_vals,
                    )
                    if cur.rowcount > 0:
                        inserted += 1
    finally:
        conn.close()

    if inserted > 0:
        schedule_cloud_sync()
    return inserted


def get_net_worth_history(days: int = 30) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        if days > 0:
            cutoff = (now_tr() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            cur.execute(
                "SELECT date, total_try, cash_try, funds_try, updated_at FROM net_worth_history WHERE date >= ? ORDER BY date ASC;",
                (cutoff,),
            )
        else:
            cur.execute("SELECT date, total_try, cash_try, funds_try, updated_at FROM net_worth_history ORDER BY date ASC;")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_recurring_rules() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM recurring_rules ORDER BY day_of_month ASC, title ASC;")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def upsert_recurring_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    rule_id = (rule.get("id") or f"rec-{uuid.uuid4().hex[:8]}").strip()
    title = (rule.get("title") or "Sabit Akış").strip()
    t_type = (rule.get("type") or "GİRİŞ").upper().strip()
    if t_type not in ("GİRİŞ", "ÇIKIŞ", "ALIM"):
        t_type = "GİRİŞ"
    category = (rule.get("category") or "Sabit Akış").strip()
    amount_try = round(max(0.01, float(rule.get("amount_try", 0.0))), 2)
    account_id = normalize_account_id(rule.get("account_id"), "5001")
    day_of_month = min(28, max(1, int(rule.get("day_of_month", 1))))
    enabled = 1 if rule.get("enabled", True) else 0
    created_at = now_tr().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO recurring_rules (id, title, type, category, amount_try, account_id, day_of_month, enabled, last_triggered_month, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    type = excluded.type,
                    category = excluded.category,
                    amount_try = excluded.amount_try,
                    account_id = excluded.account_id,
                    day_of_month = excluded.day_of_month,
                    enabled = excluded.enabled;
                """,
                (rule_id, title, t_type, category, amount_try, account_id, day_of_month, enabled, rule.get("last_triggered_month"), created_at),
            )
            cur = conn.cursor()
            cur.execute("SELECT * FROM recurring_rules WHERE id = ?;", (rule_id,))
            saved = dict(cur.fetchone())
    finally:
        conn.close()
    schedule_cloud_sync()
    return saved


def delete_recurring_rule(rule_id: str) -> bool:
    conn = get_connection()
    deleted = False
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM recurring_rules WHERE id = ?;", (rule_id,))
            deleted = cur.rowcount > 0
    finally:
        conn.close()
    if deleted:
        schedule_cloud_sync()
    return deleted


def mark_recurring_triggered(rule_id: str, month_str: str):
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE recurring_rules SET last_triggered_month = ? WHERE id = ?;",
                (month_str, rule_id),
            )
    finally:
        conn.close()
    schedule_cloud_sync()


def get_price_alerts() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM price_alerts ORDER BY created_at DESC;")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def create_price_alert(code: str, condition: str, threshold: float, note: str = "") -> Dict[str, Any]:
    conn = get_connection()
    alert_id = f"alt-{uuid.uuid4().hex[:8]}"
    created_at = now_tr().strftime("%Y-%m-%d %H:%M:%S")
    norm_code = str(code or "TCA").upper().strip()
    norm_cond = str(condition or "BELOW").upper().strip()
    if norm_cond not in ("BELOW", "ABOVE", "DROP_PCT"):
        norm_cond = "BELOW"
    val = float(threshold)
    clean_note = str(note or "").strip()[:120]
    try:
        with conn:
            conn.execute(
                """INSERT INTO price_alerts (id, code, condition, threshold, note, enabled, last_triggered_at, created_at)
                VALUES (?, ?, ?, ?, ?, 1, NULL, ?);""",
                (alert_id, norm_code, norm_cond, val, clean_note, created_at),
            )
            cur = conn.cursor()
            cur.execute("SELECT * FROM price_alerts WHERE id = ?;", (alert_id,))
            saved = dict(cur.fetchone())
    finally:
        conn.close()
    schedule_cloud_sync()
    return saved


def delete_price_alert(alert_id: str) -> bool:
    conn = get_connection()
    deleted = False
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM price_alerts WHERE id = ?;", (alert_id,))
            deleted = cur.rowcount > 0
    finally:
        conn.close()
    if deleted:
        schedule_cloud_sync()
    return deleted


def mark_price_alert_triggered(alert_id: str, ts_str: str):
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE price_alerts SET last_triggered_at = ? WHERE id = ?;", (ts_str, alert_id))
    finally:
        conn.close()
    schedule_cloud_sync()


def get_monthly_burn_stats() -> Dict[str, Any]:
    """Son 90 gündeki gerçek ÇIKIŞ (harcama) işlemlerinden aylık ortalama gider hesaplar."""
    txs = get_all_transactions()
    cutoff = (now_tr() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
    outflows = [t for t in txs if str(t.get("type", "")).upper().strip() == "ÇIKIŞ" and str(t.get("date", ""))[:10] >= cutoff]
    if not outflows:
        return {"monthly_burn_try": 0.0, "has_burn_history": False}
    months = {str(t.get("date", ""))[:7] for t in outflows if len(str(t.get("date", ""))) >= 7}
    total_spent = sum(float(t.get("amount_try", 0.0)) for t in outflows)
    div = max(1, len(months))
    return {"monthly_burn_try": round(total_spent / div, 2), "has_burn_history": True}


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM app_settings WHERE key = ?;", (key,))
        row = cur.fetchone()
        return row["value"] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def set_setting(key: str, value: str, sync_cloud: bool = False):
    now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at;
                """,
                (key, str(value), now_str),
            )
    finally:
        conn.close()
    if sync_cloud:
        schedule_cloud_sync()


def get_weekly_summary_stats() -> Dict[str, Any]:
    """Son 7 gün ile önceki 7 günü karşılaştırarak haftalık özet istatistiklerini döndürür."""
    today = now_tr().date()
    d_7 = (today - datetime.timedelta(days=6)).strftime("%Y-%m-%d")
    d_14 = (today - datetime.timedelta(days=13)).strftime("%Y-%m-%d")
    today_s = today.strftime("%Y-%m-%d")

    txs = get_all_transactions()
    spent_7d = 0.0
    spent_prev_7d = 0.0
    income_7d = 0.0
    cat_totals: Dict[str, float] = {}

    for t in txs:
        dt_part = str(t.get("date", ""))[:10]
        if not dt_part:
            continue
        t_type = str(t.get("type", "")).upper().strip()
        amt = float(t.get("amount_try", 0.0) or 0.0)
        if d_7 <= dt_part <= today_s:
            if t_type == "ÇIKIŞ":
                spent_7d += amt
                cat = (t.get("category") or "Diğer").strip() or "Diğer"
                cat_totals[cat] = cat_totals.get(cat, 0.0) + amt
            elif t_type == "GİRİŞ":
                income_7d += amt
        elif d_14 <= dt_part < d_7 and t_type == "ÇIKIŞ":
            spent_prev_7d += amt

    spend_delta_pct = None
    if spent_prev_7d > 0:
        spend_delta_pct = round(((spent_7d - spent_prev_7d) / spent_prev_7d) * 100.0, 1)

    top_category = None
    top_category_amount = 0.0
    top_category_pct = 0.0
    if cat_totals and spent_7d > 0:
        top_category, top_category_amount = max(cat_totals.items(), key=lambda kv: kv[1])
        top_category_pct = round((top_category_amount / spent_7d) * 100.0, 1)

    history = get_net_worth_history(days=14)
    target_7d_date = (today - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    ref_try = None
    for h in history:
        if str(h.get("date", "")) <= target_7d_date:
            ref_try = float(h.get("total_try", 0.0))
    if ref_try is None and history:
        # En eski mevcut nokta (7 günden yeni başlangıç varsa)
        oldest = history[0]
        if str(oldest.get("date", "")) < today_s:
            ref_try = float(oldest.get("total_try", 0.0))

    return {
        "spent_7d": round(spent_7d, 2),
        "spent_prev_7d": round(spent_prev_7d, 2),
        "spend_delta_pct": spend_delta_pct,
        "income_7d": round(income_7d, 2),
        "top_category": top_category,
        "top_category_amount": round(top_category_amount, 2),
        "top_category_pct": top_category_pct,
        "history_7d_ago_try": round(ref_try, 2) if ref_try is not None else None,
    }


def get_total_cash() -> float:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(balance), 0.0) FROM accounts;")
        total = float(cur.fetchone()[0])
        return round(total, 2)
    finally:
        conn.close()


def get_pending_orders(status: Optional[str] = "BEKLIYOR") -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        if status:
            cur.execute("SELECT * FROM pending_orders WHERE status = ? ORDER BY created_at DESC;", (status.upper(),))
        else:
            cur.execute("SELECT * FROM pending_orders ORDER BY created_at DESC;")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_pending_total_try() -> float:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(amount_try), 0.0) FROM pending_orders WHERE status = 'BEKLIYOR' AND order_type = 'ALIM';")
        return round(float(cur.fetchone()[0] or 0.0), 2)
    finally:
        conn.close()


def create_pending_order(
    code: str,
    amount_try: float,
    estimated_shares: float = 0.0,
    estimated_price: float = 0.0,
    account_id: str = "5004",
    fund_name: str = "",
    expected_settlement: str = "TEFAS Valör Bekleniyor",
    note: str = "",
    deduct_cash: bool = True,
    record_tx: bool = True,
) -> Dict[str, Any]:
    norm_code = str(code or "TCA").upper().strip()
    acc_id = normalize_account_id(account_id, "5004")
    amt = round(abs(float(amount_try)), 2)
    est_sh = round(max(0.0, float(estimated_shares or 0.0)), 4)
    est_p = float(estimated_price or 0.0)
    now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
    oid = f"ord-{uuid.uuid4().hex[:8]}"

    conn = get_connection()
    try:
        with conn:
            if deduct_cash and amt > 0:
                cur = conn.cursor()
                cur.execute("SELECT balance FROM accounts WHERE id = ?;", (acc_id,))
                row = cur.fetchone()
                curr_bal = float(row[0]) if row else 0.0
                conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (max(0.0, round(curr_bal - amt, 2)), acc_id))

            conn.execute(
                """
                INSERT INTO pending_orders
                (id, code, fund_name, order_type, amount_try, estimated_shares, estimated_price, account_id, status, expected_settlement, note, created_at, settled_at)
                VALUES (?, ?, ?, 'ALIM', ?, ?, ?, ?, 'BEKLIYOR', ?, ?, ?, NULL);
                """,
                (oid, norm_code, fund_name or norm_code, amt, est_sh, est_p, acc_id, expected_settlement, note, now_str),
            )

            if record_tx:
                tx_id = f"tx-ord-{uuid.uuid4().hex[:6]}"
                desc = note or f"{norm_code} Fon Alım Emri (Valörde - ₺{amt:,.2f})"
                conn.execute(
                    """
                    INSERT INTO transactions (id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description)
                    VALUES (?, ?, 'ALIM', 'Fon Emri (Valör)', ?, ?, ?, ?, ?, NULL, ?);
                    """,
                    (tx_id, now_str, amt, est_sh, est_p, norm_code, acc_id, desc),
                )
    finally:
        conn.close()

    schedule_cloud_sync()
    return {
        "id": oid,
        "code": norm_code,
        "fund_name": fund_name or norm_code,
        "order_type": "ALIM",
        "amount_try": amt,
        "estimated_shares": est_sh,
        "estimated_price": est_p,
        "account_id": acc_id,
        "status": "BEKLIYOR",
        "expected_settlement": expected_settlement,
        "note": note,
        "created_at": now_str,
    }


def move_active_fund_to_pending(code: str, note: str = "") -> Dict[str, Any]:
    """Aktif fonda görünen henüz işleme girmemiş pozisyonu valördeki emirlere taşır (sahte kâr/zararı sıfırlar)."""
    norm_code = str(code or "TCA").upper().strip()
    now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
    oid = f"ord-{uuid.uuid4().hex[:8]}"

    conn = get_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT code, shares, buy_price, name FROM funds WHERE code = ?;", (norm_code,))
            row = cur.fetchone()
            if not row or float(row["shares"] or 0.0) <= 0:
                raise ValueError(f"{norm_code} fonunda taşınacak aktif pay bulunamadı.")
            sh = float(row["shares"])
            bp = float(row["buy_price"] or 0.0)
            fname = row["name"] or norm_code
            amt = round(sh * bp, 2)

            conn.execute("UPDATE funds SET shares = 0.0 WHERE code = ?;", (norm_code,))
            conn.execute(
                """
                INSERT INTO pending_orders
                (id, code, fund_name, order_type, amount_try, estimated_shares, estimated_price, account_id, status, expected_settlement, note, created_at, settled_at)
                VALUES (?, ?, ?, 'ALIM', ?, ?, ?, '5004', 'BEKLIYOR', 'TEFAS Valör Bekleniyor', ?, ?, NULL);
                """,
                (oid, norm_code, fname, amt, round(sh, 4), bp, note or f"{norm_code} henüz işleme girmedi — valöre alındı", now_str),
            )
    finally:
        conn.close()

    schedule_cloud_sync()
    return {"status": "ok", "order_id": oid, "code": norm_code, "amount_try": amt, "shares": sh}


def settle_pending_order(
    order_id: str,
    settled_price: Optional[float] = None,
    settled_shares: Optional[float] = None,
) -> Dict[str, Any]:
    """Valördeki emri gerçekleşen fiyattan/pay adedinden aktif fona geçirir; artan kuruş varsa hesaba iade eder."""
    now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM pending_orders WHERE id = ?;", (order_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Bekleyen emir bulunamadı.")
            ord_dict = dict(row)
            if ord_dict["status"] != "BEKLIYOR":
                raise ValueError("Bu emir zaten sonuçlanmış.")

            code = ord_dict["code"].upper().strip()
            acc_id = normalize_account_id(ord_dict.get("account_id"), "5004")
            amt = float(ord_dict["amount_try"])
            use_price = float(settled_price) if (settled_price and float(settled_price) > 0) else float(ord_dict.get("estimated_price") or 0.0)
            if use_price <= 0:
                use_price = 1.0

            if settled_shares is not None and float(settled_shares) > 0:
                final_shares = float(int(float(settled_shares))) if float(settled_shares) >= 1 else round(float(settled_shares), 4)
            else:
                import market_service
                lot_step = market_service.get_fund_lot_step(code)
                raw_units = int((amt + 1e-9) / use_price)
                lot_units = (raw_units // lot_step) * lot_step
                final_shares = float(lot_units) if lot_units > 0 else float(raw_units)

            actual_spent = round(final_shares * use_price, 2)
            refund_krs = round(max(0.0, amt - actual_spent), 2)

            cur.execute("SELECT shares, buy_price, name FROM funds WHERE code = ?;", (code,))
            f_row = cur.fetchone()
            if f_row:
                old_sh = float(f_row["shares"] or 0.0)
                old_bp = float(f_row["buy_price"] or 0.0)
                new_sh = round(old_sh + final_shares, 4)
                if old_sh > 0:
                    new_bp = round(((old_sh * old_bp) + (final_shares * use_price)) / new_sh, 6)
                else:
                    new_bp = round(use_price, 6)
                conn.execute("UPDATE funds SET shares = ?, buy_price = ? WHERE code = ?;", (new_sh, new_bp, code))
            else:
                conn.execute(
                    "INSERT INTO funds (code, shares, buy_price, name) VALUES (?, ?, ?, ?);",
                    (code, final_shares, round(use_price, 6), ord_dict.get("fund_name") or code),
                )

            if refund_krs >= 0.01:
                cur.execute("SELECT balance FROM accounts WHERE id = ?;", (acc_id,))
                acc_row = cur.fetchone()
                if acc_row:
                    conn.execute(
                        "UPDATE accounts SET balance = ? WHERE id = ?;",
                        (round(float(acc_row[0]) + refund_krs, 2), acc_id),
                    )

            conn.execute(
                "UPDATE pending_orders SET status = 'TAMAMLANDI', settled_at = ? WHERE id = ?;",
                (now_str, order_id),
            )
    finally:
        conn.close()

    schedule_cloud_sync()
    return {
        "status": "ok",
        "order_id": order_id,
        "code": code,
        "settled_shares": final_shares,
        "settled_price": round(use_price, 6),
        "actual_spent": actual_spent,
        "refund_krs": refund_krs,
    }


def cancel_pending_order(order_id: str) -> Dict[str, Any]:
    """Valördeki emri iptal eder ve bloke tutarı kaynağa (gerçek bakiye taşması olmadan) iade eder."""
    now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    refunded = 0.0
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM pending_orders WHERE id = ?;", (order_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Bekleyen emir bulunamadı.")
            ord_dict = dict(row)
            if ord_dict["status"] != "BEKLIYOR":
                raise ValueError("Bu emir zaten sonuçlanmış.")

            acc_id = normalize_account_id(ord_dict.get("account_id"), "5004")
            amt = round(float(ord_dict["amount_try"]), 2)
            # İlgili valör transaction kaydı varsa iade öncesi kontrol et ve temizle
            cur.execute(
                "SELECT id FROM transactions WHERE code = ? AND account_id = ? AND ABS(amount_try - ?) < 0.02 AND category LIKE '%Valör%' ORDER BY date DESC LIMIT 1;",
                (ord_dict.get("code", ""), acc_id, amt),
            )
            tx_row = cur.fetchone()
            if tx_row:
                conn.execute("DELETE FROM transactions WHERE id = ?;", (tx_row[0],))

            cur.execute("SELECT balance FROM accounts WHERE id = ?;", (acc_id,))
            acc_row = cur.fetchone()
            if acc_row:
                curr_bal = float(acc_row[0] or 0.0)
                refunded = amt
                conn.execute("UPDATE accounts SET balance = ? WHERE id = ?;", (round(curr_bal + refunded, 2), acc_id))

            conn.execute("UPDATE pending_orders SET status = 'IPTAL', settled_at = ? WHERE id = ?;", (now_str, order_id))
    finally:
        conn.close()

    schedule_cloud_sync()
    return {"status": "ok", "order_id": order_id, "refunded_try": refunded, "account_id": acc_id}


def _ensure_unsettled_tca_in_valor():
    """12.11.2026 19:36 tarihli (TEFAS 13:30 kesimi sonrası) henüz işleme girmemiş TCA alımını tek seferlik valöre taşır."""
    if get_setting("tca_20261112_valor_handled") == "1":
        return
    conn = get_connection()
    migrated = False
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM pending_orders WHERE status = 'BEKLIYOR';")
            if int(cur.fetchone()[0] or 0) > 0:
                set_setting("tca_20261112_valor_handled", "1", sync_cloud=False)
                return

            cur.execute("SELECT shares, buy_price, name FROM funds WHERE code = 'TCA';")
            f_row = cur.fetchone()
            cur.execute("SELECT id, date, amount_try, shares, price, account_id FROM transactions WHERE id = 'tx-tg-2a393a';")
            tx_row = cur.fetchone()
            if f_row and tx_row and abs(float(f_row["shares"] or 0.0) - 1187.0) < 0.01:
                amt = round(float(tx_row["amount_try"] or 896.91), 2)
                est_sh = float(tx_row["shares"] or 1187.0)
                est_p = float(tx_row["price"] or 0.755613)
                acc_id = normalize_account_id(tx_row["account_id"], "5004")
                conn.execute("UPDATE funds SET shares = 0.0 WHERE code = 'TCA';")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pending_orders
                    (id, code, fund_name, order_type, amount_try, estimated_shares, estimated_price, account_id, status, expected_settlement, note, created_at, settled_at)
                    VALUES ('ord-tca-20261112', 'TCA', ?, 'ALIM', ?, ?, ?, ?, 'BEKLIYOR', 'TEFAS Valör (13:30 Sonrası Emir)', '12.11.2026 19:36 Altın Fonu alımı henüz işleme girmedi — valörde bekliyor', ?, NULL);
                    """,
                    (f_row["name"] or "Ziraat Altın Katılım Fonu", amt, est_sh, est_p, acc_id, str(tx_row["date"])),
                )
                migrated = True
            now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ('tca_20261112_valor_handled', '1', ?)
                ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at;
                """,
                (now_str,),
            )
    finally:
        conn.close()
    if migrated:
        schedule_cloud_sync()


def _zero_5004_initial_kurus_dust(push_cloud: bool = True):
    """5004 (Hesap 4) içindeki eski 0.22/0.33 TL kuruş artığını tek seferlik 0.00 TL yapar."""
    if get_setting("fix_5004_kurus_dust_v1") == "1":
        return
    conn = get_connection()
    changed = False
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT balance FROM accounts WHERE id = '5004';")
            row = cur.fetchone()
            if row is not None:
                bal = float(row["balance"] or 0.0)
                if 0.0 < bal < 1.0:
                    conn.execute("UPDATE accounts SET balance = 0.0 WHERE id = '5004';")
                    changed = True
            now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ('fix_5004_kurus_dust_v1', '1', ?)
                ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at;
                """,
                (now_str,),
            )
    finally:
        conn.close()
    if changed and push_cloud:
        schedule_cloud_sync()


def _is_stale_nov14_tzl_tx(t: Dict[str, Any]) -> bool:
    """Eski hatalı lot matematiğiyle girilmiş 14 Kas TZL (3325 pay / 500 TL) veya 5004 bakiye düzeltme tekrarını tespit eder."""
    tid = str(t.get("id") or "")
    tdate = str(t.get("date") or "")
    ttype = str(t.get("type") or "").upper().strip()
    code = str(t.get("code") or "").upper().strip()
    cat = str(t.get("category") or "").strip()
    acc = normalize_account_id(t.get("account_id"), "5001")
    amt = float(t.get("amount_try") or 0.0)
    sh = float(t.get("shares") or 0.0)
    if not tdate.startswith("2026-11-14"):
        return False
    if ttype == "ALIM" and code == "TZL" and tid != "tx-14kas-tzl-buy":
        if abs(sh - 3325.0) < 40.0 or abs(sh - 3300.0) < 5.0 or abs(amt - 500.0) < 10.0:
            return True
    if ttype in ("GİRİŞ", "GIRIS") and code == "TRY" and acc == "5004" and tid != "tx-14kas-tzl-in":
        if abs(amt - 500.0) < 0.05:
            return True
        if (tid.startswith("tx-adj-") or cat == "Bakiye Senkronizasyonu") and (abs(amt - 3.86) < 0.05 or abs(amt - 0.22) < 0.05):
            return True
    return False


def _reconcile_real_ziraat_ledger_nov14(push_cloud: bool = True, force: bool = False):
    """Gerçek Ziraat Bankası dekontlarına (12 Kas 1000 TCA valör 897.13 TL + 14 Kas 3300 TZL 496.14 TL & defter bakiyesi) deterministik eşitleme."""
    if not force and get_setting("reconcile_nov14_real_ledger_v5") == "1":
        return
    conn = get_connection()
    changed = False
    try:
        with conn:
            cur = conn.cursor()
            # 1. 12 Kas TCA valör emrini gerçek banka bloke tutarı (897.13 TL / 1000 pay) ile eşitle
            cur.execute("SELECT status, amount_try, estimated_shares FROM pending_orders WHERE id = 'ord-tca-20261112';")
            po_row = cur.fetchone()
            if po_row and str(po_row["status"]).upper().strip() == "BEKLIYOR":
                if abs(float(po_row["amount_try"] or 0.0) - 897.13) > 0.01 or abs(float(po_row["estimated_shares"] or 0.0) - 1000.0) > 0.1:
                    conn.execute(
                        """
                        UPDATE pending_orders
                        SET amount_try = 897.13,
                            estimated_shares = 1000.0,
                            estimated_price = 0.89713,
                            note = '12.11.2026 Altın Fonu alımı (1.000 pay • ₺897,13 bloke — valörde bekliyor)'
                        WHERE id = 'ord-tca-20261112';
                        """
                    )
                    changed = True

            cur.execute("SELECT id, amount_try, shares FROM transactions WHERE id = 'tx-tg-2a393a';")
            tx_tca = cur.fetchone()
            if tx_tca and (abs(float(tx_tca["amount_try"] or 0.0) - 897.13) > 0.01 or abs(float(tx_tca["shares"] or 0.0) - 1000.0) > 0.1):
                conn.execute(
                    """
                    UPDATE transactions
                    SET amount_try = 897.13,
                        shares = 1000.0,
                        price = 0.89713,
                        description = 'TCA Fon Alımı (1.000 pay - ₺897.13)'
                    WHERE id = 'tx-tg-2a393a';
                    """
                )
                changed = True

            # 2. Eski hatalı (3325 pay vb.) 14 Kas TZL ve 5004 bakiye düzeltme tekrarlarını temizle
            stale_rows = cur.execute(
                "SELECT id, date, type, category, code, account_id, amount_try, shares FROM transactions WHERE date LIKE '2026-11-14%';"
            ).fetchall()
            removed_stale = False
            for sr in stale_rows:
                if _is_stale_nov14_tzl_tx(dict(sr)):
                    conn.execute("DELETE FROM transactions WHERE id = ?;", (sr["id"],))
                    removed_stale = True
                    changed = True

            # 3. 14 Kas 500 TL -> 496.14 TL (3300 TZL) & 5004 kalan 3.86 TL kanonik kaydı ekle/garantile
            cur.execute("SELECT id FROM transactions WHERE id = 'tx-14kas-tzl-in';")
            if not cur.fetchone():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO transactions
                    (id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description)
                    VALUES ('tx-14kas-tzl-in', '2026-11-14 17:00:00', 'GİRİŞ', 'Transfer', 500.0, 0.0, 0.0, 'TRY', '5004', NULL, '14 Kas TZL alımı için 5004 hesabına aktarılan bakiye');
                    """
                )
                changed = True

            cur.execute("SELECT id FROM transactions WHERE id = 'tx-14kas-tzl-buy';")
            if not cur.fetchone():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO transactions
                    (id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description)
                    VALUES ('tx-14kas-tzl-buy', '2026-11-14 17:01:00', 'ALIM', 'Para Piyasası', 496.14, 3300.0, 0.150345, 'TZL', '5004', NULL, 'TZL Fon Alımı (3.300 pay · ₺496,14 · Kalan ₺3,86)');
                    """
                )
                changed = True

            # 4. TZL bakiyesini (600 + 3300 = 3900 pay) işlem defterinden birebir hesapla (asla çift ekleme yapma)
            tzl_txs = cur.execute(
                "SELECT type, amount_try, shares, price FROM transactions WHERE UPPER(code) = 'TZL' ORDER BY date ASC;"
            ).fetchall()
            calc_sh = 0.0
            calc_cost = 0.0
            for tt in tzl_txs:
                tp = str(tt["type"] or "").upper().strip()
                sh = float(tt["shares"] or 0.0)
                amt = float(tt["amount_try"] or 0.0)
                if tp == "ALIM" and sh > 0:
                    calc_sh += sh
                    calc_cost += amt
                elif tp in ("SATIM", "SATIŞ") and sh > 0 and calc_sh > 0:
                    avg = calc_cost / calc_sh
                    sell_sh = min(sh, calc_sh)
                    calc_sh -= sell_sh
                    calc_cost = max(0.0, calc_sh * avg)
            calc_sh = round(calc_sh, 4)
            if calc_sh <= 0:
                calc_sh = 3900.0
                calc_bp = 0.150242
            else:
                calc_bp = round(calc_cost / calc_sh, 6)

            cur.execute("SELECT shares, buy_price FROM funds WHERE code = 'TZL';")
            tzl_row = cur.fetchone()
            if tzl_row:
                old_sh = float(tzl_row["shares"] or 0.0)
                if removed_stale or abs(old_sh - calc_sh) > 0.01:
                    conn.execute("UPDATE funds SET shares = ?, buy_price = ? WHERE code = 'TZL';", (calc_sh, calc_bp))
                    changed = True
            else:
                conn.execute(
                    "INSERT INTO funds (code, shares, buy_price, name) VALUES ('TZL', ?, ?, 'Ziraat Para Piyasası Fonu');",
                    (calc_sh, calc_bp),
                )
                changed = True

            # 5. ZBJ alış ortalamasını da (118.11 + 995.48 = 1113.59 / 198 = 5.624192) dekontla eşitle
            zbj_row = cur.execute("SELECT shares, buy_price FROM funds WHERE code = 'ZBJ';").fetchone()
            if zbj_row and abs(float(zbj_row["shares"] or 0.0) - 198.0) < 0.01:
                if abs(float(zbj_row["buy_price"] or 0.0) - 5.624192) > 0.00001:
                    conn.execute("UPDATE funds SET buy_price = 5.624192 WHERE code = 'ZBJ';")
                    changed = True

            # 6. Tüm banka/kasa nakit bakiyelerini işlem defterinden kronolojik olarak eşitle (5004=3.86 vb.)
            recalc_bals = _recalculate_cash_accounts_in_conn(conn)
            # Eğer defterde başka bir kalıntı nedeniyle 5004 hâlâ 2 katı (7.72) görünüyorsa temiz 3.86'ya çek
            if abs(float(recalc_bals.get("5004", 0.0)) - 7.72) < 0.08:
                conn.execute("UPDATE accounts SET balance = 3.86 WHERE id = '5004';")
            changed = True

            now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
            # 7. Ziraat Mobil 2026-11-16 hafta sonu kesin PPF birim fiyatlarını (ZBJ +4.35 TL, TZL +0.41 TL) başlangıç kalibrasyonu olarak ekle
            cal_row = cur.execute("SELECT value FROM app_settings WHERE key = 'ppf_bank_calibration';").fetchone()
            existing_cal = {}
            if cal_row and cal_row["value"]:
                try:
                    existing_cal = json.loads(cal_row["value"]) or {}
                except Exception:
                    existing_cal = {}
            cal_changed = False
            if (existing_cal.get("ZBJ") or {}).get("target_date") != "2026-11-16":
                existing_cal["ZBJ"] = {"target_date": "2026-11-16", "price": 5.646162, "daily_rate": 0.000977, "updated_at": now_str}
                cal_changed = True
            if (existing_cal.get("TZL") or {}).get("target_date") != "2026-11-16":
                existing_cal["TZL"] = {"target_date": "2026-11-16", "price": 0.150346, "daily_rate": 0.000960, "updated_at": now_str}
                cal_changed = True
            if cal_changed:
                conn.execute(
                    """
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES ('ppf_bank_calibration', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                    """,
                    (json.dumps(existing_cal, ensure_ascii=False), now_str),
                )
                changed = True

            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ('reconcile_nov14_real_ledger_v5', '1', ?)
                ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at;
                """,
                (now_str,),
            )
    finally:
        conn.close()
    if changed and push_cloud:
        schedule_cloud_sync()


SYNCED_SETTING_KEYS = (
    "ai_config",
    "tca_20261112_valor_handled",
    "fix_5004_kurus_dust_v1",
    "reconcile_nov14_real_ledger_v1",
    "reconcile_nov14_real_ledger_v2",
    "reconcile_nov14_real_ledger_v3",
    "reconcile_nov14_real_ledger_v4",
    "reconcile_nov14_real_ledger_v5",
    "ppf_bank_calibration",
    "valor_auto_config",
    "tax_stopaj_rates",
    "auto_allocator_config",
    "user_preferences",
    "goals_config",
)


def _get_all_app_settings_map() -> Dict[str, str]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM app_settings;").fetchall()
        return {str(r["key"]): str(r["value"]) for r in rows if r["key"] is not None and r["value"] is not None}
    finally:
        conn.close()


def export_full_backup() -> Dict[str, Any]:
    accounts = get_all_accounts()
    funds = get_all_funds()
    txs = get_all_transactions()
    total_cash = get_total_cash()
    preserved_settings = _get_all_app_settings_map()
    return {
        "version": "4.3-sqlite-acid",
        "created_at": now_tr().strftime("%Y-%m-%d %H:%M:%S"),
        "holdings": {
            "cash_try": total_cash,
            "accounts": accounts,
            "funds": funds,
        },
        "transactions": txs,
        "pending_orders": get_pending_orders(status=None),
        "recurring_rules": get_recurring_rules(),
        "price_alerts": get_price_alerts(),
        "net_worth_history": get_net_worth_history(days=0),
        "settings": preserved_settings,
    }


def import_full_backup(data: Dict[str, Any], push_cloud: bool = True, replace_history: bool = False) -> Dict[str, int]:
    """JSON yedeğini doğrular ve SQLite tablolarına atomik olarak geri yükler."""
    if not isinstance(data, dict) or "holdings" not in data or "transactions" not in data:
        raise ValueError("Geçersiz yedek dosyası formatı: holdings veya transactions eksik.")

    holdings = data.get("holdings") or {}
    accounts = holdings.get("accounts") or {}
    funds = holdings.get("funds") or {}
    txs = data.get("transactions") or []
    pending_orders = data.get("pending_orders")
    rules = data.get("recurring_rules") or []
    alerts = data.get("price_alerts") or []
    history = data.get("net_worth_history") or []
    settings_map = data.get("settings") or {}

    if not isinstance(accounts, dict) or not isinstance(funds, dict) or not isinstance(txs, list):
        raise ValueError("Yedek veri yapıları bozuk.")

    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM accounts;")
            for acc_id, acc in accounts.items():
                nid = normalize_account_id(acc.get("id") or acc_id, "5001")
                conn.execute(
                    "INSERT OR REPLACE INTO accounts (id, name, balance, bank) VALUES (?, ?, ?, ?)",
                    (nid, acc.get("name", nid), round(float(acc.get("balance", 0.0)), 2), acc.get("bank", "Ziraat")),
                )

            conn.execute("DELETE FROM funds;")
            for code, f_item in funds.items():
                fcode = str(code).upper().strip()
                conn.execute(
                    "INSERT OR REPLACE INTO funds (code, shares, buy_price, name) VALUES (?, ?, ?, ?)",
                    (fcode, round(float(f_item.get("shares", 0.0)), 4), float(f_item.get("buy_price", 0.0)), f_item.get("name", fcode)),
                )

            conn.execute("DELETE FROM transactions;")
            for t in txs:
                conn.execute(
                    """INSERT OR REPLACE INTO transactions
                    (id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        t.get("id", f"tx-{uuid.uuid4().hex[:8]}"),
                        t.get("date", now_tr().strftime("%Y-%m-%d %H:%M:%S")),
                        t.get("type", "GİRİŞ"),
                        t.get("category", "İşlem"),
                        round(float(t.get("amount_try", 0.0)), 2),
                        round(float(t.get("shares", 0.0)), 4),
                        float(t.get("price", 0.0)),
                        str(t.get("code", "TRY")).upper().strip(),
                        normalize_account_id(t.get("account_id"), "5001"),
                        normalize_account_id(t.get("to_account_id")) if t.get("to_account_id") else None,
                        t.get("description", ""),
                    ),
                )

            if isinstance(rules, list) and rules:
                conn.execute("DELETE FROM recurring_rules;")
                for r in rules:
                    conn.execute(
                        """INSERT OR REPLACE INTO recurring_rules
                        (id, title, type, category, amount_try, account_id, day_of_month, enabled, last_triggered_month, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            r.get("id", f"rec-{uuid.uuid4().hex[:8]}"),
                            r.get("title", "Sabit Akış"),
                            r.get("type", "GİRİŞ"),
                            r.get("category", "Sabit Akış"),
                            round(float(r.get("amount_try", 0.0)), 2),
                            normalize_account_id(r.get("account_id"), "5001"),
                            int(r.get("day_of_month", 1)),
                            int(r.get("enabled", 1)),
                            r.get("last_triggered_month"),
                            r.get("created_at", now_tr().strftime("%Y-%m-%d %H:%M:%S")),
                        ),
                    )

            if isinstance(alerts, list) and alerts:
                conn.execute("DELETE FROM price_alerts;")
                for al in alerts:
                    conn.execute(
                        """INSERT OR REPLACE INTO price_alerts
                        (id, code, condition, threshold, note, enabled, last_triggered_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            al.get("id", f"al-{uuid.uuid4().hex[:8]}"),
                            str(al.get("code", "TCA")).upper().strip(),
                            str(al.get("condition", "BELOW")).upper().strip(),
                            float(al.get("threshold", 0.0)),
                            al.get("note", ""),
                            int(al.get("enabled", 1)),
                            al.get("last_triggered_at"),
                            al.get("created_at", now_tr().strftime("%Y-%m-%d %H:%M:%S")),
                        ),
                    )

            if isinstance(history, list) and history:
                if replace_history:
                    conn.execute("DELETE FROM net_worth_history;")
                    existing_hist = {}
                else:
                    existing_hist = {
                        str(r["date"]): dict(r)
                        for r in conn.execute("SELECT date, total_try, cash_try, funds_try, updated_at FROM net_worth_history;").fetchall()
                        if r["date"]
                    }
                for h in history:
                    h_date = h.get("date")
                    if not h_date:
                        continue
                    h_total = round(float(h.get("total_try", 0.0)), 2)
                    h_cash = round(float(h.get("cash_try", 0.0)), 2)
                    h_funds = round(float(h.get("funds_try", 0.0)), 2)
                    h_updated = str(h.get("updated_at") or now_tr().strftime("%Y-%m-%d %H:%M:%S"))
                    prev = existing_hist.get(str(h_date))
                    if prev is not None and not replace_history:
                        prev_updated = str(prev.get("updated_at") or "")
                        if h_updated < prev_updated or (h_total == 0.0 and float(prev.get("total_try") or 0.0) > 0.0):
                            continue
                    conn.execute(
                        "INSERT OR REPLACE INTO net_worth_history (date, total_try, cash_try, funds_try, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (h_date, h_total, h_cash, h_funds, h_updated),
                    )

            if isinstance(pending_orders, list):
                conn.execute("DELETE FROM pending_orders;")
                for po in pending_orders:
                    conn.execute(
                        """INSERT OR REPLACE INTO pending_orders
                        (id, code, fund_name, order_type, amount_try, estimated_shares, estimated_price, account_id, status, expected_settlement, note, created_at, settled_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            po.get("id", f"ord-{uuid.uuid4().hex[:8]}"),
                            str(po.get("code", "TCA")).upper().strip(),
                            po.get("fund_name", ""),
                            str(po.get("order_type", "ALIM")).upper().strip(),
                            round(float(po.get("amount_try", 0.0)), 2),
                            round(float(po.get("estimated_shares", 0.0)), 4),
                            float(po.get("estimated_price", 0.0)),
                            normalize_account_id(po.get("account_id"), "5004"),
                            str(po.get("status", "BEKLIYOR")).upper().strip(),
                            po.get("expected_settlement", ""),
                            po.get("note", ""),
                            po.get("created_at", now_tr().strftime("%Y-%m-%d %H:%M:%S")),
                            po.get("settled_at"),
                        ),
                    )

            if isinstance(settings_map, dict):
                now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
                for sk, sv in settings_map.items():
                    if sk and sv is not None and not str(sk).startswith("_"):
                        conn.execute(
                            """INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
                            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                            (str(sk), str(sv), now_str),
                        )
            if not isinstance(settings_map, dict) or str(settings_map.get("fix_5004_kurus_dust_v1") or "") != "1":
                conn.execute("DELETE FROM app_settings WHERE key = 'fix_5004_kurus_dust_v1';")
    finally:
        conn.close()

    _ensure_unsettled_tca_in_valor()
    _zero_5004_initial_kurus_dust(push_cloud=push_cloud)
    already_reconciled_v5 = isinstance(settings_map, dict) and str(settings_map.get("reconcile_nov14_real_ledger_v5") or "") == "1"
    _reconcile_real_ziraat_ledger_nov14(push_cloud=push_cloud, force=not already_reconciled_v5)
    try:
        c_chk = get_connection()
        try:
            c_chk.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        finally:
            c_chk.close()
    except Exception:
        pass
    if push_cloud:
        schedule_cloud_sync()
    return {"accounts": len(accounts), "funds": len(funds), "transactions": len(txs)}


def _get_turso_config() -> Optional[tuple]:
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if not url or not token or requests is None:
        return None
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    endpoint = url.rstrip("/") + "/v2/pipeline"
    return endpoint, token


def _turso_pipeline(statements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    cfg = _get_turso_config()
    if not cfg:
        return None
    endpoint, token = cfg
    req_items = [{"type": "execute", "stmt": s} for s in statements]
    req_items.append({"type": "close"})
    resp = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"requests": req_items},
        timeout=6.0,
    )
    resp.raise_for_status()
    return resp.json()


def _push_snapshot_to_cloud_now():
    global _CLOUD_DIRTY
    if not _get_turso_config():
        return
    try:
        payload_json = json.dumps(export_full_backup(), ensure_ascii=False)
        now_str = now_tr().strftime("%Y-%m-%d %H:%M:%S")
        _turso_pipeline([
            {"sql": "CREATE TABLE IF NOT EXISTS cloud_snapshots (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);"},
            {
                "sql": "INSERT INTO cloud_snapshots (id, payload, updated_at) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at;",
                "args": [{"type": "text", "value": payload_json}, {"type": "text", "value": now_str}],
            },
        ])
        _CLOUD_DIRTY = False
        _CLOUD_STATUS["last_sync_at"] = now_str
        _CLOUD_STATUS["last_error"] = None
    except Exception as e:
        _CLOUD_STATUS["last_error"] = str(e)


def schedule_cloud_sync():
    global _CLOUD_TIMER, _CLOUD_DIRTY
    if not _get_turso_config():
        return
    _CLOUD_DIRTY = True
    with _CLOUD_LOCK:
        if _CLOUD_TIMER is not None:
            _CLOUD_TIMER.cancel()
        _CLOUD_TIMER = threading.Timer(0.6, _push_snapshot_to_cloud_now)
        _CLOUD_TIMER.daemon = True
        _CLOUD_TIMER.start()


def flush_cloud_sync():
    """Sunucu kapanmadan veya restart öncesinde bekleyen bulut senkronizasyonunu anında gönderir."""
    global _CLOUD_TIMER
    if not _get_turso_config():
        return
    pending = False
    with _CLOUD_LOCK:
        if _CLOUD_TIMER is not None:
            _CLOUD_TIMER.cancel()
            _CLOUD_TIMER = None
            pending = True
    if pending or _CLOUD_DIRTY:
        _push_snapshot_to_cloud_now()


def reconcile_missing_backup(data: Dict[str, Any], deleted_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Geçici sunucu yeniden başlamasında tarayıcı kasasındaki eksik işlemleri/bakiyeleri güvenle kurtarır."""
    if not isinstance(data, dict) or "transactions" not in data or "holdings" not in data:
        raise ValueError("Geçersiz kurtarma yedeği.")
    deleted_set = {str(x) for x in (deleted_ids or []) if x}
    vault_txs = [
        t for t in (data.get("transactions") or [])
        if str(t.get("id") or "") not in deleted_set and not _is_stale_nov14_tzl_tx(t)
    ]
    vault_tx_ids = {str(t.get("id") or "") for t in vault_txs if t.get("id")}

    cur_txs = get_all_transactions()
    cur_tx_ids = {str(t.get("id") or "") for t in cur_txs if t.get("id")}
    missing_txs = [t for t in vault_txs if str(t.get("id") or "") and str(t.get("id")) not in cur_tx_ids]
    deleted_present = [did for did in deleted_set if did in cur_tx_ids]

    if not missing_txs and not deleted_present:
        _reconcile_real_ziraat_ledger_nov14(push_cloud=True, force=False)
        return {"restored": False, "recovered_tx_count": 0}

    # Sunucu veritabanı vault'un eski alt kümesi ise (tipik ephemeral deploy reseti) tam yedek geri yükle
    remaining_cur_ids = cur_tx_ids - deleted_set
    if remaining_cur_ids.issubset(vault_tx_ids):
        cleaned = dict(data)
        cleaned["transactions"] = vault_txs
        counts = import_full_backup(cleaned, push_cloud=True)
        return {"restored": True, "mode": "full_snapshot", "recovered_tx_count": len(missing_txs), "counts": counts}

    # Aksi halde eksik işlemleri kronolojik sırayla ekle, silinenleri temizle ve nakdi defterden hesapla
    missing_sorted = sorted(missing_txs, key=lambda x: str(x.get("date") or ""))
    conn = get_connection()
    try:
        with conn:
            for did in deleted_present:
                conn.execute("DELETE FROM transactions WHERE id = ?;", (did,))
            for t in missing_sorted:
                tid = str(t.get("id"))
                tx_obj = {
                    "id": tid,
                    "date": str(t.get("date") or now_tr().strftime("%Y-%m-%d %H:%M:%S")),
                    "type": str(t.get("type") or "GİRİŞ").upper().strip(),
                    "category": str(t.get("category") or "İşlem"),
                    "amount_try": round(float(t.get("amount_try", 0.0)), 2),
                    "shares": round(float(t.get("shares", 0.0)), 4),
                    "price": float(t.get("price", 0.0)),
                    "code": str(t.get("code") or "TRY").upper().strip(),
                    "account_id": normalize_account_id(t.get("account_id"), "5001"),
                    "to_account_id": normalize_account_id(t.get("to_account_id")) if t.get("to_account_id") else None,
                    "description": str(t.get("description") or ""),
                }
                conn.execute(
                    """INSERT OR IGNORE INTO transactions
                    (id, date, type, category, amount_try, shares, price, code, account_id, to_account_id, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tx_obj["id"], tx_obj["date"], tx_obj["type"], tx_obj["category"],
                        tx_obj["amount_try"], tx_obj["shares"], tx_obj["price"], tx_obj["code"],
                        tx_obj["account_id"], tx_obj["to_account_id"], tx_obj["description"],
                    ),
                )
                _apply_tx_effects_in_conn(conn, tx_obj)
            for h in (data.get("net_worth_history") or []):
                if h.get("date"):
                    conn.execute(
                        "INSERT OR IGNORE INTO net_worth_history (date, total_try, cash_try, funds_try, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (
                            h["date"],
                            round(float(h.get("total_try", 0.0)), 2),
                            round(float(h.get("cash_try", 0.0)), 2),
                            round(float(h.get("funds_try", 0.0)), 2),
                            str(h.get("updated_at") or now_tr().strftime("%Y-%m-%d %H:%M:%S")),
                        ),
                    )
    finally:
        conn.close()
    _reconcile_real_ziraat_ledger_nov14(push_cloud=False, force=False)
    schedule_cloud_sync()
    return {"restored": True, "mode": "merge_missing", "recovered_tx_count": len(missing_sorted)}


atexit.register(flush_cloud_sync)


def sync_from_cloud_if_configured() -> bool:
    if not _get_turso_config():
        return False
    try:
        res = _turso_pipeline([
            {"sql": "CREATE TABLE IF NOT EXISTS cloud_snapshots (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);"},
            {"sql": "SELECT payload, updated_at FROM cloud_snapshots WHERE id = 1;"},
        ])
        rows = (((res or {}).get("results") or [None, {}])[1] or {}).get("response", {}).get("result", {}).get("rows", [])
        if not rows:
            _push_snapshot_to_cloud_now()
            return False
        payload_val = rows[0][0].get("value")
        updated_at = rows[0][1].get("value")
        if payload_val:
            parsed = json.loads(payload_val)
            import_full_backup(parsed, push_cloud=False)
            _CLOUD_STATUS["last_restore_at"] = updated_at or now_tr().strftime("%Y-%m-%d %H:%M:%S")
            _CLOUD_STATUS["last_error"] = None
            return True
    except Exception as e:
        _CLOUD_STATUS["last_error"] = str(e)
    return False


def get_persistence_status() -> Dict[str, Any]:
    turso_active = _get_turso_config() is not None
    is_render = bool(os.environ.get("RENDER"))
    return {
        "mode": "turso_cloud_sync" if turso_active else "local_sqlite",
        "turso_configured": turso_active,
        "is_render": is_render,
        "ephemeral_warning": is_render and not turso_active,
        "last_sync_at": _CLOUD_STATUS.get("last_sync_at"),
        "last_restore_at": _CLOUD_STATUS.get("last_restore_at"),
        "last_error": _CLOUD_STATUS.get("last_error"),
    }


sync_from_cloud_if_configured()
