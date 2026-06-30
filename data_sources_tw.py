# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, datetime, time, timedelta
import hashlib
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo
import pandas as pd
from models import NewsItem, PriceFrame, TickerInfo
from truth_guard import make_truth, parse_date_safe, today_taipei_date, validate_official_block
TW_SAMPLE = {"6770.TW": dict(open=83.20, high=85.20, low=78.10, last=78.30, previous_close=83.20, volume=86000, vwap=80.53, atr14=5.99), "6586.TWO": dict(open=123.50, high=130.50, low=114.00, last=126.00, previous_close=120.50, volume=4200, vwap=123.50, atr14=9.50), "2454.TW": dict(open=4055.0, high=4145.0, low=4025.0, last=4055.0, previous_close=4100.0, volume=6800, vwap=4075.0, atr14=145.0), "2337.TW": dict(open=158.0, high=161.5, low=154.5, last=156.0, previous_close=159.0, volume=15000, vwap=157.8, atr14=7.3), "2308.TW": dict(open=1815.0, high=1855.0, low=1785.0, last=1810.0, previous_close=1835.0, volume=12200, vwap=1816.7, atr14=65.0), "00919.TW": dict(open=23.25, high=23.35, low=23.10, last=23.18, previous_close=23.22, volume=50000, vwap=23.21, atr14=0.24), "5469.TW": dict(open=90.8, high=94.0, low=90.0, last=91.8, previous_close=87.4, volume=19000, vwap=91.93, atr14=4.2)}
BULL = ["獲利", "成長", "EPS", "營收", "買超", "創高", "法說", "AI", "訂單", "擴產", "回補", "強勢", "漲"]
BEAR = ["虧損", "減損", "賣超", "下修", "衰退", "跌", "處置", "警示", "庫存", "法說虧損", "利空"]
def _code(symbol: str) -> str:
    return symbol.split(".")[0]
def _num(symbol: str) -> int:
    h = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    return int(h[:8], 16) + sum(ord(c) for c in _code(symbol))
def _series_from_sample(base: Dict[str, float], symbol: str) -> Dict[str, List[float]]:
    seed = _num(symbol) % 997
    last = float(base["last"])
    closes, highs, lows, vols = [], [], [], []
    slope = ((seed % 31) - 15) / 10000.0
    phase = (seed % 17) / 3.0
    for i in range(60, 0, -1):
        drift = math.sin(i / 4.7 + phase) * 0.018 + (i - 30) * slope
        close = max(0.01, last * (1 + drift))
        spread = max(float(base["atr14"]) * (0.25 + (seed % 7) / 50), last * 0.004)
        closes.append(round(close, 2))
        highs.append(round(close + spread, 2))
        lows.append(round(max(close - spread, 0.01), 2))
        vols.append(float(base["volume"]) * (0.65 + ((seed + i) % 40) / 100.0))
    closes[-1], highs[-1], lows[-1], vols[-1] = float(base["last"]), float(base["high"]), float(base["low"]), float(base["volume"])
    return {"closes": closes, "highs": highs, "lows": lows, "volumes": vols}
def _empty_inst(price_date: str, reason: str = "official institutional fetch failed") -> Dict[str, object]:
    # Main UI must hide this block. Admin/Trace may inspect source/reason.
    return {
        "foreign": None, "foreign_3": None, "foreign_5": None, "foreign_10": None, "foreign_streak": "",
        "trust": None, "trust_3": None, "trust_5": None, "trust_10": None, "trust_streak": "",
        "dealer": None, "dealer_3": None, "dealer_5": None, "dealer_10": None, "dealer_streak": "",
        "source": "OFFICIAL_FETCH_FAILED", "date": "", "accepted": False, "reason": reason, "hide_frontend": True,
    }
def _empty_margin(price_date: str, reason: str = "official margin fetch failed") -> Dict[str, object]:
    # Main UI must hide this block. Admin/Trace may inspect source/reason.
    return {
        "margin": None, "margin_3": None, "margin_5": None, "margin_10": None, "margin_streak": "",
        "short": None, "short_3": None, "short_5": None, "short_10": None, "short_streak": "",
        "ratio": None, "source": "OFFICIAL_FETCH_FAILED", "date": "", "accepted": False, "reason": reason, "hide_frontend": True,
    }
def _proxy_context(symbol: str, price_date: str, closes: List[float], last: float, vwap: float) -> Dict[str, object]:
    seed = _num(symbol)
    trend5 = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 and closes[-6] else 0.0
    under_vwap = last < vwap
    base = ((seed % 24000) - 12000)
    foreign = int(base * (1.25 if under_vwap else 0.55) + trend5 * 240000)
    trust = int(((seed // 7) % 7000 - 2500) + max(trend5, -0.03) * 60000)
    dealer = int(((seed // 13) % 9000 - 4500) + trend5 * 45000)
    margin = int(((seed // 19) % 16000 - 8000) + (-5500 if under_vwap else 2800))
    short = int(((seed // 29) % 3600 - 1600) + (900 if under_vwap else -600))
    ratio = max(0.05, min(18.0, abs(short) / max(abs(margin), 1) * 9.0 + (seed % 70) / 25.0))
    cover_rate = int(max(0, min(100, 62 + (-trend5 * 600) + (12 if under_vwap else -18) + (seed % 17))))
    bal3 = int(-abs(short) * (8 + seed % 13)) if cover_rate >= 60 else int(abs(short) * (5 + seed % 9))
    bal5 = int(bal3 * (1.4 + (seed % 5) / 10))
    bal10 = int(bal3 * (2.1 + (seed % 7) / 10))
    borrow3 = max(0, int(abs(short) * ((seed % 4) / 10)))
    borrow5 = max(0, int(borrow3 * 1.6))
    borrow10 = max(0, int(borrow3 * 2.4))
    return {
        "foreign": foreign, "trust": trust, "dealer": dealer, "margin": margin, "short": short, "ratio": ratio,
        "borrow_sell_3": borrow3, "borrow_sell_5": borrow5, "borrow_sell_10": borrow10,
        "balance_delta_3": bal3, "balance_delta_5": bal5, "balance_delta_10": bal10,
        "cover_rate": cover_rate, "risk": "低" if cover_rate >= 70 else ("中" if cover_rate >= 40 else "高"),
        "source": "V12_DERIVED_PROXY", "date": price_date, "accepted": False,
        "reason": "proxy only; hidden from official institutional/margin rows",
    }
def _tv_pressure_wait(price_date: str, reason: str = "待接 V9 匯率差公式") -> Dict[str, object]:
    return {
        "direction": "TV外資買賣壓",
        "amount_billion": None,
        "level": "待同步",
        "alert": "待接 V9 匯率差公式",
        "stock_fire": "待判讀",
        "confidence": 0,
        "source": "WAIT_V9_FX_DIFF_FORMULA",
        "date": price_date,
        "accepted": False,
        "reason": reason,
    }
def _latest_two_or_three_closes(symbol: str, period: str = "7d") -> Tuple[List[float], str]:
    import yfinance as yf
    hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False, timeout=8)
    if hist is None or hist.empty or "Close" not in hist:
        return [], ""
    closes = [float(x) for x in hist["Close"].dropna().tail(3)]
    d = parse_date_safe(hist.dropna(subset=["Close"]).index[-1].date().isoformat()) if closes else ""
    return closes, d
def _stock_fire_tag(last: float, prev_close: float, vwap: float, inst: Dict[str, object] | None = None) -> str:
    inst = inst or {}
    formal_inst = bool(inst.get("accepted", False))
    foreign = _to_int(inst.get("foreign")) if formal_inst else None
    price_up = last >= prev_close
    above_vwap = last >= vwap
    if price_up and above_vwap and (foreign is None or foreign >= 0):
        return "主力點火"
    if price_up and (not above_vwap or (foreign is not None and foreign < 0)):
        return "誘多出貨"
    if not above_vwap:
        return "主力熄火"
    return "主力觀察"
def _tv_pressure_context(symbol: str, price_date: str, closes: List[float], last: float, vwap: float, proxy: Dict[str, object], *, previous_close: float | None = None, inst: Dict[str, object] | None = None) -> Dict[str, object]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return _tv_pressure_wait(price_date, "offline smoke test｜待接 V9 匯率差公式")
    try:
        fx, fx_date = _latest_two_or_three_closes("USDTWD=X", "10d")
        if len(fx) < 3: fx, fx_date = _latest_two_or_three_closes("TWD=X", "10d")
        taiex, _ = _latest_two_or_three_closes("^TWII", "10d")
        if len(fx) < 3 or len(taiex) < 3:
            return _tv_pressure_wait(price_date, "匯率/大盤資料待同步｜不顯示假數字")
        fp_unit_ntd_100m = 125.0
        fp_neutral_gate = 50.0
        fp_trend_gate = 150.0
        fx_prev2, fx_prev, fx_now = fx[-3], fx[-2], fx[-1]
        tx_prev2, tx_prev, tx_now = taiex[-3], taiex[-2], taiex[-1]
        fp_base_signed = -((fx_now - fx_prev) / 0.01) * fp_unit_ntd_100m
        fp_prev_base_signed = -((fx_prev - fx_prev2) / 0.01) * fp_unit_ntd_100m
        taiex_pct = (tx_now - tx_prev) / tx_prev * 100.0 if tx_prev else 0.0
        taiex_pct_prev = (tx_prev - tx_prev2) / tx_prev2 * 100.0 if tx_prev2 else 0.0
        base_sell = fp_base_signed < -fp_neutral_gate
        prev_base_sell = fp_prev_base_signed < -fp_neutral_gate
        def boost(is_sell: bool, pct: float) -> float:
            if not is_sell:
                return 1.0
            if pct <= -2.5:
                return 4.25
            if pct <= -1.5:
                return 3.20
            if pct <= -0.8:
                return 2.10
            return 1.0
        fp_boost = boost(base_sell, taiex_pct)
        fp_prev_boost = boost(prev_base_sell, taiex_pct_prev)
        signed = fp_base_signed * fp_boost
        prev_signed = fp_prev_base_signed * fp_prev_boost
        amt = abs(signed)
        prev_amt = abs(prev_signed)
        if signed < -fp_neutral_gate:
            dir_word = "賣壓"
            direction = "預估大盤外資賣壓"
            level = "逃命" if amt >= 1000 else "高壓" if amt >= 600 else "撤退" if amt >= 300 else "警戒"
        elif signed > fp_neutral_gate:
            dir_word = "買盤"
            direction = "預估大盤外資買盤"
            level = "強回流" if amt >= 600 else "回流" if amt >= 300 else "小回補"
        else:
            dir_word = "中性"
            direction = "預估大盤外資中性"
            level = "中性"
        same_dir = (signed < -fp_neutral_gate and prev_signed < -fp_neutral_gate) or (signed > fp_neutral_gate and prev_signed > fp_neutral_gate)
        trend_delta = amt - (prev_amt if same_dir else 0.0)
        market_crash = signed < -fp_neutral_gate and fp_boost >= 2.10
        if amt < fp_neutral_gate:
            trend = ""
        elif market_crash:
            trend = "市場急殺"
        elif not same_dir:
            trend = "新訊號"
        elif trend_delta > fp_trend_gate:
            trend = "擴大" if dir_word == "買盤" else "放大"
        elif trend_delta < -fp_trend_gate:
            trend = "縮小"
        else:
            trend = "持平"
        state = level if not trend else f"{level}{trend}"
        prev_close = float(previous_close if previous_close is not None else (closes[-2] if len(closes) >= 2 else last))
        return {
            "direction": direction,
            "amount_billion": int(round(amt)) if amt >= fp_neutral_gate else "50億內",
            "level": level,
            "alert": state,
            "stock_fire": _stock_fire_tag(last, prev_close, vwap, inst),
            "confidence": 72 if amt >= fp_neutral_gate else 55,
            "source": "V9_TV_FX_DIFF_FORMULA",
            "date": fx_date or price_date,
            "accepted": True,
            "reason": f"匯率差公式｜USDTWD {fx_prev:.4f}->{fx_now:.4f}｜TAIEX {taiex_pct:+.2f}%｜boost {fp_boost:.2f}",
        }
    except Exception as exc:
        return _tv_pressure_wait(price_date, f"匯率差公式資料待同步：{type(exc).__name__}｜不顯示假數字")
def _flow_context(ticker: TickerInfo, price_date: str, closes: List[float], last: float, vwap: float, previous_close: float | None = None) -> Dict[str, object]:
    proxy = _proxy_context(ticker.resolved_symbol, price_date, closes, last, vwap)
    tv_pressure = _tv_pressure_context(ticker.resolved_symbol, price_date, closes, last, vwap, proxy, previous_close=previous_close)
    return {
        "inst": {**_empty_inst(price_date), "symbol": ticker.resolved_symbol},
        "margin": {**_empty_margin(price_date), "symbol": ticker.resolved_symbol},
        "chip_proxy": proxy,
        "tv_pressure": tv_pressure,
        "bsi": {
            "borrow_sell_3": 0, "borrow_sell_5": 0, "borrow_sell_10": 0,
            "balance_delta_3": proxy["balance_delta_3"], "balance_delta_5": proxy["balance_delta_5"], "balance_delta_10": proxy["balance_delta_10"],
            "cover_rate": proxy["cover_rate"], "risk": proxy["risk"],
            "accepted": False, "source": "WAIT_SBL", "date": price_date, "reason": "借券賣出來源未完成，主畫面以價格與資券階梯判讀",
        },
        "macro": {"accepted": False, "source": "WAIT_MACRO", "tw_gravity": None, "sox": None, "nq": None, "qqq": None, "vix": None},
        "futures": {"accepted": False, "source": "WAIT_FUTURES", "date": price_date},
        "fundamental": {"month": "最近月", "revenue": None, "mom": None, "yoy": None, "eps": None, "source": "TW_FUNDAMENTAL_PENDING", "accepted": False},
    }
V9_VERIFIED_TW_CONTEXT = {
    "6770.TW": {
        "fundamental": {"month":"2026/05","revenue":"57.70億","mom":"+14.35%","yoy":"+58.86%","accum_revenue":"243.89億","accum_yoy":"+31.41%","eps":"3.36","event_score":"+0.35","strength":"高","event_tags":"EPS 3.36、年增強、EPS 3.36、財報事件、超預期","source":"V9_VERIFIED_MOPS_EPS_MEMORY","accepted":True},
        "bsi": {"borrow_sell_3":0,"borrow_sell_5":0,"borrow_sell_10":0,"balance_delta_3":-100800,"balance_delta_5":-92800,"balance_delta_10":-53600,"cover_rate":100,"risk":"低","accepted":True,"source":"V9_VERIFIED_BSI_MEMORY","date":"2026-06-26","reason":"空方回補啟動，反彈條件改善"},
        "futures": {"accepted":True,"source":"V9_VERIFIED_TAIFEX_MEMORY","date":"2026/06/29","net_oi":-76627,"delta":-236,"settlement":"T-16 2026-07-15","risk_level":"中高50分","summary":"淨空 -76,627口｜日變化 -236口（淨空增加）｜結算壓盤風險"},
        "macro": {"accepted":True,"source":"V9_MACRO_FORWARD_CALENDAR_GUARD","date":"2026-06-29","event_score":0.35,"strength":"高","eps":"EPS 3.36","eps_tags":"年增強、EPS 3.36、財報事件、超預期","calendar":"未來72小時無一級宏觀公布｜下一個一級事件：NFP 07/02 20:30 台灣（倒數3天4小時）｜FOMC利率決議：07/30 02:00 台灣（倒數30天10小時）","sox":-5.3,"nq":0.9,"vix":18.9},
        "inst": {"foreign":-10379,"foreign_3":-15031,"foreign_5":-1674,"foreign_10":-111978,"foreign_streak":"連賣2天","trust":633,"trust_3":3135,"trust_5":3533,"trust_10":7455,"trust_streak":"連買4天","dealer":-2769,"dealer_3":-626,"dealer_5":915,"dealer_10":9958,"dealer_streak":"連賣2天","source":"FinMind_Institutional","date":"2026-06-26","accepted":True,"reason":"法人同步，使用最近有效交易日","symbol":"6770.TW"},
        "margin": {"margin":-8283,"margin_3":6036,"margin_5":2798,"margin_10":20295,"margin_streak":"連減2天","short":-342,"short_3":283,"short_5":850,"short_10":-1797,"short_streak":"連減2天","ratio":2.20,"source":"FinMind_MARGIN","date":"2026-06-26","accepted":True,"reason":"資券同步，使用最近有效交易日"}},
    "6586.TWO": {"fundamental":{"month":"最近月","revenue":"待官方更新","mom":"","yoy":"","eps":"","event_score":"+0.18","strength":"高","event_tags":"題材/事件盤","source":"V9_EVENT_MEMORY","accepted":True},"macro":{"accepted":True,"source":"V9_MACRO_FORWARD_CALENDAR_GUARD","date":"2026-06-29","event_score":0.18,"strength":"高","eps":"EPS/營收事件看深度分析","eps_tags":"題材/事件盤","calendar":"未來72小時無一級宏觀公布｜下一個一級事件：NFP 07/02 20:30 台灣｜FOMC利率決議：07/30 02:00 台灣","sox":-5.3,"nq":0.9,"vix":18.9}},
    "2308.TW": {"fundamental":{"month":"11504","revenue":"586.92億","mom":"-1.82%","yoy":"+43.92%","accum_revenue":"2,180.44億","accum_yoy":"+36.53%","eps":"","event_score":"+0.10","strength":"中","event_tags":"AI/電源/權值基本面","source":"V9_REVENUE_TRUTH_FALLBACK_MOPS_2026_04","accepted":True}}}
def _apply_v9_verified_contract(ticker: TickerInfo, context: Dict[str, object], price_date: str) -> Dict[str, object]:
    mem = V9_VERIFIED_TW_CONTEXT.get(ticker.resolved_symbol)
    if not mem:
        return context
    # Official institution/margin rows are allowed only when they are concrete rows.
    # V9 verified memory may restore real historical rows with explicit dates;
    # it must never create placeholder text on the main dashboard.
    for key, val in mem.items():
        # V20: foreign amount must come from live FX/TAIEX formula, never from fixed memory.
        if key == "foreign_amount":
            continue
        cur = context.get(key, {}) if isinstance(context.get(key, {}), dict) else {}
        if not cur.get("accepted"):
            v = dict(val)
            v.setdefault("date", price_date)
            if key in {"inst", "margin", "bsi", "futures"}:
                v = validate_official_block(v, price_date, {"inst":"三大法人","margin":"資券","bsi":"借券","futures":"外資期貨"}.get(key, key))
            context[key] = v
    return context
def _streak(values: List[int], pos: str, neg: str) -> str:
    if not values:
        return "待同步"
    up = values[-1] >= 0
    count = 1
    for v in reversed(values[:-1]):
        if (v >= 0) != up:
            break
        count += 1
    return f"連{pos if up else neg}{count}天"
def _sum_last(vals: List[int], n: int) -> int:
    return int(sum(vals[-n:])) if vals else 0
def _shares_to_lots(value: int | float | None) -> int:
    if value is None:
        return 0
    try:
        return int(round(float(value) / 1000.0))
    except Exception:
        return 0
def _tw_market_status(latest_price_date: str) -> str:
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    if now.weekday() >= 5:
        return "closed_reference"
    if now.time() < time(9, 0):
        return "pre_market"
    if now.time() <= time(13, 30):
        return "intraday"
    return "after_close"
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
    if not isinstance(data, list):
        return []
    return data


from data_sources_tw_fundamental import fetch_tw_fundamental_crosscheck

def _to_int(value) -> int | None:
    if value in (None, "", "None", "nan"):
        return None
    try:
        return int(round(float(str(value).replace(",", ""))))
    except Exception:
        return None
def _investor_key(row: Dict[str, object]) -> str | None:
    text = " ".join(str(row.get(k, "")) for k in ("name", "investor", "institutional_investors", "type", "Investor", "institutionalInvestor"))
    lower = text.lower()
    if "投信" in text or "investment" in lower or "trust" in lower:
        return "trust"
    if "外資" in text or "foreign" in lower or "qfii" in lower:
        return "foreign"
    if "自營" in text or "dealer" in lower or "proprietary" in lower:
        return "dealer"
    return None
def _institutional_net_value(row: Dict[str, object]) -> int | None:
    for key in ("buy_sell", "buySell", "buy_sell_diff", "buySellDiff", "net_buy_sell", "netBuySell"):
        v = _to_int(row.get(key))
        if v is not None:
            return v
    buy = None
    sell = None
    for key in ("buy", "Buy", "buy_volume", "buyVolume"):
        buy = _to_int(row.get(key))
        if buy is not None:
            break
    for key in ("sell", "Sell", "sell_volume", "sellVolume"):
        sell = _to_int(row.get(key))
        if sell is not None:
            break
    if buy is not None or sell is not None:
        return int((buy or 0) - (sell or 0))
    return None
def _fetch_finmind_inst(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = _code(symbol)
    try:
        end_dt = date.fromisoformat(price_date) if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else today_taipei_date()
    except Exception:
        end_dt = today_taipei_date()
    start_dt = end_dt - timedelta(days=60)
    rows: List[Dict[str, object]] = []
    errors: List[str] = []
    for dataset in ("TaiwanStockInstitutionalInvestorsBuySell", "InstitutionalInvestorsBuySell"):
        try:
            rows = _finmind_query(dataset, stock_id, start_dt.isoformat(), end_dt.isoformat())
            if rows:
                break
        except Exception as exc:
            errors.append(f"{dataset}:{type(exc).__name__}")
            rows = []
    if not rows:
        raise RuntimeError("institutional empty; " + ";".join(errors[-2:]))
    by_date: Dict[str, Dict[str, int]] = {}
    parsed = 0
    for row in rows:
        d = str(row.get("date", ""))[:10]
        if not re.match(r"\d{4}-\d{2}-\d{2}", d):
            continue
        try:
            if date.fromisoformat(d) > end_dt:
                continue
        except Exception:
            continue
        key = _investor_key(row)
        val = _institutional_net_value(row)
        if key is None or val is None:
            continue
        by_date.setdefault(d, {"foreign": 0, "trust": 0, "dealer": 0})[key] += val
        parsed += 1
    dates = sorted(by_date)[-10:]
    if not dates or parsed == 0:
        raise RuntimeError("institutional no parsed rows")
    f = [_shares_to_lots(by_date[d]["foreign"]) for d in dates]
    t = [_shares_to_lots(by_date[d]["trust"]) for d in dates]
    de = [_shares_to_lots(by_date[d]["dealer"]) for d in dates]
    if not any(f) and not any(t) and not any(de):
        raise RuntimeError("institutional parsed all zero; missing not official zero")
    return {
        "foreign": f[-1], "foreign_3": _sum_last(f, 3), "foreign_5": _sum_last(f, 5), "foreign_10": _sum_last(f, 10), "foreign_streak": _streak(f, "買", "賣"),
        "trust": t[-1], "trust_3": _sum_last(t, 3), "trust_5": _sum_last(t, 5), "trust_10": _sum_last(t, 10), "trust_streak": _streak(t, "買", "賣"),
        "dealer": de[-1], "dealer_3": _sum_last(de, 3), "dealer_5": _sum_last(de, 5), "dealer_10": _sum_last(de, 10), "dealer_streak": _streak(de, "買", "賣"),
        "source": "FinMind_Institutional", "date": dates[-1], "accepted": True,
        "reason": "法人同步｜來源 FinMind_Institutional",
        "symbol": symbol,
    }
def _fetch_finmind_margin(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = _code(symbol)
    start = (date.fromisoformat(price_date) - timedelta(days=25)).isoformat() if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else (today_taipei_date() - timedelta(days=35)).isoformat()
    rows = _finmind_query("TaiwanStockMarginPurchaseShortSale", stock_id, start, price_date if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else None)
    if not rows:
        raise RuntimeError("FinMind margin empty")
    vals = []
    for row in rows:
        d = str(row.get("date", ""))[:10]
        def get(*names):
            for name in names:
                if name in row and row[name] not in (None, ""):
                    return row[name]
            return 0
        mbuy = float(get("MarginPurchaseBuy", "margin_purchase_buy"))
        msell = float(get("MarginPurchaseSell", "margin_purchase_sell"))
        sbuy = float(get("ShortSaleBuy", "short_sale_buy"))
        ssell = float(get("ShortSaleSell", "short_sale_sell"))
        mbalance = float(get("MarginPurchaseTodayBalance", "margin_purchase_today_balance"))
        sbalance = float(get("ShortSaleTodayBalance", "short_sale_today_balance"))
        vals.append((d, int(mbuy - msell), int(ssell - sbuy), mbalance, sbalance))
    vals = vals[-10:]
    if not vals:
        raise RuntimeError("FinMind margin no parsed rows")
    dates = [x[0] for x in vals]
    m = [x[1] for x in vals]
    s = [x[2] for x in vals]
    last_margin_balance = max(vals[-1][3], 1.0)
    ratio = max(0.0, vals[-1][4] / last_margin_balance * 100.0)
    return {
        "margin": m[-1], "margin_3": _sum_last(m, 3), "margin_5": _sum_last(m, 5), "margin_10": _sum_last(m, 10), "margin_streak": _streak(m, "增", "減"),
        "short": s[-1], "short_3": _sum_last(s, 3), "short_5": _sum_last(s, 5), "short_10": _sum_last(s, 10), "short_streak": _streak(s, "增", "減"),
        "ratio": ratio, "source": "FinMind_MARGIN", "date": dates[-1], "accepted": True, "reason": "資券同步｜來源 FinMind_MARGIN",
    }
def _merge_official_context(ticker: TickerInfo, context: Dict[str, object], price_date: str, *, closes: List[float] | None = None, last: float | None = None, vwap: float | None = None, previous_close: float | None = None) -> Dict[str, object]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return context
    try:
        context["inst"] = validate_official_block(_fetch_finmind_inst(ticker.resolved_symbol, price_date), price_date, "三大法人")
    except Exception as exc:
        context["inst"] = _empty_inst(price_date, f"法人抓取失敗：{type(exc).__name__}")
    try:
        context["margin"] = validate_official_block(_fetch_finmind_margin(ticker.resolved_symbol, price_date), price_date, "資券")
    except Exception as exc:
        context["margin"] = _empty_margin(price_date, f"資券抓取失敗：{type(exc).__name__}")
    try:
        # Fundamental is an official/public-data block, not memory/news text.
        context["fundamental"] = fetch_tw_fundamental_crosscheck(ticker.resolved_symbol, price_date)
    except Exception as exc:
        context["fundamental"] = {"accepted": False, "source": "TW_FUNDAMENTAL_FETCH_ERROR", "reason": f"fundamental error:{type(exc).__name__}"}
    try:
        context["tv_pressure"] = _tv_pressure_context(
            ticker.resolved_symbol,
            price_date,
            closes or [],
            float(last if last is not None else 0.0),
            float(vwap if vwap is not None else (last if last is not None else 0.0)),
            context.get("chip_proxy", {}),
            previous_close=previous_close,
            inst=context.get("inst", {}),
        )
    except Exception:
        context["tv_pressure"] = _tv_pressure_wait(price_date, "匯率差公式資料待同步｜不顯示假數字")
    context = _apply_v9_verified_contract(ticker, context, price_date)
    return context
def _fallback_price(ticker: TickerInfo, reason: str) -> PriceFrame:
    base = TW_SAMPLE.get(ticker.resolved_symbol, dict(open=100, high=103, low=97, last=100, previous_close=100, volume=1000, vwap=100, atr14=3))
    s = _series_from_sample(base, ticker.resolved_symbol)
    d = (today_taipei_date() - timedelta(days=1)).isoformat()
    context = _flow_context(ticker, d, s["closes"], float(base["last"]), float(base["vwap"]), previous_close=float(base["previous_close"]))
    context = _apply_v9_verified_contract(ticker, context, d)
    return PriceFrame(ticker=ticker, truth=make_truth("V12_PRICE_SAMPLE_FALLBACK", d, True, True, reason, "fallback_reference"), open=float(base["open"]), high=float(base["high"]), low=float(base["low"]), last=float(base["last"]), previous_close=float(base["previous_close"]), volume=float(base["volume"]), vwap=float(base["vwap"]), atr14=float(base["atr14"]), recent_closes=s["closes"], recent_highs=s["highs"], recent_lows=s["lows"], recent_volumes=s["volumes"], price_date=d, market_status=_tw_market_status(d), context=context)
def _yahoo_chart_intraday(symbol: str) -> Dict[str, object]:
    """Fetch faster TW intraday quote from Yahoo chart API.

    yfinance daily history may lag badly during trading hours. This helper is
    intentionally small and defensive: if Yahoo chart/quote is unavailable, the
    engine falls back to the existing yfinance daily path.
    """
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "reason": "offline"}
    try:
        import requests
        from datetime import datetime as _dt
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"range": "1d", "interval": "1m", "includePrePost": "false", "events": "div,splits"}
        headers = {"User-Agent": "Mozilla/5.0"}
        data = requests.get(url, params=params, headers=headers, timeout=4).json()
        result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
        if not result:
            return {"accepted": False, "reason": "chart_empty"}
        meta = result.get("meta") or {}
        ts = result.get("timestamp") or []
        quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
        closes = [float(x) for x in quote.get("close", []) if x is not None and float(x) > 0]
        highs = [float(x) for x in quote.get("high", []) if x is not None and float(x) > 0]
        lows = [float(x) for x in quote.get("low", []) if x is not None and float(x) > 0]
        opens = [float(x) for x in quote.get("open", []) if x is not None and float(x) > 0]
        vols = [float(x or 0) for x in quote.get("volume", [])]
        if not closes:
            mp = meta.get("regularMarketPrice") or meta.get("previousClose")
            if not mp:
                return {"accepted": False, "reason": "no_close"}
            closes = [float(mp)]
        last = float(meta.get("regularMarketPrice") or closes[-1])
        open_ = float(meta.get("regularMarketOpen") or (opens[0] if opens else last))
        high = float(meta.get("regularMarketDayHigh") or (max(highs) if highs else max(open_, last)))
        low = float(meta.get("regularMarketDayLow") or (min(lows) if lows else min(open_, last)))
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        vol = float(meta.get("regularMarketVolume") or sum(vols) or 0)
        if vols and len(vols) == len(closes) and sum(vols) > 0:
            vwap = sum(c * v for c, v in zip(closes[-len(vols):], vols) if c > 0 and v > 0) / max(1.0, sum(v for v in vols if v > 0))
        else:
            vwap = (high + low + last) / 3.0
        raw_time = meta.get("regularMarketTime") or (ts[-1] if ts else None)
        if raw_time:
            dt_tw = _dt.fromtimestamp(int(raw_time), tz=ZoneInfo("Asia/Taipei"))
            price_date = parse_date_safe(dt_tw.date().isoformat())
        else:
            price_date = today_taipei_date()
        return {
            "accepted": True,
            "source": "YahooChart_1m",
            "open": open_, "high": high, "low": low, "last": last,
            "previous_close": prev, "volume": vol, "vwap": float(vwap),
            "price_date": price_date,
            "raw_time": raw_time,
        }
    except Exception as exc:
        return {"accepted": False, "reason": f"chart_error:{type(exc).__name__}"}


def _yahoo_quote_fast(symbol: str) -> Dict[str, object]:
    """Backup direct quote endpoint; usually faster than daily history."""
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "reason": "offline"}
    try:
        import requests
        from datetime import datetime as _dt
        url = "https://query1.finance.yahoo.com/v7/finance/quote"
        data = requests.get(url, params={"symbols": symbol}, headers={"User-Agent": "Mozilla/5.0"}, timeout=4).json()
        q = (((data or {}).get("quoteResponse") or {}).get("result") or [None])[0]
        if not q:
            return {"accepted": False, "reason": "quote_empty"}
        last = q.get("regularMarketPrice")
        if last is None:
            return {"accepted": False, "reason": "no_regularMarketPrice"}
        raw_time = q.get("regularMarketTime")
        price_date = today_taipei_date()
        if raw_time:
            price_date = parse_date_safe(_dt.fromtimestamp(int(raw_time), tz=ZoneInfo("Asia/Taipei")).date().isoformat())
        high = float(q.get("regularMarketDayHigh") or last)
        low = float(q.get("regularMarketDayLow") or last)
        open_ = float(q.get("regularMarketOpen") or last)
        return {
            "accepted": True,
            "source": "YahooQuote_Fast",
            "open": open_, "high": high, "low": low, "last": float(last),
            "previous_close": float(q.get("regularMarketPreviousClose") or 0),
            "volume": float(q.get("regularMarketVolume") or 0),
            "vwap": (high + low + float(last)) / 3.0,
            "price_date": price_date,
            "raw_time": raw_time,
        }
    except Exception as exc:
        return {"accepted": False, "reason": f"quote_error:{type(exc).__name__}"}


def _pick_fast_price(symbol: str) -> Dict[str, object]:
    chart = _yahoo_chart_intraday(symbol)
    if chart.get("accepted"):
        return chart
    quote = _yahoo_quote_fast(symbol)
    if quote.get("accepted"):
        return quote
    return {"accepted": False, "reason": f"chart={chart.get('reason')} quote={quote.get('reason')}"}


def fetch_tw_price(ticker: TickerInfo) -> PriceFrame:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return _fallback_price(ticker, "offline smoke test fallback")
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker.resolved_symbol).history(period="6mo", interval="1d", auto_adjust=False, timeout=8)
        if hist is None or hist.empty:
            return _fallback_price(ticker, "yfinance 無資料；只使用樣本價格")
        hist = hist.dropna(subset=["Open", "High", "Low", "Close"])
        if len(hist) < 3:
            return _fallback_price(ticker, "日 K 不足；只使用樣本價格")

        last_row, prev_row = hist.iloc[-1], hist.iloc[-2]
        daily_close = float(last_row["Close"])
        daily_prev_close = float(prev_row["Close"])

        # Fast intraday quote overrides daily OHLC when available. This fixes the
        # stale-price problem during Taiwan trading hours while keeping 6-month
        # history for ATR / model context.
        fast = _pick_fast_price(ticker.resolved_symbol)
        if fast.get("accepted"):
            close = float(fast["last"])
            high = float(fast["high"])
            low = float(fast["low"])
            open_ = float(fast["open"])
            vol = float(fast.get("volume") or last_row.get("Volume", 0) or 0)
            vwap = float(fast.get("vwap") or (high + low + close) / 3.0)
            previous_close = float(fast.get("previous_close") or daily_prev_close)
            price_date = parse_date_safe(str(fast.get("price_date") or hist.index[-1].date().isoformat()))
            source_name = fast.get("source", "YahooFast")
            freshness = "intraday_fast"
            truth_reason = "價格快速同步｜1m/quote"
        else:
            close, high, low, open_ = daily_close, float(last_row["High"]), float(last_row["Low"]), float(last_row["Open"])
            vol = float(last_row.get("Volume", 0) or 0)
            vwap = (high + low + close) / 3.0
            previous_close = daily_prev_close
            price_date = parse_date_safe(hist.index[-1].date().isoformat())
            source_name = "YahooFinance_Daily"
            freshness = "daily_fallback"
            truth_reason = f"價格日K同步｜快速報價待同步：{fast.get('reason')}"

        tr = pd.concat([(hist["High"] - hist["Low"]).abs(), (hist["High"] - hist["Close"].shift()).abs(), (hist["Low"] - hist["Close"].shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else max(close * 0.03, 0.01)

        closes = [float(x) for x in hist["Close"].tail(60)]
        highs = [float(x) for x in hist["High"].tail(60)]
        lows = [float(x) for x in hist["Low"].tail(60)]
        volumes = [float(x) for x in hist["Volume"].tail(60)]
        if closes:
            closes[-1] = close
        if highs:
            highs[-1] = max(highs[-1], high)
        if lows:
            lows[-1] = min(lows[-1], low)
        if volumes:
            volumes[-1] = max(volumes[-1], vol)

        context = _merge_official_context(
            ticker,
            _flow_context(ticker, price_date, closes, close, vwap, previous_close=previous_close),
            price_date,
            closes=closes,
            last=close,
            vwap=vwap,
            previous_close=previous_close,
        )
        return PriceFrame(
            ticker=ticker,
            truth=make_truth(str(source_name), price_date, False, True, truth_reason, freshness),
            open=open_, high=high, low=low, last=close, previous_close=previous_close,
            volume=vol, vwap=vwap, atr14=atr,
            recent_closes=closes, recent_highs=highs, recent_lows=lows, recent_volumes=volumes,
            price_date=price_date, market_status=_tw_market_status(price_date), context=context,
        )
    except Exception as exc:
        return _fallback_price(ticker, f"資料源錯誤：{type(exc).__name__}；只使用樣本價格")
def _score_news(title: str) -> Tuple[float, str]:
    t = title.lower()
    pos = sum(1 for k in BULL if k.lower() in t)
    neg = sum(1 for k in BEAR if k.lower() in t)
    score = round((pos - neg) * 0.06, 3)
    tag = "bullish_event" if score > 0.06 else ("bearish_event" if score < -0.06 else "headline_neutral")
    hit = [k for k in BULL + BEAR if k.lower() in t][:3]
    return score, "、".join(hit) if hit else tag
def _google_news(query: str, limit: int = 12) -> List[NewsItem]:
    try:
        import requests
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"})
        text = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"}).text
        root = ET.fromstring(text)
        items: List[NewsItem] = []
        seen = set()
        for item in root.findall(".//item"):
            title = re.sub(r"\s+", " ", item.findtext("title") or "").strip()
            link = item.findtext("link") or "https://news.google.com/"
            pub = item.findtext("pubDate") or ""
            if not title or title in seen:
                continue
            seen.add(title)
            score, tag = _score_news(title)
            items.append(NewsItem("GoogleNewsTW", pub, score, tag, title, link))
            if len(items) >= limit:
                break
        return items
    except Exception:
        return []
def _fallback_news(ticker: TickerInfo) -> List[NewsItem]:
    name = ticker.name
    return [
        NewsItem("GoogleNewsTW", "待同步", 0.0, "headline_neutral", f"{name} 新聞暫無即時回傳，等待資料源同步", "https://news.google.com/"),
    ]
def fetch_tw_news(ticker: TickerInfo) -> List[NewsItem]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return _fallback_news(ticker)
    queries = [f"{ticker.name} {_code(ticker.resolved_symbol)} 股票", f"{ticker.name} 法說 EPS 營收", f"{ticker.name} AI 半導體"]
    out: List[NewsItem] = []
    seen = set()
    for q in queries:
        for item in _google_news(q, 8):
            if item.title not in seen:
                out.append(item)
                seen.add(item.title)
        if len(out) >= 12:
            break
    return out[:12] or _fallback_news(ticker)
