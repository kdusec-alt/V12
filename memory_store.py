# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

def _default_memory_dir() -> Path:
    """Return a writable persistent-ish memory path.

    Streamlit Cloud keeps /tmp only for the current runtime.  For Auto-Learning,
    prefer an app-local hidden folder or the user's home folder, and use /tmp
    only as the last fallback.  TINO_MEMORY_DIR can still override everything.
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


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_prediction_log(limit: int = 100) -> List[Dict[str, Any]]:
    return read_jsonl(PREDICTION_LOG, limit)


def read_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    return read_jsonl(AUDIT_LOG, limit)


def load_profiles() -> Dict[str, Any]:
    return read_json(TICKER_PROFILE, {})


def save_profiles(profiles: Dict[str, Any]) -> None:
    write_json(TICKER_PROFILE, profiles)
