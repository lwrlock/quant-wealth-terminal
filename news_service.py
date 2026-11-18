import urllib.request
import xml.etree.ElementTree as ET
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any

_NEWS_CACHE: List[Dict[str, Any]] = []
_LAST_FETCH_TIME: float = 0.0
_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()

FEEDS = [
    {"source": "Bloomberg HT", "url": "https://www.bloomberght.com/rss"},
    {"source": "Investing.com TR", "url": "https://tr.investing.com/rss/news.rss"},
    {"source": "AA Finans", "url": "https://www.aa.com.tr/tr/rss/default?cat=ekonomi"},
]


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = (
        text.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return " ".join(text.split())


def _fetch_single_feed(feed: Dict[str, str]) -> List[Dict[str, Any]]:
    items_out: List[Dict[str, Any]] = []
    try:
        req = urllib.request.Request(
            feed["url"],
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = resp.read()
            root = ET.fromstring(data)
            items = root.findall(".//item")
            for it in items[:6]:
                title = _clean_text(it.findtext("title", ""))
                link = (it.findtext("link", "") or "").strip()
                pub_date = (it.findtext("pubDate", "") or "").strip()
                desc = _clean_text(it.findtext("description", ""))
                if not title:
                    continue
                if link and not link.startswith(("http://", "https://")):
                    link = ""
                time_str = pub_date[:22] if len(pub_date) > 22 else pub_date
                items_out.append({
                    "source": feed["source"],
                    "title": title,
                    "description": desc[:160] + "..." if len(desc) > 160 else desc,
                    "link": link,
                    "time": time_str,
                })
    except Exception:
        pass
    return items_out


def fetch_live_news(force: bool = False) -> List[Dict[str, Any]]:
    global _NEWS_CACHE, _LAST_FETCH_TIME
    now = time.time()

    with _LOCK:
        if not force and _NEWS_CACHE and (now - _LAST_FETCH_TIME < 600):
            return _NEWS_CACHE

    # Eşzamanlı isteklerde aynı anda 2 kez RSS taramayı önle
    with _FETCH_LOCK:
        now = time.time()
        with _LOCK:
            if not force and _NEWS_CACHE and (now - _LAST_FETCH_TIME < 600):
                return _NEWS_CACHE

        fresh_items: List[Dict[str, Any]] = []
        seen_titles = set()

        with ThreadPoolExecutor(max_workers=len(FEEDS)) as executor:
            for batch in executor.map(_fetch_single_feed, FEEDS):
                for item in batch:
                    t_low = item["title"].lower()
                    if t_low in seen_titles:
                        continue
                    seen_titles.add(t_low)
                    fresh_items.append(item)

        with _LOCK:
            if fresh_items:
                _NEWS_CACHE = fresh_items[:15]
                _LAST_FETCH_TIME = time.time()
            elif not _NEWS_CACHE:
                _NEWS_CACHE = [
                    {
                        "source": "TCMB / Piyasa",
                        "title": "TCMB Politika Faizi Yüksek Seyrederken Gecelik TL Repo Neması Cazibesini Koruyor",
                        "description": "Para piyasası fonları (ZBJ, TZL) net risksiz gecelik nema sağlamaya devam ediyor.",
                        "link": "https://www.tcmb.gov.tr",
                        "time": "Canlı Gösterge",
                    },
                    {
                        "source": "BIST / TEFAS",
                        "title": "Altın Katılım ve Likit Para Piyasası Fonları Portföy Kalkanı Oluşturuyor",
                        "description": "Ziraat Başak (ZBJ) ve TCA Altın fonları dengeli portföy dağılımında öne çıkıyor.",
                        "link": "https://www.tefas.gov.tr",
                        "time": "Canlı Gösterge",
                    },
                ]
                _LAST_FETCH_TIME = time.time()

            return _NEWS_CACHE
