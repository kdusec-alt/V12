# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta
import math
import os
import re
from typing import Dict, List

import pandas as pd
from truth_guard import today_taipei_date


def _finmind_query(dataset: str, stock_id: str, start: str, end: str | None = None) -> List[Dict[str, object]]:
    import requests
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": dataset, "data_id": stock_id, "start_date": start}
    if end:
        params["end_date"] = end
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_TOKEN")
    if token:
        params["token"] = token
    r = requests.get(url, params=params, timeout=10, headers={"User-Agent": "TINO-V12/1.1"})
    r.raise_for_status()
    js = r.json()
    data = js.get("data") if isinstance(js, dict) else None
    return data if isinstance(data, list) else []

def _safe_float(value, default=None):
    try:
        if value in (None, "", "None", "nan", "--", "-"):
            return default
        x = float(str(value).replace(",", "").replace("%", "").strip())
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default


def _fmt_twd_billion(value) -> str:
    """Format TWD amount to 億. FinMind monthly revenue is normally in thousand TWD."""
    x = _safe_float(value)
    if x is None:
        return ""
    # If value is already in 億 range, keep it. If it is raw TWD or thousand TWD, normalize.
    # TW monthly revenue in FinMind is usually thousand NTD; 100,000 thousand NTD = 1 億.
    if abs(x) > 1_000_000_000:
        b = x / 100_000_000.0
    elif abs(x) > 100_000:
        b = x / 100_000.0
    else:
        b = x
    return f"{b:,.2f}億"


def _fmt_pct(value) -> str:
    x = _safe_float(value)
    if x is None:
        return ""
    return f"{x:+.2f}%"


def _month_from_value(row: Dict[str, object]) -> str:
    for key in ("revenue_month", "month", "revenueMonth"):
        v = row.get(key)
        if v not in (None, ""):
            sv = str(v).strip()
            if re.fullmatch(r"\d{6}", sv):
                return f"{sv[:4]}/{sv[4:]}"
            if re.fullmatch(r"\d{4}[-/]\d{1,2}", sv):
                y, m = re.split(r"[-/]", sv)
                return f"{int(y):04d}/{int(m):02d}"
            if re.fullmatch(r"\d{3,4}/\d{1,2}", sv):
                y, m = sv.split("/")
                y = int(y) + 1911 if len(y) == 3 else int(y)
                return f"{y:04d}/{int(m):02d}"
    d = str(row.get("date", ""))[:10]
    if re.match(r"\d{4}-\d{2}", d):
        return d[:7].replace("-", "/")
    return "最近月"


def _parse_yahoo_tw_revenue(symbol: str) -> Dict[str, object]:
    """Parse Yahoo TW revenue page as a public cross-check source.

    This is deliberately defensive because Yahoo table labels can change. We only
    return fields when a row with month + revenue can be parsed.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        url = f"https://tw.stock.yahoo.com/quote/{symbol}/revenue"
        html = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8).text
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        # Prefer table parsing; fallback to regex scanning.
        rows = []
        try:
            tables = pd.read_html(html)
            for df in tables:
                if df is None or df.empty:
                    continue
                for _, r in df.iterrows():
                    vals = [str(x).strip() for x in r.tolist()]
                    joined = "｜".join(vals)
                    m = re.search(r"(\d{3,4})[/-](\d{1,2})", joined)
                    if not m:
                        continue
                    nums = []
                    for v in vals:
                        vv = v.replace(",", "").replace("%", "").strip()
                        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", vv):
                            nums.append(float(vv))
                    if nums:
                        rows.append((m.group(1), m.group(2), vals, nums))
        except Exception:
            rows = []
        if not rows:
            m = re.search(r"(\d{3,4})[/-](\d{1,2}).{0,80}?([0-9,]+(?:\.\d+)?)", text)
            if not m:
                return {"accepted": False, "source":"YahooRevenue", "reason":"Yahoo revenue parse empty"}
            y, mo, rev = m.group(1), m.group(2), m.group(3)
            year = int(y) + 1911 if len(y) == 3 else int(y)
            return {"accepted": True, "source":"YahooRevenue", "month":f"{year:04d}/{int(mo):02d}", "revenue":_fmt_twd_billion(float(rev.replace(',', '')))}
        y, mo, vals, nums = rows[0]
        year = int(y) + 1911 if len(y) == 3 else int(y)
        # Revenue is typically the first large number after month.
        revenue_candidates = [n for n in nums if abs(n) > 1000]
        revenue = revenue_candidates[0] if revenue_candidates else nums[0]
        pct_vals = [n for n in nums if -300 < n < 300]
        mom = _fmt_pct(pct_vals[0]) if len(pct_vals) >= 1 else ""
        yoy = _fmt_pct(pct_vals[1]) if len(pct_vals) >= 2 else ""
        return {"accepted": True, "source":"YahooRevenue", "month":f"{year:04d}/{int(mo):02d}", "revenue":_fmt_twd_billion(revenue), "mom":mom, "yoy":yoy}
    except Exception as exc:
        return {"accepted": False, "source":"YahooRevenue", "reason":f"Yahoo revenue error:{type(exc).__name__}"}


def _fetch_finmind_month_revenue(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = symbol.split(".")[0]
    try:
        end_dt = date.fromisoformat(price_date) if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else today_taipei_date()
    except Exception:
        end_dt = today_taipei_date()
    start_dt = end_dt - timedelta(days=240)
    rows = _finmind_query("TaiwanStockMonthRevenue", stock_id, start_dt.isoformat(), end_dt.isoformat())
    if not rows:
        return {"accepted": False, "source":"FinMind_MonthRevenue", "reason":"FinMind month revenue empty"}
    rows = sorted(rows, key=lambda r: str(r.get("date", "")))
    latest = rows[-1]
    revenue = None
    for k in ("revenue", "Revenue", "month_revenue", "monthly_revenue"):
        if latest.get(k) not in (None, ""):
            revenue = latest.get(k); break
    if revenue is None:
        return {"accepted": False, "source":"FinMind_MonthRevenue", "reason":"FinMind revenue field empty"}
    mom = latest.get("revenue_month_growth") or latest.get("mom") or latest.get("MoM") or latest.get("month_growth")
    yoy = latest.get("revenue_year_growth") or latest.get("yoy") or latest.get("YoY") or latest.get("year_growth")
    return {"accepted": True, "source":"FinMind_MonthRevenue", "month":_month_from_value(latest), "revenue":_fmt_twd_billion(revenue), "mom":_fmt_pct(mom), "yoy":_fmt_pct(yoy)}


def _fetch_finmind_eps(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = symbol.split(".")[0]
    try:
        end_dt = date.fromisoformat(price_date) if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else today_taipei_date()
    except Exception:
        end_dt = today_taipei_date()
    start_dt = end_dt - timedelta(days=520)
    for dataset in ("TaiwanStockFinancialStatements", "TaiwanStockFinancialStatement"):
        try:
            rows = _finmind_query(dataset, stock_id, start_dt.isoformat(), end_dt.isoformat())
        except Exception:
            rows = []
        if not rows:
            continue
        eps_rows = []
        for r in rows:
            blob = " ".join(str(r.get(k,"")) for k in ("type", "name", "origin_name", "account", "indicator"))
            if any(k in blob.upper() for k in ("EPS", "EARNINGS PER SHARE")) or "每股" in blob:
                val = None
                for key in ("value", "Value", "eps", "EPS"):
                    val = _safe_float(r.get(key), None)
                    if val is not None: break
                if val is not None:
                    eps_rows.append((str(r.get("date", ""))[:10], val))
        if eps_rows:
            eps_rows = sorted(eps_rows, key=lambda x: x[0])
            d, eps = eps_rows[-1]
            return {"accepted": True, "source":"FinMind_FinancialStatements", "eps":f"{eps:.2f}", "eps_date":d}
    return {"accepted": False, "source":"FinMind_FinancialStatements", "reason":"EPS field empty"}


def _revenue_num_from_text(v: str) -> float | None:
    m = re.search(r"([-+]?\d+(?:,\d{3})*(?:\.\d+)?)", str(v or ""))
    if not m: return None
    return _safe_float(m.group(1).replace(",", ""), None)


def fetch_tw_fundamental_crosscheck(symbol: str, price_date: str) -> Dict[str, object]:
    """Taiwan fundamental resolver.

    Uses FinMind monthly revenue + Yahoo revenue page as cross-check, plus FinMind
    EPS when available. Does not use news titles or memory as fundamental data.
    """
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "source":"TW_FUNDAMENTAL_OFFLINE", "reason":"offline"}
    fin = _fetch_finmind_month_revenue(symbol, price_date)
    yah = _parse_yahoo_tw_revenue(symbol)
    eps = _fetch_finmind_eps(symbol, price_date)
    candidates = [x for x in (fin, yah) if x.get("accepted") and x.get("revenue")]
    if not candidates:
        return {"accepted": False, "source":"TW_FUNDAMENTAL_CROSSCHECK", "reason":"month revenue empty from FinMind/Yahoo"}
    # Prefer FinMind, then Yahoo. Cross-check when both same month and close in value.
    primary = fin if fin.get("accepted") else candidates[0]
    cross = False
    if fin.get("accepted") and yah.get("accepted"):
        fn = _revenue_num_from_text(fin.get("revenue")); yn = _revenue_num_from_text(yah.get("revenue"))
        same_month = str(fin.get("month")) == str(yah.get("month"))
        close = bool(fn and yn and abs(fn-yn)/max(abs(fn),1.0) <= 0.03)
        cross = same_month and close
    source = "FinMind/Yahoo 月營收交叉驗證" if cross else f"{primary.get('source')}｜待交叉驗證"
    out = {
        "accepted": True,
        "cross_checked": cross,
        "month": primary.get("month") or "最近月",
        "revenue": primary.get("revenue") or "",
        "mom": primary.get("mom") or yah.get("mom") or "",
        "yoy": primary.get("yoy") or yah.get("yoy") or "",
        "eps": eps.get("eps") if eps.get("accepted") else "",
        "eps_date": eps.get("eps_date", ""),
        "source": source,
        "reason": "月營收來源：" + ",".join([x.get("source","") for x in candidates]) + ("｜EPS FinMind" if eps.get("accepted") else "｜EPS待同步"),
    }
    return out
