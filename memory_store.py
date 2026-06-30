# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import base64
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")

def _now_tw() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def _default_memory_dir() -> Path:
    """Return a writable memory path.

    Local files are the fast cache.  Google Sheet, when configured through
    Streamlit Secrets, is the long-term memory that survives redeploy/reboot.
    """
    env = os.environ.get("TINO_MEMORY_DIR")
    if env:
        return Path(env)
    candidates = [
        Path.cwd() / ".tino_memory",
        Path.home() / ".tino_stock_engine_memory",
        Path("/tmp/tino_memory"),
    ]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            test = c / ".write_test"
            test.write_text("ok", encoding="utf-8")
            try:
                test.unlink()
            except Exception:
                pass
            return c
        except Exception:
            continue
    return Path("/tmp/tino_memory")


MEMORY_DIR = _default_memory_dir()
PREDICTION_LOG = MEMORY_DIR / "prediction_log.jsonl"
AUDIT_LOG = MEMORY_DIR / "audit_log.jsonl"
TICKER_PROFILE = MEMORY_DIR / "ticker_profiles.json"
STATUS_LOG = MEMORY_DIR / "system_status.json"

PRED_HEADERS = [
    "id", "run_time_tw", "run_date_tw", "session_mode", "ticker", "name",
    "today_close_est", "next_close_est", "next_high_est", "next_low_est",
    "confidence", "one_liner", "tags_json", "radar_json", "payload_json",
]
AUDIT_HEADERS = [
    "audit_id", "audit_time_tw", "audit_date_tw", "prediction_id", "ticker",
    "target", "predicted_close", "actual_close", "error", "error_pct",
    "error_type", "source", "payload_json",
]
PROFILE_HEADERS = [
    "ticker", "audit_count", "avg_abs_error_pct", "last_error_pct",
    "last_error_type", "suggested_bias", "approved_bias", "updated_at_tw",
    "payload_json",
]
STATUS_HEADERS = [
    "updated_at_tw", "storage_mode", "event", "message",
    "prediction_count", "audit_count", "profile_count",
]


def _load_status() -> Dict[str, Any]:
    if STATUS_LOG.exists():
        try:
            data = json.loads(STATUS_LOG.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "storage_mode": "local_only",
        "google_sheet_connected": False,
        "last_sync_tw": None,
        "last_error": None,
        "sheet_id": None,
        "memory_dir": str(MEMORY_DIR),
    }


def _save_status(data: Dict[str, Any]) -> None:
    data["memory_dir"] = str(MEMORY_DIR)
    STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
    STATUS_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _set_status(**kwargs) -> None:
    s = _load_status()
    s.update(kwargs)
    _save_status(s)


def _get_streamlit_secrets() -> Dict[str, Any]:
    try:
        import streamlit as st  # type: ignore
        return dict(st.secrets)
    except Exception:
        return {}


def _sheet_id() -> str:
    secrets = _get_streamlit_secrets()
    return str(secrets.get("GSPREAD_SHEET_ID") or os.environ.get("GSPREAD_SHEET_ID") or "").strip()


def _service_account_info() -> Optional[Dict[str, Any]]:
    """Load service account credentials.

    Priority:
    1) GCP_SERVICE_ACCOUNT_B64: base64 encoded original Google JSON. Best for Streamlit Secrets.
    2) [gcp_service_account] table in Streamlit Secrets.
    3) GCP_SERVICE_ACCOUNT_JSON environment variable for local testing.
    """
    secrets = _get_streamlit_secrets()

    # v24 safest path: paste one Base64 line into Streamlit Secrets.
    b64 = ""
    try:
        b64 = str(secrets.get("GCP_SERVICE_ACCOUNT_B64") or "")
    except Exception:
        b64 = ""
    b64 = b64 or os.environ.get("GCP_SERVICE_ACCOUNT_B64", "")
    if b64:
        try:
            info = json.loads(base64.b64decode(b64).decode("utf-8"))
            if isinstance(info, dict):
                return info
        except Exception as exc:
            _set_status(
                storage_mode="local_fallback",
                google_sheet_connected=False,
                last_error=f"B64 credential parse failed: {type(exc).__name__}: {repr(exc)}",
                last_traceback=traceback.format_exc(),
            )
            return None

    info = secrets.get("gcp_service_account") if secrets else None
    if info:
        try:
            return dict(info)
        except Exception as exc:
            _set_status(
                storage_mode="local_fallback",
                google_sheet_connected=False,
                last_error=f"Secrets credential parse failed: {type(exc).__name__}: {repr(exc)}",
                last_traceback=traceback.format_exc(),
            )
            return None

    raw = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            return json.loads(raw)
        except Exception as exc:
            _set_status(
                storage_mode="local_fallback",
                google_sheet_connected=False,
                last_error=f"Env credential parse failed: {type(exc).__name__}: {repr(exc)}",
                last_traceback=traceback.format_exc(),
            )
            return None
    return None

_GS_CACHE: Dict[str, Any] = {}


def _gsheet():
    """Return opened Google spreadsheet or None.

    Import is lazy so offline tests and local runs do not require Google libs.
    """
    sid = _sheet_id()
    info = _service_account_info()
    if not sid or not info:
        _set_status(
            storage_mode="local_only",
            google_sheet_connected=False,
            sheet_id=sid or None,
            last_error="Google Sheet secrets not configured",
        )
        return None
    cache_key = sid + "|" + str(info.get("client_email", ""))
    if cache_key in _GS_CACHE:
        return _GS_CACHE[cache_key]
    try:
        import gspread  # type: ignore
        from google.oauth2.service_account import Credentials  # type: ignore

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        client = gspread.authorize(creds)
        ss = client.open_by_key(sid)
        _GS_CACHE[cache_key] = ss
        _set_status(
            storage_mode="google_sheet",
            google_sheet_connected=True,
            sheet_id=sid,
            last_sync_tw=_now_tw(),
            last_error=None,
        )
        return ss
    except Exception as exc:
        _set_status(
            storage_mode="local_fallback",
            google_sheet_connected=False,
            sheet_id=sid,
            last_error=f"{type(exc).__name__}: {repr(exc)}",
            last_traceback=traceback.format_exc(),
        )
        return None


def _ws(title: str, headers: List[str]):
    ss = _gsheet()
    if ss is None:
        return None
    try:
        worksheet = ss.worksheet(title)
    except Exception:
        worksheet = ss.add_worksheet(title=title, rows=1000, cols=max(20, len(headers) + 2))
    try:
        first = worksheet.row_values(1)
        if first != headers:
            if not first:
                worksheet.append_row(headers, value_input_option="RAW")
            else:
                worksheet.update("A1", [headers])
    except Exception:
        pass
    return worksheet


def _safe_json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _prediction_row(row: Dict[str, Any]) -> List[Any]:
    return [
        row.get("id"), row.get("run_time_tw"), row.get("run_date_tw"), row.get("session_mode"),
        row.get("ticker"), row.get("name"), row.get("today_close_est"), row.get("next_close_est"),
        row.get("next_high_est"), row.get("next_low_est"), row.get("confidence"), row.get("one_liner"),
        _safe_json(row.get("tags")), _safe_json(row.get("radar")), _safe_json(row),
    ]


def _audit_row(row: Dict[str, Any]) -> List[Any]:
    return [
        row.get("audit_id"), row.get("audit_time_tw"), row.get("audit_date_tw"), row.get("prediction_id"),
        row.get("ticker"), row.get("target"), row.get("predicted_close"), row.get("actual_close"),
        row.get("error"), row.get("error_pct"), row.get("error_type"), row.get("source"), _safe_json(row),
    ]


def _profile_row(row: Dict[str, Any]) -> List[Any]:
    return [
        row.get("ticker"), row.get("audit_count"), row.get("avg_abs_error_pct"), row.get("last_error_pct"),
        row.get("last_error_type"), row.get("suggested_bias"), row.get("approved_bias"),
        row.get("updated_at_tw"), _safe_json(row),
    ]


def _append_sheet(kind: str, row: Dict[str, Any]) -> bool:
    try:
        if kind == "prediction_log":
            ws = _ws("prediction_log", PRED_HEADERS)
            values = _prediction_row(row)
        elif kind == "audit_log":
            ws = _ws("audit_log", AUDIT_HEADERS)
            values = _audit_row(row)
        else:
            return False
        if ws is None:
            return False
        ws.append_row(values, value_input_option="USER_ENTERED")
        _set_status(storage_mode="google_sheet", google_sheet_connected=True, last_sync_tw=_now_tw(), last_error=None)
        return True
    except Exception as exc:
        _set_status(storage_mode="local_fallback", google_sheet_connected=False, last_error=f"{type(exc).__name__}: {repr(exc)}", last_traceback=traceback.format_exc())
        return False


def _replace_profiles_sheet(profiles: Dict[str, Any]) -> bool:
    try:
        ws = _ws("ticker_profiles", PROFILE_HEADERS)
        if ws is None:
            return False
        rows = [PROFILE_HEADERS]
        for ticker, row in sorted(profiles.items()):
            if isinstance(row, dict):
                r = dict(row)
                r.setdefault("ticker", ticker)
                rows.append(_profile_row(r))
        ws.clear()
        ws.update("A1", rows, value_input_option="USER_ENTERED")
        _set_status(storage_mode="google_sheet", google_sheet_connected=True, last_sync_tw=_now_tw(), last_error=None)
        return True
    except Exception as exc:
        _set_status(storage_mode="local_fallback", google_sheet_connected=False, last_error=f"{type(exc).__name__}: {repr(exc)}", last_traceback=traceback.format_exc())
        return False


def _write_system_status_sheet(event: str = "status") -> None:
    try:
        ws = _ws("system_status", STATUS_HEADERS)
        if ws is None:
            return
        st = storage_status()
        row = [
            _now_tw(), st.get("storage_mode"), event, st.get("last_error") or "OK",
            st.get("prediction_count"), st.get("audit_count"), st.get("profile_count"),
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception:
        pass


def _read_sheet_payloads(kind: str, limit: int = 200) -> List[Dict[str, Any]]:
    try:
        if kind == "prediction_log":
            ws = _ws("prediction_log", PRED_HEADERS)
        elif kind == "audit_log":
            ws = _ws("audit_log", AUDIT_HEADERS)
        else:
            return []
        if ws is None:
            return []
        records = ws.get_all_records()
        rows = []
        for rec in records[-limit:]:
            payload = rec.get("payload_json")
            if payload:
                try:
                    obj = json.loads(payload)
                    if isinstance(obj, dict):
                        rows.append(obj)
                        continue
                except Exception:
                    pass
            rows.append(dict(rec))
        return rows
    except Exception as exc:
        _set_status(storage_mode="local_fallback", google_sheet_connected=False, last_error=f"{type(exc).__name__}: {repr(exc)}", last_traceback=traceback.format_exc())
        return []


def _read_profiles_sheet() -> Dict[str, Any]:
    try:
        ws = _ws("ticker_profiles", PROFILE_HEADERS)
        if ws is None:
            return {}
        records = ws.get_all_records()
        out: Dict[str, Any] = {}
        for rec in records:
            payload = rec.get("payload_json")
            obj = None
            if payload:
                try:
                    obj = json.loads(payload)
                except Exception:
                    obj = None
            if not isinstance(obj, dict):
                obj = dict(rec)
            ticker = str(obj.get("ticker") or rec.get("ticker") or "").strip()
            if ticker:
                out[ticker] = obj
        return out
    except Exception as exc:
        _set_status(storage_mode="local_fallback", google_sheet_connected=False, last_error=f"{type(exc).__name__}: {repr(exc)}", last_traceback=traceback.format_exc())
        return {}


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    if path.name == "prediction_log.jsonl":
        _append_sheet("prediction_log", row)
    elif path.name == "audit_log.jsonl":
        _append_sheet("audit_log", row)


def read_jsonl(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    # If local cache is empty after redeploy/reboot, hydrate from Google Sheet.
    if not rows:
        if path.name == "prediction_log.jsonl":
            rows = _read_sheet_payloads("prediction_log", limit)
            if rows:
                for r in rows:
                    _append_local_only(path, r)
        elif path.name == "audit_log.jsonl":
            rows = _read_sheet_payloads("audit_log", limit)
            if rows:
                for r in rows:
                    _append_local_only(path, r)
    return rows


def _append_local_only(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if path.name == "ticker_profiles.json":
        profiles = _read_profiles_sheet()
        if profiles:
            write_json_local_only(path, profiles)
            return profiles
    return dict(default or {})


def write_json_local_only(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_json(path: Path, data: Dict[str, Any]) -> None:
    write_json_local_only(path, data)
    if path.name == "ticker_profiles.json":
        _replace_profiles_sheet(data)


def read_prediction_log(limit: int = 100) -> List[Dict[str, Any]]:
    return read_jsonl(PREDICTION_LOG, limit)


def read_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    return read_jsonl(AUDIT_LOG, limit)


def load_profiles() -> Dict[str, Any]:
    return read_json(TICKER_PROFILE, {})


def save_profiles(profiles: Dict[str, Any]) -> None:
    write_json(TICKER_PROFILE, profiles)


def storage_status() -> Dict[str, Any]:
    preds = read_jsonl(PREDICTION_LOG, 100000) if PREDICTION_LOG.exists() else []
    audits = read_jsonl(AUDIT_LOG, 100000) if AUDIT_LOG.exists() else []
    profiles = read_json(TICKER_PROFILE, {}) if TICKER_PROFILE.exists() else {}
    status = _load_status()
    # quick connection probe, but do not force failure if secrets are absent
    if _sheet_id() and _service_account_info():
        _gsheet()
        status = _load_status()
    status.update({
        "memory_dir": str(MEMORY_DIR),
        "prediction_count": len(preds),
        "audit_count": len(audits),
        "profile_count": len(profiles) if isinstance(profiles, dict) else 0,
        "prediction_log_path": str(PREDICTION_LOG),
        "audit_log_path": str(AUDIT_LOG),
        "ticker_profile_path": str(TICKER_PROFILE),
        "sheet_id": _sheet_id() or status.get("sheet_id"),
    })
    return status




def sync_prediction_row_to_sheet(row: Dict[str, Any]) -> bool:
    """Public helper: ensure a prediction row is also pushed to Google Sheet.

    Important for duplicate local predictions: log_prediction may return an
    existing local row without appending JSONL, so the sheet still needs a push.
    """
    return _append_sheet("prediction_log", row)


def sync_audit_row_to_sheet(row: Dict[str, Any]) -> bool:
    return _append_sheet("audit_log", row)


def test_google_sheet_connection() -> Dict[str, Any]:
    """Admin diagnostic: step-by-step Google Sheet connection test.

    Returns exact step and full traceback so we stop guessing.
    """
    debug = {
        "test_ok": False,
        "steps": [],
        "failed_step": None,
        "error": None,
        "traceback": None,
    }

    def step(name: str, ok: bool = True, msg: str = "") -> None:
        debug["steps"].append({"step": name, "ok": bool(ok), "message": msg})

    try:
        sid = _sheet_id()
        step("read_sheet_id", bool(sid), sid or "missing GSPREAD_SHEET_ID")
        if not sid:
            raise RuntimeError("GSPREAD_SHEET_ID missing")

        info = _service_account_info()
        step("read_credentials", bool(info), (info or {}).get("client_email", "missing credentials"))
        if not info:
            raise RuntimeError("service account credentials missing or invalid")

        import gspread  # type: ignore
        from google.oauth2.service_account import Credentials  # type: ignore
        step("import_gspread_google_auth", True, "ok")

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        step("build_credentials", True, "ok")

        client = gspread.authorize(creds)
        step("authorize_client", True, "ok")

        ss = client.open_by_key(sid)
        step("open_spreadsheet", True, getattr(ss, "title", "opened"))

        for title, headers in [
            ("prediction_log", PRED_HEADERS),
            ("audit_log", AUDIT_HEADERS),
            ("ticker_profiles", PROFILE_HEADERS),
            ("system_status", STATUS_HEADERS),
        ]:
            try:
                worksheet = ss.worksheet(title)
                step(f"open_worksheet:{title}", True, "exists")
            except Exception:
                worksheet = ss.add_worksheet(title=title, rows=1000, cols=max(20, len(headers) + 2))
                step(f"create_worksheet:{title}", True, "created")
            first = worksheet.row_values(1)
            if first != headers:
                if not first:
                    worksheet.append_row(headers, value_input_option="RAW")
                else:
                    worksheet.update("A1", [headers])
                step(f"ensure_header:{title}", True, "header updated")
            else:
                step(f"ensure_header:{title}", True, "header ok")

        ws = ss.worksheet("system_status")
        ws.append_row([_now_tw(), "google_sheet", "connection_test", "OK", 0, 0, 0], value_input_option="USER_ENTERED")
        step("append_system_status", True, "test row appended")

        _set_status(
            storage_mode="google_sheet",
            google_sheet_connected=True,
            sheet_id=sid,
            last_sync_tw=_now_tw(),
            last_error=None,
            last_traceback=None,
            last_debug=debug,
        )
        debug["test_ok"] = True
        return storage_status() | debug

    except Exception as exc:
        debug["failed_step"] = debug["steps"][-1]["step"] if debug["steps"] else "unknown"
        debug["error"] = f"{type(exc).__name__}: {repr(exc)}"
        debug["traceback"] = traceback.format_exc()
        _set_status(
            storage_mode="local_fallback",
            google_sheet_connected=False,
            last_error=debug["error"],
            last_traceback=debug["traceback"],
            last_debug=debug,
        )
        return storage_status() | debug

def force_sync_to_google_sheet() -> Dict[str, Any]:
    """Manual Admin repair button: push local memory to Google Sheet."""
    preds = read_jsonl(PREDICTION_LOG, 100000) if PREDICTION_LOG.exists() else []
    audits = read_jsonl(AUDIT_LOG, 100000) if AUDIT_LOG.exists() else []
    profiles = read_json(TICKER_PROFILE, {}) if TICKER_PROFILE.exists() else {}

    ok_pred = True
    ok_audit = True
    for r in preds[-5000:]:
        ok_pred = _append_sheet("prediction_log", r) and ok_pred
    for r in audits[-5000:]:
        ok_audit = _append_sheet("audit_log", r) and ok_audit
    ok_profile = _replace_profiles_sheet(profiles) if profiles else True
    _write_system_status_sheet("manual_force_sync")
    return storage_status() | {"manual_sync_ok": bool(ok_pred and ok_audit and ok_profile)}
