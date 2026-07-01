# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import traceback
import time
import streamlit as st

st.set_page_config(page_title="系統化分析", layout="wide", initial_sidebar_state="collapsed")


def _theme():
    st.markdown("""
    <style>
    :root{--bg:#02070c;--panel:#071727;--cyan:#36e6ff;--gold:#ffd96a;--text:#ecf6ff;}
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stMainBlockContainer"]{
        background:#02070c !important;
        color:var(--text)!important;
    }
    body::before{content:"";position:fixed;inset:0;background:#02070c;z-index:-999999;}
    [data-testid="stHeader"], header, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stTopNav"], [data-testid="stBottomBlockContainer"]{
        background:#02070c !important;
        color:#eaf6ff!important;
    }
    [data-testid="stToolbar"]{z-index:1000000!important;}
    .block-container{max-width:1920px;padding:.72rem .34rem .24rem!important;}
    [data-testid="stSidebar"]{background:#07101c!important;}
    [data-testid="stSidebar"] *{color:#eaf6ff!important;}
    .input-safe-spacer{height:46px;}
    .stTextInput input{
        background:#071727!important;color:#eaf6ff!important;border:1px solid #1d6f95!important;border-radius:12px!important;
        font-weight:1000!important;font-size:17px!important;min-height:46px!important;box-shadow:0 0 0 1px rgba(54,230,255,.10) inset!important;
    }
    .stTextInput input:focus{border-color:#82e8ff!important;box-shadow:0 0 0 2px rgba(54,230,255,.22)!important;}
    .stButton{position:relative;z-index:9999!important;}
    .stButton button{
        background:#11151d!important;color:#fff5c4!important;border:1px solid rgba(255,217,106,.45)!important;border-radius:12px!important;
        font-weight:1000!important;font-size:16px!important;min-height:52px!important;box-shadow:0 10px 28px rgba(0,0,0,.24)!important;
        pointer-events:auto!important;opacity:1!important;
    }
    .stButton button:hover{border-color:#ffe78a!important;background:#17202b!important;transform:translateY(-1px);}
    .stButton button:active{transform:translateY(0);filter:brightness(1.12);}
    .stButton button:disabled{background:#17202b!important;color:#ffeaa3!important;border:1px solid rgba(255,217,106,.48)!important;opacity:1!important;}
    .v12bar{border:1px solid rgba(54,230,255,.23);border-radius:12px;padding:7px 12px;margin:2px 0 7px;background:#06101b;font-weight:1000;color:#dff5ff;}
    .bootbox{border:1px solid rgba(255,217,106,.35);border-radius:14px;background:#071727;padding:18px 20px;margin-top:12px;color:#eaf6ff;font-weight:850;line-height:1.6;}
    textarea{font-family:'Consolas','Microsoft JhengHei',monospace!important;color:#eaf6ff!important;background:#071727!important;border:1px solid #15506d!important;}
    </style>
    """, unsafe_allow_html=True)

try:
    from data_sources import fetch_news, fetch_price
    from orchestrator import orchestrate
    from ui_admin import render_admin
    from ui_v9_battle_panel import render_battle_panel
    from ui_v9_deep_report import render_deep_report
    from ui_v9_input import render_input
    from ui_v9_radar import render_radar
    from learning import log_prediction, prediction_signature
    try:
        from learning import build_learning_signals
    except Exception:
        # v17 safety net:
        # 若雲端部署時 learning.py 沒有完整覆蓋到最新版，不讓主程式直接白屏。
        # 前台仍可分析；Auto-Learning bias 只會暫時降級為 0，Admin/Trace 可再檢查檔案版本。
        def build_learning_signals(symbol):
            return []
except Exception as exc:
    _theme()
    st.error("系統模組載入失敗，停止啟動正式預測。")
    st.code(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
    st.stop()


@st.cache_data(show_spinner=False, ttl=30)
def _cached_analysis(symbol: str, macro: str, live_data: bool, live_refresh_bucket: int = 0, learning_fingerprint: int = 0):
    if not live_data:
        os.environ["TINO_OFFLINE_TEST"] = "1"
    else:
        os.environ.pop("TINO_OFFLINE_TEST", None)
    price = fetch_price(symbol)
    news = fetch_news(symbol)
    extra_signals = build_learning_signals(symbol)
    return orchestrate(price, macro, news_items=news, extra_signals=extra_signals)


def run_analysis(symbol: str, macro: str, live_data: bool):
    # Live mode must not reuse a 5-minute-old forecast.
    # Bucket makes manual re-analysis refresh price roughly every 8 seconds while still preventing API spam.
    live_refresh_bucket = int(time.time() // 8) if live_data else 0
    learning_fingerprint = int(time.time() // 30)
    return _cached_analysis(symbol.strip(), macro, live_data, live_refresh_bucket, learning_fingerprint)


def _render_forecast(forecast):
    left, right = st.columns([1.03, 0.97], gap="small")
    with left:
        render_battle_panel(st, forecast)
    with right:
        render_radar(st, forecast)
    render_deep_report(st, forecast)


def main():
    _theme()
    if "forecast" not in st.session_state:
        st.session_state.forecast = None
    if "last_error" not in st.session_state:
        st.session_state.last_error = ""

    macro, auto, live, debug = render_admin(st, st.session_state.forecast)
    symbol, analyze, clear = render_input(st)

    if clear:
        st.cache_data.clear()
        st.session_state.forecast = None
        st.session_state.last_error = ""
        st.session_state.symbol = ""
        st.session_state.suppress_auto_once = True
        st.session_state.input_was_cleared = True
        st.rerun()

    suppress_auto_once = bool(st.session_state.pop("suppress_auto_once", False))
    active_symbol = str(st.session_state.get("symbol", "") or "").strip().upper()
    typing_changed = bool(st.session_state.get("typing_changed", False))
    auto_ready = bool(auto and not suppress_auto_once and not typing_changed and st.session_state.forecast is None and symbol and active_symbol == symbol)
    should_run = bool((analyze and symbol) or auto_ready)
    if should_run:
        try:
            with st.status("分析中：價格 / 法人 / 資券 / 模型", expanded=False):
                if not symbol:
                    st.session_state.forecast = None
                    st.session_state.last_error = ""
                    st.stop()
                st.session_state.symbol = symbol
                st.session_state.input_was_cleared = False
                st.session_state.forecast = run_analysis(symbol, macro, live)
                sig = prediction_signature(st.session_state.forecast) if st.session_state.forecast else ""
                if sig and st.session_state.get("last_logged_prediction_sig") != sig:
                    log_prediction(st.session_state.forecast, macro=macro, live_data=live)
                    st.session_state.last_logged_prediction_sig = sig
                st.session_state.last_error = ""
        except Exception as exc:
            st.session_state.forecast = None
            st.session_state.last_error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"

    if st.session_state.last_error:
        st.error("分析流程發生錯誤，已阻擋整頁白屏。")
        st.code(st.session_state.last_error)

    forecast = st.session_state.forecast
    if forecast:
        _render_forecast(forecast)
    else:
        st.markdown("""
        <div class="bootbox">
        系統已啟動。請輸入股票 / ETF 後按「🚀 開始分析」。<br>
        這版已關閉首次載入自動抓資料，避免 Streamlit Cloud 開頁時因外部資料源延遲造成白屏或卡住。<br>
        若要自動分析，可到左側 Admin Console 開啟 Auto Analyze。
        </div>
        """, unsafe_allow_html=True)

    if debug:
        st.caption("Debug：主畫面不顯示工程字串；錯誤只在此區或 Admin Console 顯示。")


if __name__ == "__main__":
    main()
