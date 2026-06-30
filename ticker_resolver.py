# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from models import TickerInfo
from config import TW_PRICE_LIMIT_PCT, TWO_PRICE_LIMIT_PCT

TW_NAME_MAP = {
    "2337": ("旺宏", "2337.TW", "TWSE"),
    "2454": ("聯發科", "2454.TW", "TWSE"),
    "6770": ("力積電", "6770.TW", "TWSE"),
    "6586": ("醣基", "6586.TWO", "TPEX"),
    "00919": ("群益台灣精選高息", "00919.TW", "TWSE"),
    "2308": ("台達電", "2308.TW", "TWSE"),
    "3037": ("欣興", "3037.TW", "TWSE"),
    "2408": ("南亞科", "2408.TW", "TWSE"),
    "5469": ("瀚宇博", "5469.TW", "TWSE"),
}

TW_NAME_ALIAS = {
    "聯發科": "2454", "MEDIATEK": "2454",
    "旺宏": "2337", "MACRONIX": "2337",
    "力積電": "6770",
    "醣基": "6586",
    "台達電": "2308",
    "欣興": "3037", "欣興電子": "3037",
    "南亞科": "2408",
    "瀚宇博": "5469",
}

US_NAME_MAP = {
    "ONDS": ("Ondas Holdings", "ONDS", "NASDAQ"),
    "MRVL": ("Marvell Technology", "MRVL", "NASDAQ"),
    "MU": ("Micron Technology", "MU", "NASDAQ"),
    "NKE": ("Nike", "NKE", "NYSE"),
    "AAPL": ("Apple", "AAPL", "NASDAQ"),
    "NVDA": ("NVIDIA", "NVDA", "NASDAQ"),
    "TSM": ("Taiwan Semiconductor", "TSM", "NYSE"),
}

ETF_CODES = {"00919", "0050", "00918", "00929", "00981A", "009823", "00997A"}


def _clean(raw: str) -> str:
    return str(raw or "").strip().upper().replace(" ", "")


def resolve_ticker(raw: str) -> TickerInfo:
    text = _clean(raw)
    if not text:
        raise ValueError("Ticker 不可為空")

    base = text.replace(".TW", "").replace(".TWO", "")
    if base in TW_NAME_ALIAS:
        base = TW_NAME_ALIAS[base]
        text = base
    if re.fullmatch(r"\d{4,5}[A-Z]?", base):
        name, symbol, exchange = TW_NAME_MAP.get(base, (base, f"{base}.TW", "TWSE"))
        if text.endswith(".TWO"):
            symbol, exchange = f"{base}.TWO", "TPEX"
        asset_type = "etf" if base in ETF_CODES or base.startswith("00") else "stock"
        pct = TWO_PRICE_LIMIT_PCT if symbol.endswith(".TWO") else TW_PRICE_LIMIT_PCT
        return TickerInfo(raw=raw, resolved_symbol=symbol, name=name, market="TW", asset_type=asset_type, exchange=exchange, currency="TWD", price_limit_pct=pct)

    symbol = text.split(".")[0]
    name, resolved, exchange = US_NAME_MAP.get(symbol, (symbol, symbol, "US"))
    return TickerInfo(raw=raw, resolved_symbol=resolved, name=name, market="US", asset_type="stock", exchange=exchange, currency="USD", price_limit_pct=None)
