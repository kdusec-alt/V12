# -*- coding: utf-8 -*-
from __future__ import annotations

import math
from typing import Optional, Tuple
from models import PriceFrame


def is_valid_number(value) -> bool:
    try:
        x = float(value)
        return math.isfinite(x) and x > 0
    except Exception:
        return False


def validate_price_frame(price: PriceFrame) -> Tuple[bool, str]:
    required = [price.last, price.previous_close, price.open, price.high, price.low]
    if any(not is_valid_number(x) for x in required):
        return False, "價格抓不到或為 0，STOP，不產生 T0/T1/ABC。"
    if price.high < price.low:
        return False, "高低價異常，STOP。"
    if not price.truth.accepted:
        return False, f"資料未採納：{price.truth.reason}"
    return True, "OK"


def tw_tick(price: float) -> float:
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def round_to_tick(price: float, market: str) -> float:
    if market == "TW":
        tick = tw_tick(abs(price))
        return round(round(price / tick) * tick, 2)
    return round(float(price), 2)


def apply_market_bounds(value: float, previous_close: float, market: str, price_limit_pct: Optional[float]) -> float:
    v = float(value)
    if market == "TW" and price_limit_pct and previous_close > 0:
        upper = previous_close * (1 + price_limit_pct)
        lower = previous_close * (1 - price_limit_pct)
        v = min(max(v, lower), upper)
    return round_to_tick(v, market)
