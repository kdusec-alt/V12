# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import hashlib

from models import FinalForecast, LearningSuggestion, SignalPacket
from memory_store import (
    AUDIT_LOG,
    PREDICTION_LOG,
    append_jsonl,
    load_profiles,
    read_audit_log,
    read_prediction_log,
    save_profiles,
    sync_prediction_row_to_sheet,
    sync_audit_row_to_sheet,
)
from ticker_resolver import resolve_ticker

TW_TZ = ZoneInfo("Asia/Taipei")
ERROR_TYPES = [
    "FQC overpull", "LCR underweight", "Macro overfit", "BSI missing", "法人日期誤判",
    "T1 High Magnet", "Risk Cascade 漏判", "ETF mode error", "Ticker resolver error",
]


def _now() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _canonical(raw_symbol: str) -> str:
    try:
        return resolve_ticker(raw_symbol).resolved_symbol
    except Exception:
        return str(raw_symbol or "").strip().upper()



def _run_date_tw() -> str:
    return datetime.now(TW_TZ).date().isoformat()


def _forecast_data_label(forecast: FinalForecast) -> str:
    try:
        return str((forecast.decision_card or {}).get("資料標題", ""))
    except Exception:
        return ""


def _forecast_session_mode(forecast: FinalForecast) -> str:
    label = _forecast_data_label(forecast)
    if "盤中" in label:
        return "intraday"
    if "收盤" in label:
        return "closed"
    if "盤前" in label:
        return "pre_market"
    if "盤後" in label:
        return "after_hours"
    return "unknown"


def _latest_audit_for_prediction(prediction_id: str, target: str = "today") -> Optional[Dict[str, Any]]:
    audit_id = f"{prediction_id}:{target}"
    for row in reversed(read_audit_log(500)):
        if row.get("audit_id") == audit_id:
            return row
    return None


def _same_day_predictions(ticker: str, limit: int = 500) -> List[Dict[str, Any]]:
    key = _canonical(ticker)
    today = _run_date_tw()
    rows = []
    for r in read_prediction_log(limit):
        if r.get("ticker") == key and str(r.get("run_date_tw") or "") == today:
            rows.append(r)
    return rows

def prediction_signature(forecast: FinalForecast) -> str:
    base = "|".join([
        forecast.ticker.resolved_symbol,
        str(forecast.final_t0),
        str(forecast.final_t1),
        str(forecast.final_t1_high),
        str(forecast.final_t1_low),
        str(forecast.confidence),
        str(forecast.reality_anchor),
    ])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def forecast_snapshot(forecast: FinalForecast, macro: str = "neutral", live_data: bool = True) -> Dict[str, Any]:
    return {
        "id": prediction_signature(forecast),
        "run_time_tw": _now(),
        "run_date_tw": _run_date_tw(),
        "session_mode": _forecast_session_mode(forecast),
        "data_label": _forecast_data_label(forecast),
        "ticker": forecast.ticker.resolved_symbol,
        "name": forecast.ticker.name,
        "market": forecast.ticker.market,
        "asset_type": forecast.ticker.asset_type,
        "macro_bias": macro,
        "live_data": bool(live_data),
        "spot_last": (forecast.decision_card or {}).get("現價"),
        "vwap_state": (forecast.decision_card or {}).get("VWAP位置"),
        "today_close_est": forecast.final_t0,
        "next_close_est": forecast.final_t1,
        "next_high_est": forecast.final_t1_high,
        "next_low_est": forecast.final_t1_low,
        "confidence": forecast.confidence,
        "one_liner": forecast.one_liner,
        "tags": forecast.tags,
        "radar": forecast.radar,
        "truths": [getattr(x, "__dict__", {}) for x in forecast.data_truths],
        "trace": forecast.trace.to_rows() if forecast.trace else [],
        "audited": False,
    }


def log_prediction(forecast: FinalForecast, macro: str = "neutral", live_data: bool = True) -> Dict[str, Any]:
    """Write one prediction snapshot per signature.

    Streamlit reruns often; this guard prevents the Auto-Learning panel from
    duplicating the same forecast every time the sidebar is opened.
    """
    row = forecast_snapshot(forecast, macro, live_data)
    rid = row.get("id")
    if rid:
        for old in read_prediction_log(500):
            if old.get("id") == rid:
                # v23: local duplicate still needs to be pushed to Google Sheet.
                try:
                    sync_prediction_row_to_sheet(old)
                except Exception:
                    pass
                return old
    append_jsonl(PREDICTION_LOG, row)
    try:
        sync_prediction_row_to_sheet(row)
    except Exception:
        pass
    return row


def suggest_from_forecast(forecast: FinalForecast, actual_close: Optional[float] = None) -> List[LearningSuggestion]:
    if forecast.stopped or forecast.final_t1 is None:
        return []
    if actual_close is None:
        return [LearningSuggestion(forecast.ticker.resolved_symbol, "pending_audit", "等待收盤後比對，不自動改主程式", 0.0, "尚未有 actual close", False)]
    pred = forecast.final_t0 if forecast.final_t0 is not None else forecast.final_t1
    err_pct = (actual_close - pred) / pred * 100.0 if pred else 0.0
    if abs(err_pct) < 1.5:
        return [LearningSuggestion(forecast.ticker.resolved_symbol, "within_tolerance", "不調權重", 0.0, f"誤差 {err_pct:+.2f}%", False)]
    etype = _classify_error(forecast, actual_close, err_pct)
    direction = "+" if err_pct > 0 else "-"
    return [LearningSuggestion(forecast.ticker.resolved_symbol, etype, f"連續同類錯誤後建議 {direction}1～3% 個股偏壓", 1.0, f"誤差 {err_pct:+.2f}%｜單次只記錄", True)]


def _classify_error(forecast: FinalForecast, actual_close: float, err_pct: float) -> str:
    radar_text = "\n".join(str(v) for v in (forecast.radar or {}).values())
    if "VWAP 下方" in radar_text and err_pct > 0:
        return "VWAP underweight"
    if "VWAP 上方" in radar_text and err_pct < 0:
        return "FQC overpull"
    if "融資" in radar_text and err_pct < 0:
        return "Risk Cascade 漏判"
    if "事件" in radar_text and abs(err_pct) >= 2.5:
        return "Macro overfit"
    return "under_prediction" if err_pct > 0 else "over_prediction"


def audit_prediction_row(row: Dict[str, Any], actual_close: float, source: str = "manual", target: str = "today") -> Dict[str, Any]:
    """Persist prediction-vs-actual comparison and update ticker profile.

    target="today" compares `today_close_est` with actual close.
    target="next" compares `next_close_est` with actual close.
    Duplicate audits are blocked by audit_id so Streamlit reruns do not pollute memory.
    """
    target = "next" if str(target).lower().startswith("next") else "today"
    prediction_id = str(row.get("id") or "")
    audit_id = f"{prediction_id}:{target}"
    for old in read_audit_log(500):
        if old.get("audit_id") == audit_id:
            return old

    pred_key = "next_close_est" if target == "next" else "today_close_est"
    pred = _safe_float(row.get(pred_key))
    actual = _safe_float(actual_close)
    err = actual - pred if pred else 0.0
    err_pct = ((actual - pred) / pred * 100.0) if pred else 0.0
    ticker = str(row.get("ticker") or "UNKNOWN")
    error_type = "within_tolerance" if abs(err_pct) < 1.0 else ("under_prediction" if err_pct > 0 else "over_prediction")
    audit = {
        "audit_id": audit_id,
        "audit_time_tw": _now(),
        "audit_date_tw": _run_date_tw(),
        "prediction_id": prediction_id,
        "ticker": ticker,
        "target": target,
        "predicted_close": round(pred, 4),
        "actual_close": round(actual, 4),
        "error": round(err, 4),
        "error_pct": round(err_pct, 4),
        "error_type": error_type,
        "prediction_run_time_tw": row.get("run_time_tw"),
        "prediction_session_mode": row.get("session_mode"),
        "source": source,
        "safe_to_apply": bool(abs(err_pct) >= 1.0),
        "applied": False,
    }
    append_jsonl(AUDIT_LOG, audit)
    try:
        sync_audit_row_to_sheet(audit)
    except Exception:
        pass
    _update_profile_from_audit(audit)
    return audit


def audit_today_prediction_for_forecast(forecast: FinalForecast, actual_close: float, source: str = "auto_close_compare") -> Dict[str, Any]:
    """Find today's intraday prediction snapshot, compare it with actual close, and persist audit.

    This is the real Auto-Learning bridge for the frontend:
    prediction snapshot -> actual close -> error -> stock profile.
    """
    key = forecast.ticker.resolved_symbol
    rows = _same_day_predictions(key, 800)
    intraday_rows = [r for r in rows if r.get("session_mode") == "intraday" and r.get("today_close_est") is not None]
    candidate = intraday_rows[-1] if intraday_rows else None
    if not candidate:
        return {
            "status": "no_intraday_prediction",
            "ticker": key,
            "actual_close": actual_close,
            "message": "尚無今日盤中預測快照，無法做真正預測VS實際。",
        }
    audit = audit_prediction_row(candidate, actual_close, source=source, target="today")
    audit["status"] = "audited"
    return audit


def today_prediction_vs_actual(forecast: FinalForecast, actual_close: Optional[float] = None) -> Dict[str, Any]:
    """Return UI-ready today prediction-vs-actual comparison.

    If actual_close is given and a valid intraday snapshot exists, this function also
    writes the audit once and updates ticker profile.
    """
    key = forecast.ticker.resolved_symbol
    if actual_close is None:
        actual_close = _safe_float((forecast.decision_card or {}).get("現價"))
    rows = _same_day_predictions(key, 800)
    intraday_rows = [r for r in rows if r.get("session_mode") == "intraday" and r.get("today_close_est") is not None]
    if not intraday_rows:
        return {
            "status": "no_intraday_prediction",
            "ticker": key,
            "actual_close": actual_close,
            "display": "今日預測VS實際：尚無盤中預測快照",
        }
    row = intraday_rows[-1]
    audit = audit_prediction_row(row, actual_close, source="frontend_close_compare", target="today")
    pred = _safe_float(audit.get("predicted_close"))
    actual = _safe_float(audit.get("actual_close"))
    err = _safe_float(audit.get("error"))
    err_pct = _safe_float(audit.get("error_pct"))
    direction = "低估收盤，模型偏保守" if err > 0 else "高估收盤，模型偏樂觀" if err < 0 else "命中收盤"
    display = f"今日預測VS實際：預估 {pred:.2f}｜實際 {actual:.2f}｜誤差 {err:+.2f} / {err_pct:+.2f}%｜{direction}"
    audit["display"] = display
    return audit


def _update_profile_from_audit(audit: Dict[str, Any]) -> None:
    profiles = load_profiles()
    ticker = str(audit.get("ticker") or "UNKNOWN")
    p = profiles.get(ticker, {})
    audits = int(p.get("audit_count", 0)) + 1
    old_avg = _safe_float(p.get("avg_abs_error_pct", 0.0))
    new_abs = abs(_safe_float(audit.get("error_pct")))
    avg_abs = ((old_avg * (audits - 1)) + new_abs) / audits
    error_type = str(audit.get("error_type") or "unknown")
    counts = p.get("error_type_counts", {}) if isinstance(p.get("error_type_counts"), dict) else {}
    counts[error_type] = int(counts.get(error_type, 0)) + 1
    suggested_bias = _safe_float(p.get("suggested_bias", 0.0))
    if error_type == "under_prediction" and counts[error_type] >= 2:
        suggested_bias = min(0.03, suggested_bias + 0.01)
    elif error_type == "over_prediction" and counts[error_type] >= 2:
        suggested_bias = max(-0.03, suggested_bias - 0.01)
    p.update({
        "ticker": ticker,
        "audit_count": audits,
        "avg_abs_error_pct": round(avg_abs, 4),
        "last_error_pct": audit.get("error_pct"),
        "last_error_type": error_type,
        "error_type_counts": counts,
        "suggested_bias": round(suggested_bias, 4),
        "approved_bias": _safe_float(p.get("approved_bias", 0.0)),
        "updated_at_tw": _now(),
    })
    profiles[ticker] = p
    save_profiles(profiles)


def approve_profile_bias(ticker: str, max_abs_bias: float = 0.03) -> Dict[str, Any]:
    profiles = load_profiles()
    key = _canonical(ticker)
    p = profiles.get(key, {"ticker": key})
    suggested = max(-max_abs_bias, min(max_abs_bias, _safe_float(p.get("suggested_bias", 0.0))))
    p["approved_bias"] = round(suggested, 4)
    p["approved_at_tw"] = _now()
    p["approval_note"] = "Tino Admin approved; applied as small SignalPacket bias only."
    profiles[key] = p
    save_profiles(profiles)
    return p


def reset_profile_bias(ticker: str) -> Dict[str, Any]:
    profiles = load_profiles()
    key = _canonical(ticker)
    p = profiles.get(key, {"ticker": key})
    p["approved_bias"] = 0.0
    p["approved_at_tw"] = _now()
    p["approval_note"] = "Tino Admin reset approved learning bias."
    profiles[key] = p
    save_profiles(profiles)
    return p


def get_profile(ticker: str) -> Dict[str, Any]:
    return load_profiles().get(_canonical(ticker), {})


def build_learning_signals(raw_symbol: str) -> List[SignalPacket]:
    key = _canonical(raw_symbol)
    profile = get_profile(key)
    bias = _safe_float(profile.get("approved_bias", 0.0))
    if abs(bias) < 0.0001:
        return []
    direction = "偏多修正" if bias > 0 else "偏空修正"
    return [SignalPacket(
        "LearningProfile",
        f"{key} 個股學習{direction}｜{bias:+.2%}",
        0.0,
        0.8,
        0.0,
        bias,
        f"Auto-Learning Audit approved profile｜audit_count={profile.get('audit_count', 0)}｜avg_abs_error={profile.get('avg_abs_error_pct', 'NA')}%",
        "AutoLearningAudit",
        str(profile.get("approved_at_tw") or profile.get("updated_at_tw") or ""),
        True,
    )]


def audit_latest_prediction_for_ticker(ticker: str, actual_close: float, target: str = "today") -> Optional[Dict[str, Any]]:
    key = _canonical(ticker)
    rows = [r for r in read_prediction_log(500) if r.get("ticker") == key]
    if not rows:
        return None
    if target == "today":
        intraday = [r for r in rows if r.get("run_date_tw") == _run_date_tw() and r.get("session_mode") == "intraday"]
        if intraday:
            return audit_prediction_row(intraday[-1], actual_close, source="manual_admin", target="today")
    return audit_prediction_row(rows[-1], actual_close, source="manual_admin", target=target)


def recent_learning_tables(limit: int = 80) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "predictions": read_prediction_log(limit),
        "audits": read_audit_log(limit),
        "profiles": list(load_profiles().values()),
    }
