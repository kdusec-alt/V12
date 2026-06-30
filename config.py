# -*- coding: utf-8 -*-
from __future__ import annotations

VERSION = "TINO V12｜V9 Frontend Clone Contract"

CONSTITUTION = """
V12 是唯一正式版本，取代 V9，但不得低於 V9。
V9 的成熟戰術面板、操盤語言、閱讀流程、深度分析與功能是最低能力基線。
V12 只能在底層新增可追蹤、可防錯、可學習、可維護的能力。
任何模組只能輸出 SignalPacket，Final T0/T1/High/Low 只能由 Orchestrator 仲裁。
GPT 不得私自刪除、合併、濃縮、重排或重新詮釋正式功能。
""".strip()

TW_PRICE_LIMIT_PCT = 0.10
TWO_PRICE_LIMIT_PCT = 0.10
US_PRICE_LIMIT_PCT = None
MIN_CONFIDENCE = 20.0
MAX_CONFIDENCE = 95.0
MAX_T1_ADJUSTMENT_ATR = 1.25
HIGH_MAGNET_BUFFER = 0.18

PRICE_NEUTRAL_MODULES = {"Macro", "GRR", "事件", "財報", "News"}

REQUIRED_RADAR_ROWS = [
    "Fair Value", "ABC 多空情境", "BSI 借券空方", "FQC",
    "市場風控", "事件/Macro", "外資期貨", "基本面",
    "空方成本 / 回補", "三大法人", "資券 / 融資融券",
]

FORBIDDEN_MAIN_UI_STRINGS = [
    "pending", "Alpha", "詳細見 Debug", "欄位待驗證", "Fallback", "fallback", "不納入正式分數", "僅方向參考", "待接",
    "新聞來源 / 外部事件", "V12 AI Core", "Trace可重建", "Trace 可重建",
    "Truth Guard", "Learning Audit", "由 Orchestrator 採納", "accepted TRUE",
    "RuntimeError", "WAIT_OFFICIAL",
]
