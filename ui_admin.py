# -*- coding: utf-8 -*-
from __future__ import annotations

import hmac
import os
from typing import Any, Dict

import pandas as pd
from debug_trace import trace_to_text
from learning import (
    approve_profile_bias,
    audit_latest_prediction_for_ticker,
    get_profile,
    log_prediction,
    prediction_signature,
    recent_learning_tables,
    reset_profile_bias,
    suggest_from_forecast,
    today_prediction_vs_actual,
)
from memory_store import MEMORY_DIR


def _secret_value(st, key: str) -> str:
    try:
        val = st.secrets.get(key, "")
    except Exception:
        val = ""
    return str(val or os.environ.get(key, ""))


def _admin_gate(st) -> bool:
    st.sidebar.title("Tino Admin Console")
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False
    configured = _secret_value(st, "ADMIN_PASSWORD")
    if not configured:
        st.sidebar.warning("Admin Password 尚未設定")
        with st.sidebar.expander("設定方式", expanded=False):
            st.code('ADMIN_PASSWORD = "請換成你的密碼"', language="toml")
            st.caption("請放在 Streamlit Secrets；未設定前後台功能保持鎖定。")
        return False
    if st.session_state.admin_authenticated:
        st.sidebar.success("Admin 已登入")
        if st.sidebar.button("登出 Admin"):
            st.session_state.admin_authenticated = False
            st.rerun()
        return True
    pwd = st.sidebar.text_input("Admin Password", type="password", key="tino_admin_password")
    c1, c2 = st.sidebar.columns([1, 1])
    with c1:
        login = st.button("Login", key="tino_admin_login")
    with c2:
        st.caption("後台鎖定")
    if login:
        if hmac.compare_digest(str(pwd or ""), configured):
            st.session_state.admin_authenticated = True
            st.rerun()
        else:
            st.sidebar.error("密碼錯誤")
    return False


def _df(st, rows, empty_text: str):
    if not rows:
        st.caption(empty_text)
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _learning_panel(st, forecast):
    with st.sidebar.expander("Auto-Learning Audit", expanded=False):
        enabled = st.checkbox(
            "啟用 Auto-Learning 記錄",
            value=bool(st.session_state.get("learning_log_enabled", True)),
            key="learning_log_enabled",
            help="只記錄預測快照與 Audit，不會自動改主程式；需 Tino Approve 才會影響下次分析。",
        )
        if not enabled:
            st.info("Auto-Learning Log 目前關閉。開啟後才會寫入 prediction snapshot。")
            return

        logged_row = None
        if forecast and not getattr(forecast, "stopped", False):
            try:
                sig = prediction_signature(forecast)
                # Sidebar may be opened after the forecast was already generated.
                # Therefore the Audit panel also guarantees one snapshot exists.
                logged_row = log_prediction(forecast)
                st.success(f"Learning 已啟用｜目前預測快照：{sig}")
            except Exception as exc:
                st.warning(f"Learning snapshot 暫時無法寫入：{type(exc).__name__}: {exc}")
        else:
            st.caption("尚未有 forecast。請先按『開始分析』，系統才會產生 prediction log。")

        suggestions = suggest_from_forecast(forecast) if forecast else []
        _df(st, [x.__dict__ for x in suggestions], "目前沒有可顯示的建議。")
        st.caption("流程：預測快照 → 收盤回填 → 誤差分類 → 個股 profile → Tino Approve 後才小幅影響下次分析。")

        if forecast and not getattr(forecast, "stopped", False):
            if st.button("寫入目前預測快照", key="write_current_prediction_snapshot"):
                try:
                    row = log_prediction(forecast)
                    st.success(f"Prediction snapshot 已確認：{row.get('id')}")
                except Exception as exc:
                    st.error(f"寫入失敗：{type(exc).__name__}: {exc}")

            st.markdown("**Manual close audit**")
            audit_target_label = st.radio("Audit 目標", ["今日預測 VS 實際", "下一交易日預測 VS 實際"], horizontal=False, key="learning_audit_target")
            actual_default = float((forecast.decision_card or {}).get("現價", 0.0) or getattr(forecast, "final_t0", 0.0) or 0.0)
            actual = st.number_input("實際收盤價 / 最新確認價", min_value=0.0, value=actual_default, step=0.01)
            if st.button("寫入 Audit", key="write_learning_audit"):
                # Make sure the latest forecast exists before auditing.
                try:
                    log_prediction(forecast)
                except Exception:
                    pass
                target = "next" if audit_target_label.startswith("下一") else "today"
                audit = audit_latest_prediction_for_ticker(forecast.ticker.resolved_symbol, actual, target=target)
                if audit:
                    st.success(f"Audit 已寫入：{audit.get('target')}｜誤差 {float(audit.get('error_pct', 0.0)):+.2f}%")
                else:
                    st.warning("找不到該股票的 prediction log，請先按『寫入目前預測快照』或重新分析一次。")

            try:
                cmp = today_prediction_vs_actual(forecast, actual_default)
                if cmp.get("display"):
                    st.caption(cmp.get("display"))
            except Exception:
                pass

            profile = get_profile(forecast.ticker.resolved_symbol)
            if profile:
                st.markdown("**Ticker profile**")
                _df(st, [profile], "尚未建立個股 profile。")
                c1, c2 = st.columns([1, 1])
                with c1:
                    if st.button("Approve bias", key="approve_learning_bias"):
                        p = approve_profile_bias(forecast.ticker.resolved_symbol)
                        st.success(f"已核准偏壓：{float(p.get('approved_bias', 0.0)):+.2%}")
                with c2:
                    if st.button("Reset bias", key="reset_learning_bias"):
                        p = reset_profile_bias(forecast.ticker.resolved_symbol)
                        st.info(f"已歸零：{float(p.get('approved_bias', 0.0)):+.2%}")

        tables = recent_learning_tables(50)
        st.markdown("**Recent predictions**")
        _df(st, tables.get("predictions", [])[-10:], "尚無 prediction log。")
        st.markdown("**Recent audits**")
        _df(st, tables.get("audits", [])[-10:], "尚無 audit log。")
        st.markdown("**Storage status**")
        st.caption(f"Memory path：{MEMORY_DIR}")
        st.caption(f"Predictions：{len(tables.get('predictions', []))}｜Audits：{len(tables.get('audits', []))}｜Profiles：{len(tables.get('profiles', []))}")


def _mis_debug_panel(st, forecast):
    """Admin-only MIS diagnostics. Never render engineering strings on V9 front stage."""
    if not forecast or not getattr(forecast, "decision_card", None):
        st.sidebar.caption("MIS Debug：尚無 forecast。")
        return
    price_meta = (forecast.decision_card or {}).get("_price_meta") or {}
    mis_debug = price_meta.get("mis_debug") or {}
    with st.sidebar.expander("MIS Price Debug", expanded=True):
        st.caption("只顯示在 Admin Debug；前台不顯示工程字串。")
        st.markdown("**Selected price source**")
        st.json({
            "price_source": price_meta.get("source", ""),
            "price_status": price_meta.get("status", ""),
            "price_label": price_meta.get("label", ""),
            "decision_blocked": bool(price_meta.get("decision_blocked", False)),
        })
        st.markdown("**TWSE/TPEX MIS trace**")
        if mis_debug:
            ordered = {
                "mis_tried": mis_debug.get("mis_tried"),
                "mis_market": mis_debug.get("mis_market"),
                "mis_symbol": mis_debug.get("mis_symbol"),
                "mis_http_status": mis_debug.get("mis_http_status"),
                "mis_raw_ok": mis_debug.get("mis_raw_ok"),
                "mis_raw_rows": mis_debug.get("mis_raw_rows"),
                "mis_parsed_last": mis_debug.get("mis_parsed_last"),
                "mis_parsed_high": mis_debug.get("mis_parsed_high"),
                "mis_parsed_low": mis_debug.get("mis_parsed_low"),
                "mis_parsed_time": mis_debug.get("mis_parsed_time"),
                "mis_reject_reason": mis_debug.get("mis_reject_reason"),
                "fallback_used": price_meta.get("source", ""),
            }
            st.json(ordered)
        else:
            st.warning("尚未收到 mis_debug。請重新按一次『開始分析』，或確認已替換 v8.6 價格資料檔。")

def render_admin(st, forecast):
    authed = _admin_gate(st)
    if not authed:
        return "neutral", False, True, False
    macro = st.sidebar.selectbox("Macro 手動偏壓", ["neutral", "bullish", "bearish"], index=0)
    auto = st.sidebar.checkbox("Auto Analyze", value=False, help="預設關閉，避免開頁就抓外部資料。")
    live = st.sidebar.checkbox("Live Data / News", value=True, help="關閉時使用離線樣本，方便先確認系統可開啟。")
    if forecast:
        with st.sidebar.expander("Prediction Trace", expanded=False):
            if forecast.raw:
                st.code(trace_to_text(forecast.trace.steps, forecast.trace.raw_t1 or 0, forecast.trace.final_t1 or 0))
            rows = forecast.trace.to_rows()
            _df(st, rows, "尚無 trace。")
        with st.sidebar.expander("Dashboard Truth Guard", expanded=False):
            _df(st, [x.__dict__ for x in forecast.data_truths], "尚無資料真實性紀錄。")
    _learning_panel(st, forecast)
    debug = st.sidebar.checkbox("Debug Mode", value=False)
    if debug:
        _mis_debug_panel(st, forecast)
    return macro, auto, live, debug
