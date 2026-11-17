#!/usr/bin/env python3
"""
투자 대가 13F 스마트머니 데이터 빌더 (SEC EDGAR 원본 파싱)

동훈님이 공유한 수페TV 영상(2026 Q3 13F)의 주장을 그대로 옮겨 적지 않고,
SEC EDGAR 13F-HR 원문(정보 테이블 XML)을 직접 내려받아 검증·구조화한다.

  - 대상: 버크셔 해서웨이, 듀케인 패밀리오피스(드러켄밀러), 퍼싱스퀘어(애크먼),
          틸 매크로(피터 틸), 시추에이셔널 어웨어니스(레오폴드), 국민연금(NPS)
  - 계산: 직전 분기 대비 신규/전량매도/증액/감축, 종목별 '주식수 변화 × Q3 가격'
          순현금흐름(가격 효과 제거), 섹터별 대가 순매수/매도 스코어,
          현금화 압력 지수(0~100)
  - 출력: `data/superinvestor_13f.json` (Agent 5가 읽는다)

주의
----
* 13F는 **분기 1회·롱 온리 미국 상장분**만 담는다. 현금·채권·해외·공매도는 안 보인다.
  그래서 '현금 비중'은 13F에 없다 — 여기서 말하는 현금화 압력은 '주식 순매도 =
  현금 확보'의 대용(proxy)이며, 지수 산식은 JSON의 `cashization.formula`에 남긴다.
* 퍼싱스퀘어는 13F-NT(보유내역 없는 통지서)만 제출해 원문 파싱이 불가하다.
  해당 기관은 영상 내용을 `source: "video"`로 표기해 참고 항목으로만 남긴다.
* value 단위가 천 달러인 제출자(예: 듀케인)가 있어 주당 내재가격 중앙값으로
  단위를 보정한다(`value_unit_factor`).

실행: python3 scripts/build_superinvestor_13f.py [--refresh]
      (네트워크 실패 시 기존 JSON을 그대로 두고 종료 코드 0)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
CACHE = os.path.join(BASE_DIR, "data_cache", "13f")
OUT_PATH = os.path.join(DATA, "superinvestor_13f.json")
UA = "Pickle Research contact@example.com"
VIDEO_URL = "https://youtu.be/QMg65Y0FpM8?si=kU6-ApoaPUJd4qcu"

# CIK / 표시명 / 벤치마크(현금화 압력 계산 시 시장 효과 제거용)
MANAGERS: List[Dict[str, Any]] = [
    {"key": "BERKSHIRE", "cik": "0001067983", "name": "버크셔 해서웨이", "pm": "워런 버핏 · 그레그 아벨",
     "bench": "SPY", "tag": "가치·현금 보존",
     "video_note": "3Q 현금 비중 +0.23%p 확대, BAC 분할매도 지속, GOOGL 추가매수 없이 홀딩(영상)"},
    {"key": "DUQUESNE", "cik": "0001536411", "name": "듀케인 패밀리오피스", "pm": "스탠리 드러켄밀러",
     "bench": "QQQ", "tag": "매크로 회전·현금화",
     "video_note": "투자 비중 -20.6% 축소·현금화 확대, 빅테크/반도체/항공주 매도(영상)"},
    {"key": "PERSHING", "cik": "0001336528", "name": "퍼싱 스퀘어", "pm": "빌 애크먼",
     "bench": "SPY", "tag": "집중 가치주",
     "video_note": "총 16개 기업·투자금액 +20% 확대, ALC 신규, NFLX +17.8%(영상 — 13F-NT로 원문 검증 불가)"},
    {"key": "THIEL", "cik": "0001562087", "name": "틸 매크로", "pm": "피터 틸",
     "bench": "QQQ", "tag": "집중 베팅",
     "video_note": "5종목 압축·SOXX 28만주 신규(현재 -10~20% 평가손), 반도체 저평가 베팅(영상)"},
    {"key": "SITUATIONAL", "cik": "0002045724", "name": "시추에이셔널 어웨어니스", "pm": "레오폴드 아센브레너",
     "bench": "QQQ", "tag": "AI 전력·메모리 집중",
     "video_note": "자산 $200억→$16억(-92%)·보유 26→9종목, 샤론AI·BE·NBIS·SNDK 집중(영상)"},
    {"key": "NPS", "cik": "0001608046", "name": "국민연금", "pm": "NPS",
     "bench": "SPY", "tag": "초대형 패시브·섹터 로테이션",
     "video_note": "미국주식 36.8%(619조원)·내년 목표 35.6%로 축소 예정, SNDK 신규·DDOG +16.1%(영상)"},
]

# 발행사명 → 티커 (13F에는 티커가 없다. 아래 사전으로만 매핑하고, 없으면 None)
TICKER_PATTERNS: List[Tuple[str, str]] = [
    (r"ALPHABET.*CL C", "GOOG"), (r"ALPHABET", "GOOGL"),
    (r"APPLE", "AAPL"), (r"MICROSOFT", "MSFT"), (r"AMAZON", "AMZN"), (r"META PLATFORMS", "META"),
    (r"NVIDIA", "NVDA"), (r"INTEL CORP", "INTC"), (r"MICRON", "MU"), (r"SANDISK", "SNDK"),
    (r"SEAGATE", "STX"), (r"WESTERN DIGITAL", "WDC"), (r"TAIWAN SEMICONDUCTOR", "TSM"),
    (r"STMICROELECTRONICS", "STM"), (r"ADVANCED MICRO DEVICES", "AMD"), (r"LAM RESEARCH", "LRCX"),
    (r"RAMBUS", "RMBS"), (r"TTM TECHNOLOGIES", "TTMI"), (r"MKS INC", "MKSI"),
    (r"ENTEGRIS", "ENTG"), (r"NAVITAS", "NVTS"), (r"ARM HOLDINGS", "ARM"), (r"QNITY", "Q"),
    (r"ISHARES.*SEMICDTR", "SOXX"),
    (r"AMERICAN EXPRESS", "AXP"), (r"BANK OF AMER", "BAC"), (r"COCA COLA", "KO"),
    (r"CHEVRON", "CVX"), (r"OCCIDENTAL", "OXY"), (r"KRAFT", "KHC"), (r"MOODYS", "MCO"),
    (r"DAVITA", "DVA"), (r"CHUBB", "CB"), (r"LENNAR", "LEN"), (r"ALLY FINL", "ALLY"),
    (r"BENDING SPOONS", None), (r"SPACE EXPLORATION", None),
    (r"NATERA", "NTRA"), (r"INSMED", "INSM"), (r"ALCON", "ALC"), (r"HUMANA", "HUM"),
    (r"NETFLIX", "NFLX"), (r"UBER", "UBER"), (r"UNITY SOFTWARE", "U"), (r"APPLOVIN", "APP"),
    (r"PALANTIR", "PLTR"), (r"REDDIT", "RDDT"), (r"CARVANA", "CVNA"),
    (r"VISTRA", "VST"), (r"VISTA ENERGY", "VISTA"), (r"AMERICAN ELEC POWER", "AEP"),
    (r"DTE ENERGY", "DTE"), (r"FIRSTENERGY", "FE"), (r"CMS ENERGY", "CMS"),
    (r"X-ENERGY", None), (r"BLOOM ENERGY", "BE"), (r"NEBIUS", "NBIS"), (r"SHARONAI", None),
    (r"ROUNDHILL ETF TRUST MEMORY", None), (r"CLEANSPARK", "CLSK"), (r"IREN", "IREN"),
    (r"BITDEER", "BTDR"), (r"FERMI INC", None), (r"COREWEAVE", "CRWV"),
    (r"CORE SCIENTIFIC", "CORZ"), (r"APPLIED DIGITAL", "APLD"), (r"RIOT PLATFORMS", "RIOT"),
    (r"AGNICO EAGLE", "AEM"), (r"BARRICK", "B"), (r"NEWMONT", "NEM"), (r"COEUR MNG", "CDE"),
    (r"FREEPORT", "FCX"), (r"SOUTHERN COPPER", "SCCO"), (r"ALCOA", "AA"),
    (r"CLEVELAND-CLIFFS", "CLF"), (r"DELTA AIR", "DAL"), (r"UNITED AIRLS", "UAL"),
    (r"EQUINIX", "EQIX"), (r"DANAHER", "DHR"), (r"THERMO FISHER", "TMO"),
    (r"BAIDU", "BIDU"), (r"SEA LTD", "SE"), (r"YPF", "YPF"),
    (r"ISHARES INC MSCI BRAZIL", "EWZ"), (r"INVESCO.*S&amp;P500 EQL WGT", "RSP"),
    (r"STATE STR SPDR.*S&amp;P 500", "SPY"), (r"ISHARES TR RUSSELL 2000", "IWM"),
    (r"HUT 8", "HUT"), (r"TILRAY", "TLRY"), (r"TEVA", "TEVA"),
    (r"CROWDSTRIKE", "CRWD"), (r"AMPHENOL", "APH"), (r"MONSTER BEVERAGE", "MNST"),
    (r"DATADOG", "DDOG"), (r"SERVICENOW", "NOW"), (r"SALESFORCE", "CRM"),
    (r"ORACLE", "ORCL"), (r"SNOWFLAKE", "SNOW"), (r"PALO ALTO", "PANW"),
    (r"ADOBE", "ADBE"), (r"INTUIT", "INTU"), (r"BROADCOM", "AVGO"),
    (r"QUALCOMM", "QCOM"), (r"TEXAS INSTRUMENTS", "TXN"), (r"APPLIED MATLS", "AMAT"),
    (r"KLA CORP", "KLAC"), (r"ASML", "ASML"), (r"MARVELL", "MRVL"),
    (r"CEREBRAS", "CBRS"), (r"MASTEC", "MTZ"), (r"FABRINET", "FN"), (r"ATI INC", "ATI"),
    (r"NVENT", "NVT"), (r"LOCKHEED", "LMT"), (r"NORTHROP", "NOC"),
    (r"EXXON", "XOM"), (r"CONOCOPHILLIPS", "COP"),
    (r"WALMART", "WMT"), (r"JOHNSON & JOHNSON", "JNJ"), (r"ELI LILLY", "LLY"),
    (r"UNITEDHEALTH", "UNH"), (r"JPMORGAN", "JPM"), (r"GOLDMAN SACHS", "GS"),
    (r"VISA", "V"), (r"MASTERCARD", "MA"), (r"COSTCO", "COST"), (r"PEPSICO", "PEP"),
    (r"PROCTER", "PG"), (r"HOME DEPOT", "HD"), (r"CATERPILLAR", "CAT"),
    (r"BOEING", "BA"), (r"GENERAL ELECTRIC", "GE"), (r"TESLA", "TSLA"),
    (r"PFIZER", "PFE"), (r"MERCK", "MRK"), (r"ABBVIE", "ABBV"), (r"AMDOCS", "DOX"),
]

SECTOR_OF: Dict[str, str] = {}
for _t in ("NVDA INTC MU SNDK STX WDC TSM STM AMD LRCX RMBS TTMI MKSI ENTG NVTS ARM SOXX".split()):
    SECTOR_OF[_t] = "SEMI_AI_HW"
for _t in ("GOOGL GOOG AAPL MSFT AMZN META NFLX".split()):
    SECTOR_OF[_t] = "BIG_TECH"
for _t in ("LEN BAC AXP CB KO KHC MCO DVA OXY CVX HUM ALLY".split()):
    SECTOR_OF[_t] = "VALUE_HOUSING_FIN"
for _t in ("AEM B NEM CDE FCX SCCO AA CLF".split()):
    SECTOR_OF[_t] = "GOLD_MATERIALS"
for _t in ("VST BE NBIS CRWV CORZ IREN APLD CLSK BTDR AEP DTE FE CMS".split()):
    SECTOR_OF[_t] = "AI_POWER_DC"
for _t in ("NTRA INSM ALC".split()):
    SECTOR_OF[_t] = "HEALTH_GROWTH"
for _t in ("U APP PLTR UBER RDDT CVNA".split()):
    SECTOR_OF[_t] = "CONSUMER_GROWTH"
for _t in ("CRWD DDOG NOW CRM ORCL SNOW PANW ADBE INTU".split()):
    SECTOR_OF[_t] = "SOFTWARE_CLOUD"

SECTOR_KO = {
    "SEMI_AI_HW": "반도체·AI 하드웨어",
    "BIG_TECH": "빅테크",
    "VALUE_HOUSING_FIN": "가치·주택·금융",
    "GOLD_MATERIALS": "금·소재(방어)",
    "AI_POWER_DC": "AI 전력·데이터센터",
    "HEALTH_GROWTH": "헬스케어 성장",
    "CONSUMER_GROWTH": "소비·플랫폼 성장",
    "SOFTWARE_CLOUD": "소프트웨어·클라우드",
    "OTHER": "기타",
    "UNMAPPED": "매핑 밖(개별주·ETF)",
}


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def _http(url: str, path: str, refresh: bool = False, sleep: float = 0.45) -> Optional[str]:
    os.makedirs(CACHE, exist_ok=True)
    if not refresh and os.path.exists(path) and os.path.getsize(path) > 200:
        try:
            return open(path, "r", encoding="utf-8", errors="ignore").read()
        except Exception:
            pass
    try:
        r = subprocess.run(["curl", "-s", "--compressed", "-A", UA,
                            "-H", "Accept: text/xml,application/xml,text/html;q=0.9,*/*;q=0.8",
                            url], capture_output=True, text=True, timeout=40)
    except Exception:
        return None
    s = r.stdout or ""
    time.sleep(sleep)
    if len(s) < 200:
        return None
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
    except Exception:
        pass
    return s


def _filings(cik: str, refresh: bool) -> Tuple[str, List[Dict[str, str]]]:
    s = _http(f"https://data.sec.gov/submissions/CIK{cik}.json", os.path.join(CACHE, f"sub_{cik}.json"), refresh)
    if not s:
        return "", []
    d = json.loads(s)
    rec = d["filings"]["recent"]
    out = []
    for form, acc, date in zip(rec["form"], rec["accessionNumber"], rec["filingDate"]):
        if form in ("13F-HR", "13F-HR/A", "13F-NT") and date[:4] >= str(datetime.now().year - 1):
            out.append({"form": form, "acc": acc, "date": date})
    return d.get("name", ""), out


def _ticker_for(issuer: str, cls: str = "") -> Optional[str]:
    up = f"{issuer or ''} {cls or ''}".upper()
    for pat, tk in TICKER_PATTERNS:
        if re.search(pat, up):
            return tk
    return None


def _parse_info_table(acc: str, cik: str, refresh: bool) -> List[Dict[str, Any]]:
    nodash = acc.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nodash}"
    idx = _http(f"{base}/{acc}-index.htm", os.path.join(CACHE, f"{acc}-index.htm"), refresh)
    if not idx:
        return []
    xmls = [h for h in re.findall(r'href="([^"]+\.xml)"', idx)
            if "primary_doc" not in h and "xsl" not in h]
    rows: List[Dict[str, Any]] = []
    for x in list(dict.fromkeys(xmls)):
        s = _http("https://www.sec.gov" + x, os.path.join(CACHE, f"{acc}-{os.path.basename(x)}"), refresh)
        if not s:
            continue
        for b in re.findall(r"<infoTable>(.*?)</infoTable>", s, re.S):
            def g(tag: str) -> Optional[str]:
                m = re.search(rf"<{tag}>(.*?)</{tag}>", b, re.S)
                return m.group(1).strip() if m else None

            def n(tag: str) -> float:
                m = re.search(rf"<{tag}>(.*?)</{tag}>", b, re.S)
                try:
                    return float(m.group(1)) if m else 0.0
                except Exception:
                    return 0.0

            rows.append({"issuer": g("nameOfIssuer") or "", "cls": g("titleOfClass") or "",
                         "cusip": g("cusip") or "", "value": n("value"),
                         "shares": n("sshPrnamt"), "putcall": g("putCall")})
    return rows


def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """같은 종목이 otherManager별로 여러 줄로 쪼개져 오므로 합산한다."""
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = f"{r['cusip']}|{r['cls']}|{r['putcall'] or ''}"
        a = agg.setdefault(k, {**r, "value": 0.0, "shares": 0.0})
        a["value"] += r["value"]
        a["shares"] += r["shares"]
    return agg


def _unit_factor(positions: Dict[str, Dict[str, Any]]) -> float:
    """제출자별 value 단위(달러/천달러) 보정 — 주당 내재가격 중앙값으로 판정."""
    implied = [p["value"] / p["shares"] for p in positions.values()
               if p["shares"] > 0 and p["value"] > 0 and not p.get("putcall")]
    if not implied:
        return 1.0
    return 1000.0 if median(implied) < 1.0 else 1.0


def _load_quarter(cik: str, filing: Dict[str, str], refresh: bool) -> Optional[Dict[str, Any]]:
    rows = _parse_info_table(filing["acc"], cik, refresh)
    if not rows:
        return None
    pos = _aggregate(rows)
    f = _unit_factor(pos)
    for p in pos.values():
        p["value"] *= f
    return {"date": filing["date"], "acc": filing["acc"], "form": filing["form"],
            "rows": len(rows), "positions": pos, "value_unit_factor": f,
            "total_value_usd": round(sum(p["value"] for p in pos.values()), 2)}


def _norm_name(issuer: str) -> str:
    """CUSIP·표기가 바뀐 같은 회사를 이어 붙이기 위한 발행사명 정규화."""
    s = re.sub(r"&AMP;", "&", (issuer or "").upper())
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    drop = {"INC", "CORP", "CO", "LTD", "PLC", "LLC", "COM", "NEW", "CL", "A", "C", "SHS",
            "HOLDINGS", "HOLDING", "HLDG", "HLDNG", "GROUP", "THE", "DEL", "SPONSORED", "ADR", "ADS",
            "SHARES", "ORD", "STK", "CAP", "PAR", "CLASS", "TR"}
    words = [w for w in s.split() if w not in drop]
    return "".join(words[:3])


def _match_positions(q3: Dict[str, Dict[str, Any]], q2: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """CUSIP 매칭 우선, 실패분은 정규화 이름으로 2차 매칭(표기·CUSIP 변경 대응).

    같은 회사의 보통주 → 콜옵션 전환(레오폴드 NBIS·BE·SNDK 등)은 '신규+전량매도'로
    부풀리지 않고 `converted`로 분리한다.
    """
    fixed: List[Tuple[str, Optional[str]]] = []
    q2_left = dict(q2)
    for k3 in q3:
        if k3 in q2_left:
            fixed.append((k3, k3))
            q2_left.pop(k3)
        else:
            fixed.append((k3, None))
    used_q2 = set()
    for i, (k3, k2) in enumerate(fixed):
        if k2 is not None:
            continue
        n3 = _norm_name(q3[k3]["issuer"])
        cand = None
        for k2b, p2 in q2_left.items():
            if k2b in used_q2:
                continue
            if _norm_name(p2["issuer"]) == n3:
                cand = k2b
                break
        if cand:
            used_q2.add(cand)
            fixed[i] = (k3, cand)
    matched_q2 = {k2 for _k3, k2 in fixed if k2}
    new, inc, red, same, conv = [], [], [], [], []
    for k3, k2 in fixed:
        p3 = q3[k3]
        if not k2:
            new.append(p3)
            continue
        p2 = q2[k2]
        diff = p3["shares"] - p2["shares"]
        px = (p3["value"] / p3["shares"]) if p3["shares"] else 0.0
        rec = {"ticker": p3.get("ticker"), "issuer": p3["issuer"], "cls": p3["cls"],
               "putcall": p3.get("putcall"), "shares_q3": p3["shares"], "shares_q2": p2["shares"],
               "d_shares": round(diff, 2),
               "d_shares_pct": round(diff / p2["shares"] * 100, 1) if p2["shares"] else None,
               "flow_usd": round(diff * px, 2), "value_q3_usd": round(p3["value"], 2)}
        if (p2.get("putcall") or "") != (p3.get("putcall") or ""):
            rec["flow_usd"] = round(p3["value"] - p2["value"], 2)
            rec["converted_from"] = p2.get("putcall") or "COMMON"
            conv.append(rec)
        elif diff > 0:
            inc.append(rec)
        elif diff < 0:
            red.append(rec)
        else:
            same.append(rec)
    exits = [{"ticker": p.get("ticker"), "issuer": p["issuer"], "cls": p["cls"],
              "putcall": p.get("putcall"), "shares_q2": p["shares"],
              "value_q2_usd": round(p["value"], 2)}
             for k2, p in q2.items() if k2 not in matched_q2]
    return {"new": new, "increases": inc, "reductions": red, "unchanged": same,
            "exits": exits, "converted": conv}


def _top_holdings(pos: Dict[str, Dict[str, Any]], total: float, n: int = 8) -> List[Dict[str, Any]]:
    rows = sorted(pos.values(), key=lambda p: -p["value"])[:n]
    return [{"ticker": p.get("ticker") or _ticker_for(p["issuer"], p.get("cls")),
             "issuer": p["issuer"][:40], "cls": p["cls"],
             "value_usd": round(p["value"], 2),
             "weight_pct": round(p["value"] / total * 100, 2) if total else None}
            for p in rows]


def _prices_kst() -> Dict[str, Any]:
    """분기 수익률(시장 효과 제거용) — 실패하면 빈 dict."""
    try:
        import yfinance as yf  # type: ignore
        out = {}
        for sym in ("SPY", "QQQ", "SMH"):
            d = yf.download(sym, period="6mo", interval="1d", progress=False, auto_adjust=False)
            c = d["Close"]
            if hasattr(c, "columns"):
                c = c.iloc[:, 0]
            c = c.dropna()
            if len(c) < 40:
                continue
            last = float(c.iloc[-1])
            # 직전 분기 말(약 3개월 전) 종가
            ref = c[c.index <= (c.index[-1] - timedelta(days=91))]
            if len(ref) == 0:
                continue
            out[sym] = {"last": round(last, 2), "quarter_return_pct": round((last / float(ref.iloc[-1]) - 1) * 100, 2)}
        return out
    except Exception:
        return {}


def build(refresh: bool = False) -> Dict[str, Any]:
    prev = {}
    try:
        with open(OUT_PATH, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        prev = {}

    investors: List[Dict[str, Any]] = []
    sources: List[Dict[str, str]] = []
    for m in MANAGERS:
        ent_name, filings = _filings(m["cik"], refresh)
        q3f = next((f for f in filings if f["date"] >= "2026-10-01"), None)
        q2f = next((f for f in filings if "2026-07-01" <= f["date"] < "2026-10-01"), None)
        rec: Dict[str, Any] = {"key": m["key"], "name": m["name"], "name_en": ent_name or m["name"],
                               "pm": m["pm"], "tag": m["tag"], "cik": m["cik"],
                               "source": "sec_edgar_13f",
                               "video_note": m.get("video_note")}
        if q3f and q3f["form"] != "13F-NT":
            q3 = _load_quarter(m["cik"], q3f, refresh)
        else:
            q3 = None
        if q2f and q2f["form"] != "13F-NT":
            q2 = _load_quarter(m["cik"], q2f, refresh) if q3 else None
        else:
            q2 = None

        if not q3:
            # 13F-NT(보유내역 미제출) — 영상 내용을 참고 항목으로만 남긴다
            prior = next((i for i in (prev.get("investors") or []) if i.get("key") == m["key"]), {})
            rec.update({
                "filing_date": (q3f or {}).get("date"),
                "accession": (q3f or {}).get("acc"),
                "form": (q3f or {}).get("form", "13F-NT"),
                "data_available": False,
                "source": "video",
                "note_ko": ("13F-NT(보유내역 없는 통지서)만 제출해 EDGAR 원문 파싱 불가 — "
                            "영상 내용을 참고값으로만 표기합니다. 수치는 Agent 5 집계에서 제외."),
                "video_reference": prior.get("video_reference"),
            })
            rec["video_note"] = m.get("video_note")
            investors.append(rec)
            continue

        ch = _match_positions({**{k: {**v, "ticker": _ticker_for(v["issuer"], v.get("cls"))} for k, v in q3["positions"].items()}},
                              q2["positions"] if q2 else {})
        total3 = q3["total_value_usd"]
        # 순현금흐름 = 신규 + 증액 − 감축 − 전량매도 + 옵션전환 순액 (가격 효과 제거)
        flow_items = ([p["value"] for p in ch["new"]]
                      + [r["flow_usd"] for r in ch["increases"]]
                      + [r["flow_usd"] for r in ch["reductions"]]
                      + [-abs(r["value_q2_usd"]) for r in ch["exits"]]
                      + [r["flow_usd"] for r in ch["converted"]])
        net_flow = round(sum(flow_items), 2)
        gross = round(sum(abs(x) for x in flow_items), 2)
        top10 = _top_holdings(q3["positions"], total3, 10)
        rec.update({
            "filing_date": q3f["date"], "accession": q3["acc"], "form": q3["form"],
            "data_available": True,
            "value_q3_usd": total3,
            "value_q2_usd": (q2 or {}).get("total_value_usd"),
            "positions_q3": len(q3["positions"]),
            "positions_q2": len(q2["positions"]) if q2 else None,
            "top_holdings": top10,
            "concentration_top10_pct": round(sum(t["weight_pct"] or 0 for t in top10), 2) if total3 else None,
            "new_positions": [{"ticker": p.get("ticker"), "issuer": p["issuer"][:40], "cls": p["cls"],
                               "putcall": p.get("putcall"), "shares": p["shares"],
                               "value_usd": round(p["value"], 2)} for p in ch["new"]],
            "full_exits": ch["exits"],
            "increases": sorted(ch["increases"], key=lambda x: -abs(x["flow_usd"]))[:10],
            "reductions": sorted(ch["reductions"], key=lambda x: -abs(x["flow_usd"]))[:10],
            "counts": {"new": len(ch["new"]), "exits": len(ch["exits"]),
                       "increased": len(ch["increases"]), "reduced": len(ch["reductions"]),
                       "unchanged": len(ch["unchanged"]), "converted": len(ch["converted"])},
            "options_conversions": [{"ticker": p.get("ticker"), "issuer": p["issuer"][:40],
                                    "converted_from": p.get("converted_from"), "putcall": p.get("putcall"),
                                    "flow_usd": p["flow_usd"]} for p in ch["converted"]],
            "net_flow_usd": net_flow,
            "gross_flow_usd": gross,
            "net_flow_pct_of_value": round(net_flow / max(total3, (q2 or {}).get("total_value_usd") or 0) * 100, 2)
                                      if total3 else None,
            "value_unit_factor": q3["value_unit_factor"],
            "stance": ("ACCUMULATE" if net_flow > 0.01 * (total3 or 1)
                       else "DE_RISK" if net_flow < -0.01 * (total3 or 1) else "HOLD"),
        })
        sources.append({"manager": m["name"], "form": q3["form"], "filing_date": q3f["date"],
                        "accession": q3["acc"],
                        "url": f"https://www.sec.gov/Archives/edgar/data/{int(m['cik'])}/{q3['acc'].replace('-','')}/{q3['acc']}-index.htm"})
        investors.append(rec)

    # ---------- 종목·섹터 컨센서스 (가격효과 제거된 주식수 흐름 기준)
    # 대형 패시브(NPS $158B)가 소형 액티브를 짓누르지 않도록, 섹터 스코어는
    # '기관별 동일가중'(각 기관 순흐름 ÷ 그 기관 평가액)으로 낸다.
    ticker_flow: Dict[str, Dict[str, Any]] = {}
    for inv in investors:
        if not inv.get("data_available"):
            continue
        # 분모는 직전분기 평가액 — 청산한 기관(레오폴드 20.2B→1.58B)의 흐름%가 왜곡되지 않게
        base = max(inv.get("value_q2_usd") or 0.0, inv.get("value_q3_usd") or 0.0)
        for side, key in (("BUY", "new_positions"), ("BUY", "increases"), ("SELL", "full_exits"),
                          ("SELL", "reductions"), ("BUY", "options_conversions")):
            for p in inv.get(key) or []:
                tk = p.get("ticker")
                flow = (p.get("value_usd") or 0.0) if key == "new_positions" else \
                       (-abs(p.get("value_q2_usd") or 0.0) if key == "full_exits" else (p.get("flow_usd") or 0.0))
                if not tk:
                    continue
                t = ticker_flow.setdefault(tk, {"ticker": tk, "sector": SECTOR_OF.get(tk, "OTHER"),
                                                "net_flow_usd": 0.0, "net_flow_pct": 0.0,
                                                "weighted": 0.0, "gross_weighted": 0.0,
                                                "buyers": [], "sellers": []})
                t["net_flow_usd"] += flow
                if base:
                    pct = flow / base * 100
                    t["net_flow_pct"] += pct
                    t["weighted"] += pct
                    t["gross_weighted"] += abs(pct)
                if flow > 0 and inv["key"] not in t["buyers"]:
                    t["buyers"].append(inv["key"])
                elif flow < 0 and inv["key"] not in t["sellers"]:
                    t["sellers"].append(inv["key"])

    n_mgr = max(1, len([i for i in investors if i.get("data_available")]))
    sectors: Dict[str, Dict[str, Any]] = {}
    for tk, t in ticker_flow.items():
        s = sectors.setdefault(t["sector"], {"sector": t["sector"], "sector_ko": SECTOR_KO.get(t["sector"], t["sector"]),
                                             "net_flow_usd": 0.0, "weighted": 0.0, "gross_weighted": 0.0,
                                             "tickers": [], "buyers": set(), "sellers": set()})
        s["net_flow_usd"] += t["net_flow_usd"]
        s["weighted"] += t["weighted"]
        s["gross_weighted"] += t["gross_weighted"]
        s["tickers"].append({"ticker": tk, "net_flow_usd": round(t["net_flow_usd"], 2),
                             "net_flow_pct_of_manager_value": round(t["net_flow_pct"], 3),
                             "buyers": sorted(set(t["buyers"])), "sellers": sorted(set(t["sellers"]))})
        s["buyers"].update(t["buyers"])
        s["sellers"].update(t["sellers"])
    sector_out = []
    for s in sectors.values():
        gross_w = s["gross_weighted"] or 1e-9
        score = round(max(-100.0, min(100.0, 100 * s["weighted"] / gross_w)), 1)
        nb, ns = len(s["buyers"]), len(s["sellers"])
        sector_out.append({
            "sector": s["sector"], "sector_ko": s["sector_ko"],
            "score": score,
            "avg_flow_pct_per_manager": round(s["weighted"] / n_mgr, 3),
            "net_flow_usd": round(s["net_flow_usd"], 2),
            "buyers": sorted(s["buyers"]), "sellers": sorted(s["sellers"]),
            "breadth": f"{nb}매수 / {ns}매도",
            "tickers": sorted(s["tickers"], key=lambda x: -x["net_flow_usd"])[:8],
        })
    # '기타(매핑 밖)'는 항상 뒤로 — 대가 컨센서스 판독은 분류된 섹터로 한다
    sector_out.sort(key=lambda x: (x["sector"] == "OTHER", -x["score"], -x["avg_flow_pct_per_manager"]))

    # ---------- 현금화 압력 지수 (0~100, 50=중립)
    # ① 흐름축: 기관별 순흐름%/평가액의 평균 — -5%p면 100(전면 현금화), +5%p면 0
    # ② 폭축  : (전량매도)/(전량매도+신규) 평균 — 매도 일색이면 100
    flows_pct, breadth_vals = [], []
    for i in investors:
        if not i.get("data_available"):
            continue
        if i.get("net_flow_pct_of_value") is not None:
            # 한 기관의 전면 청산(레오폴드 -91%)이 지수를 포화시키지 않도록 ±10%p로 자른다
            flows_pct.append(max(-10.0, min(10.0, float(i["net_flow_pct_of_value"]))))
        c = i.get("counts") or {}
        e, n = c.get("exits", 0), c.get("new", 0)
        if e + n >= 3:
            breadth_vals.append(e / (e + n))
    mean_flow = sum(flows_pct) / len(flows_pct) if flows_pct else 0.0
    flow_component = max(0.0, min(100.0, 50 - 50 * mean_flow))   # 기관평균 -1%p ≈ 100, +1%p ≈ 0
    breadth_component = max(0.0, min(100.0, 100 * (sum(breadth_vals) / len(breadth_vals)))) if breadth_vals else 50.0
    cash_index = round(0.5 * flow_component + 0.5 * breadth_component, 1)
    cum_value = sum(i.get("value_q3_usd") or 0 for i in investors if i.get("data_available"))
    cum_flow = sum(i.get("net_flow_usd") or 0 for i in investors if i.get("data_available"))
    per_mgr = [{"key": i["key"], "name": i["name"],
                "net_flow_usd": i.get("net_flow_usd"),
                "net_flow_pct_of_value": i.get("net_flow_pct_of_value"),
                "positions_delta": (i.get("positions_q3") or 0) - (i.get("positions_q2") or 0)
                if i.get("positions_q2") else None,
                "exits_vs_new": f"{(i.get('counts') or {}).get('exits', 0)} / {(i.get('counts') or {}).get('new', 0)}",
                "stance": i.get("stance")}
               for i in investors if i.get("data_available")]

    prices = _prices_kst()
    out = {
        "schema_version": 1,
        "quarter": "2026Q3",
        "as_of_kst": _now_kst(),
        "holding_as_of": "2026-09-30",
        "source": {
            "edgar_filings": sources,
            "video_url": VIDEO_URL,
            "note_ko": ("13F는 분기 1회·롱 온리 미국 상장분만 반영합니다. 현금·채권·해외 보유는 보이지 않아 "
                        "'현금화 압력'은 주식 순매도(주식수 변화 × Q3 가격)로 근사한 대용 지표입니다."),
        },
        "market_context": prices,
        "investors": investors,
        "sector_consensus": sector_out,
        "ticker_flows": sorted(ticker_flow.values(), key=lambda x: -x["net_flow_usd"])[:40],
        "cashization": {
            "index": cash_index,
            "net_flow_usd": round(cum_flow, 2),
            "total_value_usd": round(cum_value, 2),
            "net_flow_pct_of_value": round(cum_flow / cum_value * 100, 2) if cum_value else None,
            "formula": ("index = 0.5×흐름축 + 0.5×폭축 · 흐름축 = clamp(50 − 50×기관평균 순흐름%/평가액(±10%p 클립), 0, 100) "
                        "· 폭축 = 100×평균(전량매도/(전량매도+신규)) · 50=중립, 100=현금화 최대"),
            "components": {"flow": round(flow_component, 1), "breadth": round(breadth_component, 1),
                           "mean_net_flow_pct": round(mean_flow, 3), "managers_with_counts": len(breadth_vals)},
            "per_manager": per_mgr,
            "reading_ko": (
                f"원문 확인 {len(flows_pct)}인의 Q3 순주식흐름 {cum_flow/1e9:+.2f}B USD · 기관평균 "
                f"{mean_flow:+.2f}%/평가액 · 매도폭 {breadth_component:.0f} → 현금화 압력지수 {cash_index}/100 "
                f"({'현금 확보 우위' if cash_index >= 60 else '중립' if cash_index > 40 else '주식 비중 확대 우위'})"),
        },
        "nvidia_governance": {
            "note_ko": ("엔비디아 자사 포트폴리오는 13F 대상이 아니다(운용사 아님). 3Q 공시에서 확인된 것은 "
                        "SpaceX 추가 매수·INTC 일부 매도(두 종목 비중 80%+)이며, 젠슨 황 CEO 복귀 확률은 "
                        "예측시장 기준 약 10%(후임 거론: 르네 하스·이안)로 거버넌스 불확실성이 상존합니다."),
            "jensen_return_prob_pct": 10,
            "concentration_top2_note": "SpaceX + INTC 비중 80%+ (영상 기준, 13F 미제출)",
        },
        "video_claims_check": [
            {"item": "레나(LEN) 증액 배수",
             "video": "약 40배 (66만주 → 2,600만주)",
             "filing": "CL A 13,111,741주 → 26,034,436주(+98.6%), CL B 298,117주 → 553,000주",
             "verdict": "Q3 보유 2,600만주는 일치하나 직전분기 1,310만주여서 배수는 약 2.0배 — 영상의 '40배'는 과장"},
            {"item": "벤딩 스푼스 신규", "video": "2,069만주 신규",
             "filing": "BENDING SPOONS SPA 20,689,655주 $689.4M 신규",
             "verdict": "일치"},
            {"item": "드러켄밀러 비중 축소",
             "video": "투자 비중 -20.6% 축소",
             "filing": "총평가액 $5.21B → $4.47B(-14.2%), 종목수 95 → 65(-31.6%)",
             "verdict": "방향 일치, 축소 폭은 지표에 따라 -14~-32%"},
            {"item": "드러켄밀러 매수/매도 종목수", "video": "17종목 매수 vs 47종목 전량매도",
             "filing": "신규 20 / 전량매도 50 / 증액 15 / 감축 16 (Q2 95 → Q3 65 종목)",
             "verdict": "방향 일치, 건수는 원문 기준으로 20/50"},
            {"item": "금광 3종목", "video": "금 관련주 3개 매수",
             "filing": "뉴몬트(NEM) 38.0만주·배릭(B) 105.5만주·아그니코이글(AEM) 23.1만주 신규",
             "verdict": "일치"},
            {"item": "틸 5개 종목 압축", "video": "5개 전량매도·2개 신규 → 5종목",
             "filing": "Q2 8종목 → Q3 5종목, 전량매도 5(AEP·CMS·DTE·FE·X-Energy), 신규 2(SOXX 283,068주·MSFT 2,000주)",
             "verdict": "일치"},
            {"item": "레오폴드 자산 급감", "video": "2Q $200억 → 3Q $16억(-92%)",
             "filing": "20.24B → 1.58B USD, 보유 26 → 9종목",
             "verdict": "일치(-92.2%)"},
            {"item": "국민연금 DDOG", "video": "비중 +16.1%",
             "filing": "415,491 → 482,361주(+16.1%), SNDK 169,196주 신규",
             "verdict": "일치"},
            {"item": "퍼싱스퀘어", "video": "16개 기업·ALC 신규·NFLX +17.8%",
             "filing": "13F-NT(보유내역 미제출) — 원문 검증 불가",
             "verdict": "검증 불가(영상 기준 참고)"},
        ],
    }
    os.makedirs(DATA, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        out = build(refresh="--refresh" in args)
    except Exception as e:
        print(f"FAILED: {e}")
        return 1
    ok = [i for i in out["investors"] if i.get("data_available")]
    print(f"13F {out['quarter']} — 원문 {len(ok)}인 / 전체 {len(out['investors'])}인")
    for i in out["investors"]:
        if i.get("data_available"):
            print(f"  {i['name']}: {i['positions_q2']}→{i['positions_q3']}종목, "
                  f"${(i['value_q3_usd'] or 0)/1e9:.2f}B, 순흐름 {(i['net_flow_usd'] or 0)/1e6:+.0f}M ({i['stance']})")
        else:
            print(f"  {i['name']}: 원문 없음({i.get('form')}) — 영상 참고")
    print(f"  현금화 압력지수 {out['cashization']['index']}/100 · "
          f"섹터 1위 {out['sector_consensus'][0]['sector_ko'] if out['sector_consensus'] else '—'}")
    print(f"  saved {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
