# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta, datetime, time as dtime
import os
import re
from typing import List, Dict, Tuple
import math
import pandas as pd
from zoneinfo import ZoneInfo

from models import PriceFrame, TickerInfo, NewsItem
from truth_guard import make_truth, parse_date_safe

US_SAMPLE = {
    "ONDS": dict(open=2.42, high=2.55, low=2.31, last=2.38, previous_close=2.45, volume=3600000, vwap=2.41, atr14=0.22),
    "MRVL": dict(open=72.1, high=74.8, low=71.0, last=73.4, previous_close=71.8, volume=14500000, vwap=73.0, atr14=3.2),
    "MU": dict(open=129.0, high=135.2, low=128.1, last=133.5, previous_close=126.8, volume=32000000, vwap=132.3, atr14=5.8),
}


US_PUBLIC_MEMORY = {
    "MRVL": {
        "shortPercentOfFloat": 0.0526,
        "sharesShort": 39310000,
        "shortRatio": 0.71,
        "floatShares": 747000000,
        "longName": "Marvell Technology, Inc.",
        "sector": "Technology",
        "industry": "Semiconductors",
        "trailingEps": 2.91,
        "totalRevenue": 2420000000,
        "earningsQuarterlyGrowth": 0.0897,
        "revenueGrowth": 0.0100,
        "trailingPE": 91.67,
        "fiscalQuarterLabel": "Q1",
        "nextEarningsDate": "2026-08-27",
        "earningsDays": 59,
    },
    "MU": {
        "shortPercentOfFloat": 0.0370,
        "sharesShort": 42000000,
        "shortRatio": 1.20,
        "floatShares": 1120000000,
        "longName": "Micron Technology, Inc.",
        "sector": "Technology",
        "industry": "Semiconductors / Memory",
        "trailingEps": 44.27,
        "totalRevenue": 9542700000,
        "earningsQuarterlyGrowth": 0.7375,
        "revenueGrowth": 3.4572,
        "trailingPE": 25.58,
        "fiscalQuarterLabel": "Q3",
        "nextEarningsDate": "2026-09-23",
        "earningsDays": 86,
    },
    "ONDS": {
        "shortPercentOfFloat": 0.3329,
        "sharesShort": 41590000,
        "shortRatio": 2.06,
        "floatShares": 124900000,
        "longName": "Ondas Holdings Inc.",
        "sector": "Technology",
        "industry": "Communication Equipment / Drone / Defense",
        "trailingEps": 0.09,
        "totalRevenue": 40000000,
        "earningsQuarterlyGrowth": 0.6646,
        "revenueGrowth": 10.7990,
        "trailingPE": 87.00,
        "fiscalQuarterLabel": "Q2",
        "nextEarningsDate": "2026-08-12",
        "earningsDays": 44,
    },
}


def _merge_public_memory(symbol: str, info: Dict[str, object]) -> Dict[str, object]:
    """V9-style public memory overlay.

    Yahoo sometimes omits short float / PE / next earnings fields on Cloud. V9 kept
    verified public context instead of letting the right radar go empty. The overlay
    fills only missing/empty fields; live Yahoo values still win.
    """
    mem = US_PUBLIC_MEMORY.get(symbol.upper(), {})
    out = dict(info or {})
    for k, v in mem.items():
        if out.get(k) in (None, "", "NA"):
            out[k] = v
    return out



def _clean_num(v, default=None):
    try:
        if v in (None, '', 'NA'):
            return default
        if isinstance(v, str):
            v = v.replace(',', '').replace('%','').strip()
        x = float(v)
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default

def _fmt_source(v):
    return str(v or '').strip() or 'US_PUBLIC'

def _sector_persona(symbol: str, info: Dict[str, object], news_titles: str = '') -> Dict[str, str]:
    blob = ' '.join([symbol, str(info.get('longName','')), str(info.get('sector','')), str(info.get('industry','')), news_titles]).upper()
    # Order matters: ONDS contains "Communication Equipment"; substring "IP" inside equipment
    # must not classify it as semiconductor IP.
    if any(k in blob for k in ['DRONE','DEFENSE','AEROSPACE','UAV','UNMANNED']):
        return {
            'badge': '國防/無人機事件盤｜高波動題材｜盤中用 VWAP 驗證',
            'label': '國防/無人機事件盤',
            'bias': '事件股｜用VWAP驗證',
            'chip': '題材與訂單是主軸，Short Float 只是燃料，不是無腦追價理由。',
        }
    semicon_tokens = ['MEMORY','DRAM','NAND','HBM','MICRON','SEMICONDUCTOR','SEMICONDUCTORS','CHIP','SILICON','INTERFACE']
    is_ip = bool(re.search(r'\bIP\b', blob))
    is_ai = bool(re.search(r'\bAI\b', blob))
    if any(k in blob for k in semicon_tokens) or is_ip or (is_ai and 'TECHNOLOGY' in blob and symbol.upper() in {'MU','MRVL','NVDA','AMD','AVGO','TSM'}):
        return {
            'badge': '半導體 / 記憶體 / AI供應鏈｜盤中用 VWAP 驗證',
            'label': '半導體 / 記憶體 / AI供應鏈',
            'bias': 'AI敘事加分｜用VWAP驗證',
            'chip': '主軸是半導體、記憶體或AI供應鏈，仍以財報、VWAP與量價確認。',
        }
    return {
        'badge': '美股產業定位觀察｜盤中用 VWAP 驗證',
        'label': '美股產業定位觀察',
        'bias': '先看VWAP與正式收盤',
        'chip': '美股先看產業、財報、Short Float、VWAP，不套台股法人資券。',
    }

def _get_us_info(symbol: str) -> Dict[str, object]:
    if os.environ.get('TINO_OFFLINE_TEST') == '1':
        return _merge_public_memory(symbol, {})
    info = {}
    try:
        import yfinance as yf
        info = dict(yf.Ticker(symbol).get_info() or {})
    except Exception:
        try:
            import yfinance as yf
            info = dict(yf.Ticker(symbol).info or {})
        except Exception:
            info = {}
    return _merge_public_memory(symbol, info)

def _fetch_finviz_short(symbol: str) -> Dict[str, object]:
    if os.environ.get('TINO_OFFLINE_TEST') == '1':
        return {}
    try:
        import requests
        headers={'User-Agent':'Mozilla/5.0 TINO-V9-ShortFloat'}
        html=requests.get('https://finviz.com/quote.ashx?t='+symbol,headers=headers,timeout=8).text
        txt=re.sub(r'<[^>]+>',' ',html)
        txt=re.sub(r'\s+',' ',txt)
        out={}
        m=re.search(r'Short Float\s*/\s*Ratio\s*([0-9.]+)%\s*/\s*([0-9.]+)',txt,re.I)
        if m:
            out['short_float']=float(m.group(1)); out['short_ratio']=float(m.group(2)); out['short_source']='Finviz Short Float / Ratio'
        else:
            m=re.search(r'Short Float\s*([0-9.]+)%',txt,re.I)
            if m: out['short_float']=float(m.group(1)); out['short_source']='Finviz Short Float'
        m=re.search(r'Shs Float\s*([0-9.]+)([MB])',txt,re.I)
        if m:
            mult=1_000_000 if m.group(2).upper()=='M' else 1_000_000_000
            out['float_shares']=float(m.group(1))*mult
        return out
    except Exception:
        return {}

def _us_short_context(symbol: str, info: Dict[str, object], last: float, low: float, high: float, atr: float) -> Dict[str, object]:
    sf = _clean_num(info.get('shortPercentOfFloat'), None)
    if sf is not None and sf <= 1.5:
        sf = sf * 100.0
    shares_short = _clean_num(info.get('sharesShort'), None)
    short_ratio = _clean_num(info.get('shortRatio'), None)
    float_shares = _clean_num(info.get('floatShares'), None)
    source = 'YahooFinance quoteSummary'
    fz = _fetch_finviz_short(symbol)
    if fz.get('short_float') is not None and (sf is None or float(fz['short_float']) > float(sf) * 1.6 or sf == 0):
        sf = float(fz['short_float']); source = fz.get('short_source','Finviz Short Float')
    if short_ratio is None and fz.get('short_ratio') is not None:
        short_ratio = float(fz['short_ratio'])
    if float_shares is None and fz.get('float_shares') is not None:
        float_shares = float(fz['float_shares'])
    if shares_short is None and sf is not None and float_shares:
        shares_short = float_shares * sf / 100.0
        source = source + '｜derived sharesShort'
    # V9-like short cost zone: high short float uses higher squeeze band; normal uses VWAP/ATR band.
    sfv = float(sf or 0.0)
    if sfv >= 20:
        cost_low = max(0.01, low * 1.224)
        cost_high = max(cost_low, high * 1.56)
        trigger = cost_high
    elif sfv >= 8:
        cost_low = max(0.01, low * 1.10)
        cost_high = max(cost_low, high + atr * 2.2)
        trigger = cost_high
    else:
        cost_low = max(0.01, low * 0.666)
        cost_high = max(cost_low, last - atr * 0.10)
        trigger = high + atr * 0.65
    return {
        'accepted': sf is not None,
        'short_float': round(float(sf),2) if sf is not None else None,
        'shares_short': int(shares_short) if shares_short else None,
        'short_ratio': round(float(short_ratio),2) if short_ratio is not None else None,
        'float_shares': int(float_shares) if float_shares else None,
        'short_source': source if sf is not None else 'Yahoo/Finviz short float pending',
        'cost_low': round(cost_low,2), 'cost_high': round(cost_high,2), 'trigger': round(trigger,2),
        'source': source if sf is not None else 'US_SHORT_PENDING',
        'date': '',
    }

def _us_macro_context() -> Dict[str, object]:
    out={'accepted': False, 'source':'US_MARKET_PUBLIC', 'sox':None, 'nq':None, 'qqq':None, 'vix':None}
    if os.environ.get('TINO_OFFLINE_TEST') == '1':
        out.update({'accepted':True,'sox':-5.3,'nq':0.9,'qqq':0.9,'vix':18.0})
        return out
    try:
        import yfinance as yf
        maps={'sox':'^SOX','nq':'NQ=F','qqq':'QQQ','vix':'^VIX'}
        for k,sym in maps.items():
            h=yf.Ticker(sym).history(period='5d',interval='1d',auto_adjust=False,timeout=6).dropna(subset=['Close'])
            if len(h)>=2:
                out[k]=round((float(h['Close'].iloc[-1])-float(h['Close'].iloc[-2]))/float(h['Close'].iloc[-2])*100,2)
        out['accepted']=any(out.get(k) is not None for k in ['sox','nq','qqq','vix'])
    except Exception:
        pass
    return out


def _us_market_status_now() -> str:
    """V9-style US session router: keeps premarket/after-hours wording alive."""
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return "closed_reference"
        hm = now.hour * 60 + now.minute
        if 4 * 60 <= hm < 9 * 60 + 30:
            return "pre_market"
        if 9 * 60 + 30 <= hm < 16 * 60:
            return "intraday"
        if 16 * 60 <= hm < 20 * 60:
            return "after_hours"
        return "closed_reference"
    except Exception:
        return "closed_reference"

def _fallback_price(ticker: TickerInfo, reason: str) -> PriceFrame:
    b = US_SAMPLE.get(ticker.resolved_symbol, dict(open=50, high=52, low=48, last=50, previous_close=50, volume=1000000, vwap=50, atr14=2.5))
    last = b["last"]
    closes = [round(last * (1 + math.sin(i/5)*0.025), 2) for i in range(60,0,-1)]
    highs = [round(x + b["atr14"]*0.4, 2) for x in closes]
    lows = [round(max(x - b["atr14"]*0.4, 0.01), 2) for x in closes]
    vols = [b["volume"] * (0.7 + i/140) for i in range(60,0,-1)]
    closes[-1] = b["last"]; highs[-1] = b["high"]; lows[-1] = b["low"]; vols[-1] = b["volume"]
    d = (date.today() - timedelta(days=1)).isoformat()
    info=_get_us_info(ticker.resolved_symbol)
    short=_us_short_context(ticker.resolved_symbol, info, b["last"], b["low"], b["high"], b["atr14"])
    short["date"]=d
    persona=_sector_persona(ticker.resolved_symbol, info)
    ctx={"macro":_us_macro_context(),"short":short,"persona":persona,"fundamental":_us_fundamental_context(ticker.resolved_symbol, info, d),"inst":{"accepted":False,"source":"US","date":d},"margin":{"accepted":False,"source":"US","date":d}}
    return PriceFrame(ticker, make_truth("US_PRICE_SAMPLE", d, True, True, reason, "fallback_reference"), b["open"], b["high"], b["low"], b["last"], b["previous_close"], b["volume"], b["vwap"], b["atr14"], closes, highs, lows, vols, d, _us_market_status_now(), ctx)



def _us_fundamental_context(symbol: str, info: Dict[str, object], price_date: str) -> Dict[str, object]:
    q = info.get('fiscalQuarterLabel') or info.get('mostRecentQuarter') or info.get('lastFiscalYearEnd') or '最新財報'
    eps = _clean_num(info.get('trailingEps'), None)
    rev = _clean_num(info.get('totalRevenue'), None)
    qgrowth = _clean_num(info.get('earningsQuarterlyGrowth'), None)
    rgrowth = _clean_num(info.get('revenueGrowth'), None)
    pe = _clean_num(info.get('trailingPE'), None)
    next_date = info.get('nextEarningsDate') or info.get('earningsDate') or ''
    days = _clean_num(info.get('earningsDays'), None)
    return {
        'accepted': bool(eps is not None or rev is not None), 'source':'YahooFinance quoteSummary + V9 public memory', 'date': price_date,
        'quarter': str(q or '最新財報'), 'eps': eps, 'revenue': rev,
        'qoq': qgrowth*100 if qgrowth is not None and abs(qgrowth) < 5 else qgrowth,
        'yoy': rgrowth*100 if rgrowth is not None and abs(rgrowth) < 5 else rgrowth,
        'pe': pe, 'next_earnings': str(next_date or ''), 'earnings_days': int(days) if days is not None else None,
    }

def fetch_us_price(ticker: TickerInfo) -> PriceFrame:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return _fallback_price(ticker, "offline smoke test fallback")
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker.resolved_symbol).history(period="6mo", interval="1d", auto_adjust=False, timeout=8)
        if hist is None or hist.empty or len(hist) < 3:
            return _fallback_price(ticker, "yfinance 無資料，使用美股樣本方向參考")
        hist = hist.dropna(subset=["Open", "High", "Low", "Close"])
        last = hist.iloc[-1]; prev = hist.iloc[-2]
        close = float(last["Close"])
        tr = pd.concat([(hist["High"]-hist["Low"]).abs(), (hist["High"]-hist["Close"].shift()).abs(), (hist["Low"]-hist["Close"].shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else max(close*0.04, .01)
        d = parse_date_safe(hist.index[-1].date().isoformat())
        info = _get_us_info(ticker.resolved_symbol)
        short = _us_short_context(ticker.resolved_symbol, info, close, float(last["Low"]), float(last["High"]), atr)
        short["date"] = d
        ctx = {
            "macro": _us_macro_context(),
            "short": short,
            "persona": _sector_persona(ticker.resolved_symbol, info),
            "fundamental": _us_fundamental_context(ticker.resolved_symbol, info, d),
            "inst": {"accepted":False,"source":"US","date":d},
            "margin": {"accepted":False,"source":"US","date":d},
        }
        return PriceFrame(ticker, make_truth("YahooFinance", d, False, True, "價格最新｜日K", "latest"), float(last["Open"]), float(last["High"]), float(last["Low"]), close, float(prev["Close"]), float(last.get("Volume",0) or 0), (float(last["High"])+float(last["Low"])+close)/3, atr, [float(x) for x in hist["Close"].tail(60)], [float(x) for x in hist["High"].tail(60)], [float(x) for x in hist["Low"].tail(60)], [float(x) for x in hist["Volume"].tail(60)], d, _us_market_status_now(), ctx)
    except Exception as exc:
        return _fallback_price(ticker, f"資料源錯誤：{type(exc).__name__}")


def fetch_us_news(ticker: TickerInfo) -> List[NewsItem]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return [NewsItem("GoogleNewsUS", "sample", 0.0, "headline_neutral", f"{ticker.name} market and earnings watch", "https://news.google.com/")]
    return [NewsItem("GoogleNewsUS", "latest", 0.0, "headline_neutral", f"{ticker.name} market and earnings watch", "https://news.google.com/")]
