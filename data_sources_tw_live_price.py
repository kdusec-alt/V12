# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Dict
from zoneinfo import ZoneInfo

from truth_guard import parse_date_safe, today_taipei_date


def _code(symbol: str) -> str:
    return str(symbol).split(".")[0]


def _num(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "null", "None"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def fetch_twse_mis_live_price(symbol: str) -> Dict[str, object]:
    """Official TWSE/TPEX MIS realtime quote for Taiwan intraday price.

    Returns the same lightweight dict contract as data_sources_tw Yahoo fast
    quote helpers.  It is intentionally isolated so V12 can hotfix live price
    without touching UI, institutional, margin, fundamental, or learning flows.
    """
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "reason": "offline"}
    try:
        import requests
        import time as _time

        code = _code(symbol)
        prefix = "otc" if str(symbol).upper().endswith(".TWO") else "tse"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        }
        sess = requests.Session()
        try:
            sess.get("https://mis.twse.com.tw/stock/index.jsp", headers=headers, timeout=2)
        except Exception:
            pass
        data = sess.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
            params={"ex_ch": f"{prefix}_{code}.tw", "json": "1", "delay": "0", "_": int(_time.time() * 1000)},
            headers=headers,
            timeout=4,
        ).json()
        row = (((data or {}).get("msgArray") or [None])[0])
        if not row:
            return {"accepted": False, "reason": "twse_mis_empty"}

        last = _num(row.get("z"))
        if last is None or last <= 0:
            return {"accepted": False, "reason": "twse_mis_no_last"}
        open_ = _num(row.get("o")) or last
        high = _num(row.get("h")) or max(open_, last)
        low = _num(row.get("l")) or min(open_, last)
        prev = _num(row.get("y")) or 0.0
        lots = _num(row.get("v")) or 0.0
        volume = lots * 1000.0 if lots < 10_000_000 else lots

        d_raw = str(row.get("d") or "").strip()
        t_raw = str(row.get("t") or "").strip()
        price_date = today_taipei_date()
        raw_time = None
        if len(d_raw) == 8:
            price_date = parse_date_safe(f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}")
            if t_raw and ":" in t_raw:
                raw_time = f"{price_date} {t_raw}"

        return {
            "accepted": True,
            "source": "TWSE_MIS_Realtime" if prefix == "tse" else "TPEX_MIS_Realtime",
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "last": float(last),
            "previous_close": float(prev),
            "volume": float(volume),
            "vwap": float((high + low + last) / 3.0),
            "price_date": price_date,
            "raw_time": raw_time,
        }
    except Exception as exc:
        return {"accepted": False, "reason": f"twse_mis_error:{type(exc).__name__}"}



def fetch_google_finance_reference(symbol: str) -> Dict[str, object]:
    """Best-effort Google Finance reference quote.

    Google Finance is not a stable official API.  V12 uses this only as a
    third-source reference note; it must never override TWSE/TPEX MIS or Yahoo.
    """
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "reason": "offline"}
    try:
        import re
        import requests
        code = _code(symbol)
        exch = "TWO" if str(symbol).upper().endswith(".TWO") else "TPE"
        url = f"https://www.google.com/finance/quote/{code}:{exch}"
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).text
        # Common Google Finance visible price block: <div class="YMlKec fxKbKc">NT$74.90</div>
        m = re.search(r'class="YMlKec[^"]*"[^>]*>\s*(?:NT\$|TWD|\$)?\s*([0-9,]+(?:\.[0-9]+)?)\s*<', html)
        if not m:
            return {"accepted": False, "reason": "google_finance_no_price"}
        last = _num(m.group(1))
        if last is None or last <= 0:
            return {"accepted": False, "reason": "google_finance_invalid_price"}
        return {
            "accepted": True,
            "source": "GoogleFinance_Reference",
            "last": float(last),
            "price_date": today_taipei_date(),
            "raw_time": None,
            "reference_only": True,
        }
    except Exception as exc:
        return {"accepted": False, "reason": f"google_finance_error:{type(exc).__name__}"}
