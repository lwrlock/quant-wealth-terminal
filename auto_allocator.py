import json
import uuid
from typing import Any, Dict, List, Optional

import db
import market_service
import settlement_engine


DEFAULT_ALLOCATOR_CONFIG: Dict[str, Any] = {
    "income_try": 4000.0,
    "living_reserve_try": 1500.0,
    "living_account_id": "5001",
    "invest_account_id": "5004",
    "weights": [
        {"code": "ZBJ", "pct": 45.0, "label": "Acil Durum / Nema (PPF)"},
        {"code": "TCA", "pct": 35.0, "label": "Altın Katılım"},
        {"code": "AFT", "pct": 20.0, "label": "Yabancı Teknoloji"},
    ],
}


def get_allocator_config() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_ALLOCATOR_CONFIG))
    raw = db.get_setting("auto_allocator_config", "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                if "income_try" in parsed:
                    cfg["income_try"] = max(0.0, float(parsed["income_try"]))
                if "living_reserve_try" in parsed:
                    cfg["living_reserve_try"] = max(0.0, float(parsed["living_reserve_try"]))
                if parsed.get("living_account_id"):
                    cfg["living_account_id"] = db.normalize_account_id(parsed["living_account_id"], "5001")
                if parsed.get("invest_account_id"):
                    cfg["invest_account_id"] = db.normalize_account_id(parsed["invest_account_id"], "5004")
                if isinstance(parsed.get("weights"), list) and parsed["weights"]:
                    cfg["weights"] = parsed["weights"]
        except Exception:
            pass
    return cfg


def save_allocator_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_allocator_config()
    if "income_try" in updates:
        cfg["income_try"] = round(max(0.0, float(updates["income_try"])), 2)
    if "living_reserve_try" in updates:
        cfg["living_reserve_try"] = round(max(0.0, float(updates["living_reserve_try"])), 2)
    if updates.get("living_account_id"):
        cfg["living_account_id"] = db.normalize_account_id(updates["living_account_id"], "5001")
    if updates.get("invest_account_id"):
        cfg["invest_account_id"] = db.normalize_account_id(updates["invest_account_id"], "5004")
    if isinstance(updates.get("weights"), list) and updates["weights"]:
        cleaned = []
        for w in updates["weights"]:
            c = str(w.get("code", "")).upper().strip()
            p = max(0.0, float(w.get("pct", 0.0)))
            if c and p > 0:
                cleaned.append({"code": c, "pct": round(p, 2), "label": str(w.get("label") or c)})
        if cleaned:
            cfg["weights"] = cleaned
    db.set_setting("auto_allocator_config", json.dumps(cfg, ensure_ascii=False), sync_cloud=True)
    return cfg


def preview_allocation(
    income_try: Optional[float] = None,
    living_reserve_try: Optional[float] = None,
    weights: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    cfg = get_allocator_config()
    inc = round(max(0.0, float(income_try if income_try is not None else cfg["income_try"])), 2)
    res = round(max(0.0, min(inc, float(living_reserve_try if living_reserve_try is not None else cfg["living_reserve_try"]))), 2)
    invest_pool = round(max(0.0, inc - res), 2)

    raw_weights = weights if weights else cfg["weights"]
    total_w = sum(max(0.0, float(w.get("pct", 0.0))) for w in raw_weights) or 100.0

    mkt_funds = market_service.fetch_live_market().get("funds", {})
    now_str = market_service.now_tr().strftime("%Y-%m-%d %H:%M:%S")

    items: List[Dict[str, Any]] = []
    total_order_try = 0.0
    for w in raw_weights:
        code = str(w.get("code", "")).upper().strip()
        pct_raw = max(0.0, float(w.get("pct", 0.0)))
        if not code or pct_raw <= 0:
            continue
        norm_pct = pct_raw / total_w
        alloc_try = round(invest_pool * norm_pct, 2)
        f_info = mkt_funds.get(code, {})
        price = float(f_info.get("price") or 1.0)
        lot_step = market_service.get_fund_lot_step(code)
        raw_units = int((alloc_try + 1e-9) / price) if price > 0 else 0
        shares = (raw_units // lot_step) * lot_step
        actual_try = round(shares * price, 2) if shares > 0 else 0.0
        sched = settlement_engine.compute_valor_schedule(code, now_str)

        # Saat/fon kuralına göre valör mü anlık mı?
        needs_valor = bool(sched["after_cutoff"] or code in ("TCA", "AFT", "KZL", "TI3"))
        total_order_try += actual_try

        items.append({
            "code": code,
            "name": f_info.get("name", w.get("label") or code),
            "pct": round(norm_pct * 100.0, 1),
            "target_try": alloc_try,
            "actual_try": actual_try,
            "price": round(price, 6),
            "shares": shares,
            "leftover_krs": round(max(0.0, alloc_try - actual_try), 2),
            "needs_valor": needs_valor,
            "settlement_text": sched["expected_text"] if needs_valor else f"Anlık / {sched['settle_label']}",
        })

    return {
        "income_try": inc,
        "living_reserve_try": res,
        "invest_pool_try": invest_pool,
        "total_order_try": round(total_order_try, 2),
        "living_account_id": cfg["living_account_id"],
        "invest_account_id": cfg["invest_account_id"],
        "orders": items,
        "generated_at": now_str,
    }


def execute_allocation(
    income_try: float,
    living_reserve_try: float,
    weights: Optional[List[Dict[str, Any]]] = None,
    record_income: bool = True,
    source_note: str = "KYK / Gelir Akıllı Dağıtım",
) -> Dict[str, Any]:
    plan = preview_allocation(income_try, living_reserve_try, weights)
    if plan["income_try"] <= 0:
        raise ValueError("Dağıtılacak gelir tutarı 0'dan büyük olmalı.")

    now_str = market_service.now_tr().strftime("%Y-%m-%d %H:%M:%S")
    liv_acc = plan["living_account_id"]
    inv_acc = plan["invest_account_id"]
    accounts = db.get_all_accounts()
    funds = db.get_all_funds()

    # 1. İstenirse gelen parayı önce yaşam hesabına + yatırım havuzunu 5004'e yaz
    if record_income:
        cur_liv = float(accounts.get(liv_acc, {}).get("balance", 0.0))
        db.update_account(liv_acc, round(cur_liv + plan["living_reserve_try"], 2))
        db.insert_transaction({
            "id": f"tx-alloc-inc-{uuid.uuid4().hex[:6]}",
            "date": now_str,
            "type": "GİRİŞ",
            "category": "Gelir / KYK",
            "amount_try": plan["income_try"],
            "shares": 0.0,
            "price": 0.0,
            "code": "TRY",
            "account_id": liv_acc,
            "to_account_id": None,
            "description": f"{source_note} (Yaşam Rezervi: ₺{plan['living_reserve_try']:,.2f})",
        })
        if plan["invest_pool_try"] > 0:
            cur_inv = float(accounts.get(inv_acc, {}).get("balance", 0.0))
            db.update_account(inv_acc, round(cur_inv + plan["invest_pool_try"], 2))
            if liv_acc != inv_acc:
                db.insert_transaction({
                    "id": f"tx-alloc-vir-{uuid.uuid4().hex[:6]}",
                    "date": now_str,
                    "type": "VİRMAN",
                    "category": "Yatırım Transferi",
                    "amount_try": plan["invest_pool_try"],
                    "shares": 0.0,
                    "price": 0.0,
                    "code": "TRY",
                    "account_id": liv_acc,
                    "to_account_id": inv_acc,
                    "description": f"{source_note} -> Yatırım Havuzu",
                })

    # 2. Fon alımlarını valör kuralına göre sıraya al veya portföye yaz
    created_orders: List[Dict[str, Any]] = []
    settled_instant: List[Dict[str, Any]] = []

    for item in plan["orders"]:
        code = item["code"]
        amt = float(item["actual_try"])
        sh = float(item["shares"])
        price = float(item["price"])
        if amt <= 0 or sh <= 0:
            continue

        if item["needs_valor"]:
            po = db.create_pending_order(
                code=code,
                amount_try=amt,
                estimated_shares=sh,
                estimated_price=price,
                account_id=inv_acc,
                fund_name=item["name"],
                expected_settlement=item["settlement_text"],
                note=f"{source_note} ({code} • {item['pct']}%)",
                deduct_cash=True,
                record_tx=True,
            )
            created_orders.append(po)
        else:
            accounts = db.get_all_accounts()
            cur_inv = float(accounts.get(inv_acc, {}).get("balance", 0.0))
            db.update_account(inv_acc, max(0.0, round(cur_inv - amt, 2)))

            f_cur = funds.get(code, {})
            old_sh = float(f_cur.get("shares", 0.0))
            old_bp = float(f_cur.get("buy_price", 0.0))
            new_sh = round(old_sh + sh, 4)
            new_bp = round(((old_sh * old_bp) + (sh * price)) / new_sh, 6) if old_sh > 0 else round(price, 6)
            db.update_fund(code, new_sh, new_bp, item["name"])

            db.insert_transaction({
                "id": f"tx-alloc-{uuid.uuid4().hex[:6]}",
                "date": now_str,
                "type": "ALIM",
                "category": "Akıllı Dağıtım",
                "amount_try": amt,
                "shares": sh,
                "price": price,
                "code": code,
                "account_id": inv_acc,
                "to_account_id": None,
                "description": f"{source_note} - {code} ({int(sh)} pay)",
            })
            settled_instant.append({"code": code, "shares": sh, "amount_try": amt})

    return {
        "status": "ok",
        "plan": plan,
        "pending_orders_created": created_orders,
        "instant_buys": settled_instant,
    }
