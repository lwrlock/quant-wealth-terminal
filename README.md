# Quant Wealth Terminal (Finans Komuta Merkezi)

Full-stack personal wealth, TEFAS fund analytics, and 5-year walk-forward backtesting terminal built with **FastAPI**, **SQLite (WAL)**, **NumPy / Pandas**, **Chart.js**, **GSAP**, and a **Telegram Bot** assistant.

> **Demo / Open-Source Edition:** All personal balances, API keys, and private database files have been stripped. On first run, the app initializes a clean SQLite database (`finans.db`) with synthetic demo holdings and generates a local `security_config.json` (default demo PIN: `1234`, configurable via `MASTER_PIN`).

---

## Core Architecture & Features

- **Executive 4-Bento Command Deck & Spatial UI** (`templates/index.html`, `static/css/terminal.css`, `static/js/core-portfolio.js`)
  - Cursor-reactive spatial spotlight & 3D perspective card tilt (`requestAnimationFrame` throttled, respects `prefers-reduced-motion`).
  - Live compound overnight interest (PPF *nema*) stream & TEFAS `13:30` settlement cutoff countdown.
  - Interactive lot & basket allocator (`500` / `1K` / `4K` / `11K`) using live TEFAS unit prices.
- **5-Year Walk-Forward Alpha Hunter** (`alpha_hunter.py`)
  - Scans 27 assets (BIST 100 blue chips, US Tech/ETFs, Crypto) with `0.15%` slippage, `-2.5×ATR` hard stop, `-3.0×ATR` trailing stop, falling-knife guard, and historical edge gates (`PF >= 1.35`, causal no-lookahead verified).
  - Stale-While-Revalidate (SWR) background refresh with atomic disk cache writes.
- **Universal TEFAS Analyzer & Switch Breakeven Simulator** (`tefas_analyzer.py`)
  - Compares any TEFAS fund's 1-year return against risk-free `ZBJ` over identical dates, detects 0% withholding tax (*HSYF*) funds, and computes exact stopaj + `T+N` valör breakeven days before switching funds.
- **Monte Carlo & Benchmark Race** (`monte_carlo.py`, `app.py`, `static/js/charts-radar.js`)
  - 10,000-path portfolio projection and normalized 100-TRY benchmark race (`Portfolio` vs `ZBJ` vs `Gold` vs `USD`).
- **Security & Persistence** (`app.py`, `db.py`)
  - PBKDF2-SHA256 PIN auth, HMAC-signed session cookies, rate limiting, CSRF/same-origin guard, SQLite WAL mode, and optional Turso LibSQL cloud snapshot sync.
- **Telegram Assistant & Multi-LLM AI Council** (`telegram_bot.py`, `ai_advisor.py`)
  - Natural-language Turkish transaction parsing, `/fon <KOD>`, `/gecis <FROM> <TO>`, `/firsat`, and JSON backup export.

---

## Quickstart (Local)

```bash
pip install -r requirements.txt
cp .env.example .env  # Optional: configure MASTER_PIN, TELEGRAM_BOT_TOKEN, TURSO_*
python3 app.py
```

Open `http://127.0.0.1:8080` and unlock with demo PIN **`1234`** (or your `MASTER_PIN`).

## Running Tests

```bash
python3 -m pytest -v tests/
```

---

## Tech Stack

`Python 3.10+` · `FastAPI` · `Uvicorn` · `SQLite (WAL)` · `Turso LibSQL` · `Pandas / NumPy` · `yfinance` · `tefas-crawler` · `Tailwind CSS` · `Chart.js` · `GSAP` · `Pytest`
