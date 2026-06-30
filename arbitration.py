# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable, Tuple
from config import MAX_T1_ADJUSTMENT_ATR, PRICE_NEUTRAL_MODULES
from models import PriceFrame, SignalPacket


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def signal_price_adjustment(signal: SignalPacket, price: PriceFrame) -> float:
    if not signal.accepted or signal.module in PRICE_NEUTRAL_MODULES:
        return 0.0
    atr = max(float(price.atr14), float(price.last) * 0.012, 0.01)
    bias_component = float(signal.bias) * atr
    score_component = clamp(float(signal.score) / 100.0, -0.18, 0.18) * atr
    risk_component = -clamp(float(signal.risk) / 120.0, 0.0, 0.18) * atr
    return round(bias_component + score_component + risk_component, 4)


def cap_total_adjustment(adjustments: Iterable[float], price: PriceFrame) -> Tuple[float, float]:
    raw_total = float(sum(adjustments))
    cap = max(float(price.atr14), float(price.last) * 0.012, 0.01) * MAX_T1_ADJUSTMENT_ATR
    return round(clamp(raw_total, -cap, cap), 4), round(cap, 4)
