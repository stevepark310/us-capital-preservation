"""
CUBE Real-Time News & Catalyst Engine

Collects weekend and weekday headlines from public RSS endpoints plus the
Yahoo Finance news API, classifies them per ticker with a transparent lexicon,
extracts genuine analyst actions, and blends the result with a market-implied
risk score (futures / VIX / crypto / inverse-ETF ratio).

Nothing here is a stored quote: every headline keeps its source, publish time
and link, and if all endpoints fail the engine returns the previous cache with
`source_health: DEGRADED` instead of inventing copy.
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Any, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(BASE_DIR, "data", "news_feed_raw.json")
DOCS_CACHE_PATH = os.path.join(BASE_DIR, "docs", "news_feed_raw.json")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

# ---------------------------------------------------------------- sources
TICKER_FEEDS = [
    ("Yahoo Finance", "https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"),
]
GLOBAL_FEEDS = [
    ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Nasdaq Markets", "https://www.nasdaq.com/feed/rssoutbound?category=Markets"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
]
GOOGLE_QUERIES = [
    ("Google News · semiconductors", "semiconductor stocks OR TSMC OR HBM OR \"SK hynix\""),
    ("Google News · nvidia", "NVIDIA stock OR Jensen Huang OR Blackwell"),
    ("Google News · fed & rates", "Federal Reserve OR FOMC OR \"interest rates\""),
    ("Google News · tariffs & export", "semiconductor tariffs OR export controls OR China chips"),
    ("Google News · futures", "stock futures OR \"S&P 500 futures\" OR Nasdaq futures"),
    ("Google News · bitcoin", "bitcoin price OR crypto market"),
]

FEED_TICKERS = {
    "NVDA": ["NVDA"], "SOXL": ["SOXL", "SOXS"], "TQQQ": ["TQQQ", "SQQQ"],
    "TSLA": ["TSLA"], "SGOV": ["SGOV"],
}

# ---------------------------------------------------------------- lexicon
POSITIVE = {
    "surge": 1.2, "surges": 1.2, "surged": 1.2, "rally": 1.1, "rallies": 1.1, "soar": 1.2, "soars": 1.2,
    "jump": 1.0, "jumps": 1.0, "gain": 0.8, "gains": 0.8, "rise": 0.7, "rises": 0.7, "rebound": 1.0,
    "beat": 1.0, "beats": 1.0, "record": 0.9, "upgrade": 1.1, "upgrades": 1.1, "raises": 0.9,
    "top pick": 1.2, "outperform": 1.0, "overweight": 0.8, "buy rating": 1.0, "strong": 0.7,
    "optimism": 0.8, "deal": 0.8, "approval": 0.9, "breakthrough": 1.1, "demand": 0.6,
    "growth": 0.6, "profit": 0.6, "eases": 0.6, "softer": 0.5, "dovish": 0.9, "rate cut": 1.0,
    "all-time high": 1.2, "expansion": 0.6, "recovery": 0.7, "bullish": 1.0, "buyback": 0.8,
}
NEGATIVE = {
    "plunge": 1.2, "plunges": 1.2, "plunged": 1.2, "slump": 1.1, "slumps": 1.1, "tumble": 1.1,
    "tumbles": 1.1, "fall": 0.7, "falls": 0.7, "drop": 0.8, "drops": 0.8, "sink": 0.9, "sinks": 0.9,
    "miss": 1.0, "misses": 1.0, "downgrade": 1.2, "downgrades": 1.2, "cuts": 0.8, "lower": 0.6,
    "weak": 0.8, "warning": 1.0, "warns": 1.0, "probe": 0.9, "investigation": 0.9, "lawsuit": 0.9,
    "scandal": 1.1, "resign": 1.0, "resigns": 1.0, "steps down": 1.1, "tariff": 0.9, "tariffs": 0.9,
    "ban": 1.0, "bans": 1.0, "restrictions": 0.9, "export controls": 0.9, "layoffs": 0.9,
    "crash": 1.3, "selloff": 1.1, "sell-off": 1.1, "fear": 0.8, "recession": 1.1, "default": 1.0,
    "hawkish": 0.9, "rate hike": 1.1, "inflation": 0.5, "shortage": 0.6, "halt": 0.8, "bearish": 1.0,
    "underweight": 0.8, "sell rating": 1.0, "competitors pounce": 0.9, "market share loss": 1.0,
}

TICKER_KEYWORDS = {
    "NVDA": ["nvidia", "jensen huang", "blackwell", "rubin", "geforce", "cuda", "h100", "b200", "gpu maker"],
    "SOXL": ["semiconductor", "chips", "chip stocks", "sox", "philadelphia semiconductor", "tsmc", "hbm",
             "sk hynix", "micron", "asml", "applied materials", "foundry", "wafer", "amd", "broadcom",
             "dram", "nand", "memory prices"],
    "TQQQ": ["nasdaq", "big tech", "tech stocks", "apple", "microsoft", "meta platforms", "alphabet",
             "amazon", "growth stocks", "megacap"],
    "TSLA": ["tesla", "elon musk", "ev maker", "electric vehicle", "fsd", "robotaxi"],
    "SGOV": ["treasury", "t-bill", "short-term rates", "fed funds", "money market", "risk-free"],
    "SOXS": ["semiconductor selloff", "chip slump", "sox decline", "semis fall"],
    "SQQQ": ["nasdaq selloff", "tech rout", "growth selloff"],
}
MACRO_TOPICS = {
    "FED_RATES": ["federal reserve", "fomc", "powell", "rate cut", "rate hike", "treasury yield", "10-year yield"],
    "TARIFF_TRADE": ["tariff", "export control", "trade war", "sanctions", "entity list", "china chips"],
    "OIL_ENERGY": ["oil", "crude", "brent", "opec", "hormuz", "iran"],
    "CRYPTO": ["bitcoin", "ether", "crypto", "stablecoin", "etf inflow"],
    "AI_CAPEX": ["capex", "data center", "ai spending", "hyperscaler", "gpu demand"],
}

ANALYST_RE = re.compile(
    r"(upgrade[sd]?|downgrade[sd]?|raises? (?:its )?price target|price target (?:to|raised|cut)|initiates? coverage|"
    r"top pick|overweight|underweight|outperform|equal-?weight|buy rating|sell rating)", re.I)
FIRMS = ["goldman", "morgan stanley", "jpmorgan", "j.p. morgan", "bofa", "bank of america", "citi", "citigroup",
         "ubs", "barclays", "wedbush", "piper sandler", "mizuho", "evercore", "bernstein", "d.a. davidson",
         "oppenheimer", "jefferies", "rbc", "deutsche bank", "hsbc", "nomura", "baird", "needham"]

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    if not text:
        return ""
    text = TAG_RE.sub(" ", text)
    text = (text.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " "))
    return WS_RE.sub(" ", text).strip()


def _get(url: str, timeout: int = 12) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def _parse_date(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def _parse_feed(xml_text: str, source: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not xml_text:
        return items
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return items
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    nodes = root.findall(".//item") or root.findall(".//atom:entry", ns)
    for node in nodes:
        def txt(tag: str) -> str:
            el = node.find(tag)
            if el is None:
                el = node.find(f"atom:{tag}", ns)
            return (el.text or "") if el is not None else ""
        title = _clean(txt("title"))
        if not title:
            continue
        link = txt("link") or (node.find("atom:link", ns).get("href") if node.find("atom:link", ns) is not None else "")
        desc = _clean(txt("description") or txt("summary"))
        pub = _parse_date(txt("pubDate") or txt("published") or txt("updated"))
        src_el = node.find("source")
        publisher = _clean(src_el.text) if src_el is not None and src_el.text else source
        items.append({
            "title": title[:300],
            "summary": desc[:400],
            "link": link,
            "source": publisher,
            "feed": source,
            "published": pub.isoformat() if pub else None,
        })
    return items


def _score_text(text: str, weight: float = 1.0) -> float:
    low = text.lower()
    score = 0.0
    for w, v in POSITIVE.items():
        if w in low:
            score += v * weight
    for w, v in NEGATIVE.items():
        if w in low:
            score -= v * weight
    # crude negation flip: "not a recession", "no evidence of downgrade"
    if re.search(r"\b(no|not|denies|without)\b[^.]{0,20}\b(recession|downgrade|cut|ban|tariff|layoffs)\b", low):
        score += 0.8
    return score


def _tags_for(text: str) -> Dict[str, Any]:
    low = text.lower()
    tickers = [t for t, kws in TICKER_KEYWORDS.items() if any(k in low for k in kws)]
    topics = [t for t, kws in MACRO_TOPICS.items() if any(k in low for k in kws)]
    analysts = [f for f in FIRMS if f in low] if ANALYST_RE.search(low) else []
    return {"tickers": tickers, "topics": topics, "analysts": analysts,
            "is_analyst_action": bool(ANALYST_RE.search(low))}


def fetch_all(quiet: bool = True) -> Dict[str, Any]:
    t0 = time.time()
    raw_items: List[Dict[str, Any]] = []
    health: Dict[str, str] = {}

    for sym in ["NVDA", "SOXL", "TQQQ", "TSLA", "SGOV", "SOXS", "SQQQ", "QQQ"]:
        for name, tpl in TICKER_FEEDS:
            url = tpl.format(sym=sym)
            body = _get(url)
            health[f"{name}:{sym}"] = "OK" if body else "FAIL"
            raw_items.extend(_parse_feed(body or "", name))

    for name, url in GLOBAL_FEEDS:
        body = _get(url)
        health[name] = "OK" if body else "FAIL"
        raw_items.extend(_parse_feed(body or "", name))

    for name, query in GOOGLE_QUERIES:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
        body = _get(url)
        health[name] = "OK" if body else "FAIL"
        raw_items.extend(_parse_feed(body or "", name))

    # Yahoo Finance news API as a second path for single names
    try:
        import yfinance as yf
        for sym in ["NVDA", "SOXL", "TSLA", "QQQ", "BTC-USD"]:
            try:
                news = yf.Ticker(sym).news or []
            except Exception:
                news = []
            health[f"yfinance_news:{sym}"] = "OK" if news else "FAIL"
            for n in news[:10]:
                c = n.get("content", n)
                title = _clean(c.get("title") or "")
                if not title:
                    continue
                pub = c.get("pubDate") or c.get("displayTime") or ""
                link = ""
                try:
                    link = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or ""
                except Exception:
                    link = ""
                raw_items.append({
                    "title": title[:300],
                    "summary": _clean(c.get("summary") or c.get("description") or "")[:400],
                    "link": link,
                    "source": (c.get("provider") or {}).get("displayName", "Yahoo Finance") if isinstance(c.get("provider"), dict) else "Yahoo Finance",
                    "feed": f"yfinance:{sym}",
                    "published": _parse_date(pub).isoformat() if _parse_date(pub) else None,
                })
    except Exception:
        health["yfinance_news"] = "FAIL"

    now = datetime.now(timezone.utc)

    # dedupe by normalized title
    seen = set()
    items: List[Dict[str, Any]] = []
    for it in raw_items:
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        tags = _tags_for(it["title"] + " " + it["summary"])
        sent = _score_text(it["title"], 1.4) + _score_text(it["summary"], 0.7)
        try:
            pub_dt = datetime.fromisoformat(it["published"]) if it["published"] else None
        except Exception:
            pub_dt = None
        age_h = ((now - pub_dt).total_seconds() / 3600.0) if pub_dt else 48.0
        it.update(tags)
        it["sentiment"] = round(sent, 2)
        it["age_hours"] = round(max(0.0, age_h), 1)
        it["recency_weight"] = round(0.5 ** (max(0.0, age_h) / 18.0), 3)
        it["weighted_sentiment"] = round(sent * it["recency_weight"], 2)
        items.append(it)

    items.sort(key=lambda x: (x["age_hours"], -abs(x["sentiment"])))
    # keep the freshest 300 (the scoring below only uses the first 40 per ticker anyway)
    if len(items) > 300:
        fresh = items[:200]
        impactful = sorted(items, key=lambda x: -abs(x["weighted_sentiment"]))[:100]
        seen2, merged = set(), []
        for it in sorted(fresh + impactful, key=lambda x: x["age_hours"]):
            if it["title"] in seen2:
                continue
            seen2.add(it["title"])
            merged.append(it)
        items = merged
    ok_feeds = sum(1 for v in health.values() if v == "OK")
    payload = {
        "fetched_at_utc": now.isoformat(),
        "fetched_at_kst": (now + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
        "source_health": "OK" if ok_feeds >= max(3, len(health) // 2) else ("DEGRADED" if items else "FAILED"),
        "feeds_ok": ok_feeds,
        "feeds_total": len(health),
        "feed_status": health,
        "fetch_seconds": round(time.time() - t0, 1),
        "item_count": len(items),
        "items": items[:400],
    }
    if not items:
        prev = load_cached()
        if prev:
            prev = dict(prev)
            prev["source_health"] = "DEGRADED_CACHE"
            prev["fetched_at_utc"] = now.isoformat()
            return prev
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    try:
        with open(DOCS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    if not quiet:
        print(f"✅ news: {len(items)} items from {ok_feeds}/{len(health)} feeds in {payload['fetch_seconds']}s")
    return payload


def load_cached() -> Optional[Dict[str, Any]]:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def market_implied_score(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Objective risk-on/off anchor derived from live prices, not from text."""
    q = (snapshot or {}).get("quotes", {})
    def chg(s):
        return (q.get(s) or {}).get("change_pct") or 0.0
    def last(s):
        return (q.get(s) or {}).get("last") or 0.0

    vix = last("^VIX") or 20.0
    vix_chg = chg("^VIX")
    nq = chg("NQ=F")
    btc = chg("BTC-USD")
    soxl, soxs = chg("SOXL"), chg("SOXS")
    semi_ratio = (1 + soxl / 100.0) / max(1e-6, (1 + soxs / 100.0)) - 1.0

    score = 50.0
    score += max(-15, min(15, nq * 4.0))          # Nasdaq futures
    score += max(-12, min(12, -vix_chg * 1.5))    # VIX direction
    score += max(-10, min(10, (20.0 - vix) * 1.2))  # VIX level vs 20
    score += max(-8, min(8, btc * 0.8))           # crypto risk appetite
    score += max(-10, min(10, semi_ratio * 200))  # semi long/short ratio
    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "components": {"nasdaq_fut_pct": nq, "vix": vix, "vix_change_pct": vix_chg,
                       "btc_change_pct": btc, "soxl_pct": soxl, "soxs_pct": soxs,
                       "semi_long_short_spread": round(semi_ratio * 100, 2)},
        "label": "RISK_ON" if score >= 60 else ("RISK_OFF" if score <= 40 else "NEUTRAL"),
    }


def aggregate(payload: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per-ticker news score blended with the market-implied anchor."""
    items = payload.get("items", [])
    anchor = market_implied_score(snapshot)
    out: Dict[str, Dict[str, Any]] = {}
    for ticker in ["NVDA", "SOXL", "TQQQ", "TSLA", "SGOV", "SOXS", "SQQQ"]:
        rel = [it for it in items if ticker in it["tickers"]]
        # semi/broad tech headlines also carry information for the leveraged ETFs
        if ticker in ("SOXL", "SOXS") and len(rel) < 8:
            rel += [it for it in items if "SOXL" in it["tickers"] or "SOXS" in it["tickers"]]
        if ticker in ("TQQQ", "SQQQ") and len(rel) < 8:
            rel += [it for it in items if "TQQQ" in it["tickers"] or "SQQQ" in it["tickers"]]
        seen = set()
        uniq = []
        for it in rel:
            k = it["title"]
            if k in seen:
                continue
            seen.add(k)
            uniq.append(it)
        weighted = sum(it["weighted_sentiment"] for it in uniq[:40])
        news_score = 50.0 + 50.0 * (weighted / (abs(weighted) + 6.0)) if uniq else 50.0
        # leverage beta: leveraged ETFs inherit the index anchor more strongly
        beta = {"SOXL": 0.55, "SOXS": 0.55, "TQQQ": 0.60, "SQQQ": 0.60, "NVDA": 0.45, "TSLA": 0.4, "SGOV": 0.2}.get(ticker, 0.4)
        blended = news_score * (1 - beta) + anchor["score"] * beta
        # Inverse ETFs are bullish exactly when their long counterpart is bearish.
        if ticker in ("SOXS", "SQQQ"):
            blended = 100.0 - blended
        out[ticker] = {
            "ticker": ticker,
            "news_score": round(news_score, 1),
            "market_implied_score": anchor["score"],
            "sentiment_score": round(max(0.0, min(100.0, blended)), 1),
            "headline_count": len(uniq),
            "weighted_sentiment": round(weighted, 2),
            "top_headlines": [
                {"title": it["title"], "source": it["source"], "published": it["published"],
                 "link": it["link"], "sentiment": it["sentiment"]}
                for it in sorted(uniq, key=lambda x: -abs(x["weighted_sentiment"]))[:5]
            ],
            "analyst_notes": [
                {"firm": (it["analysts"][0].title() if it["analysts"] else "Wall Street"),
                 "headline": it["title"], "source": it["source"], "link": it["link"], "published": it["published"]}
                for it in uniq if it.get("is_analyst_action")
            ][:5],
        }
    return {"tickers": out, "anchor": anchor, "payload": payload}


def grade_for(score: float) -> str:
    if score >= 75:
        return "STRONG_BULLISH (강한 호재)"
    if score >= 60:
        return "MODERATE_BULLISH (우호적)"
    if score >= 45:
        return "NEUTRAL (중립)"
    if score >= 30:
        return "BEARISH (부정적)"
    return "STRONG_BEARISH (강한 악재)"


if __name__ == "__main__":
    p = fetch_all(quiet=False)
    from core import market_snapshot
    s = market_snapshot.load_snapshot()
    agg = aggregate(p, s)
    print(json.dumps({"anchor": agg["anchor"], "tickers": {k: {kk: vv for kk, vv in v.items() if kk != "top_headlines"} for k, v in agg["tickers"].items()}}, ensure_ascii=False, indent=2)[:3000])
