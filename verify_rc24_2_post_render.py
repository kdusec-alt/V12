from __future__ import annotations
import compileall
import os
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
assert compileall.compile_dir(str(ROOT), quiet=1)

# No orphan-thread fundamental runner.
import data_sources_tw_fundamental as f
orig = [f._fetch_mops_month_revenue, f._fetch_finmind_month_revenue]
try:
    f._fetch_mops_month_revenue = lambda s, d: {"accepted": True, "source": "MOPS_TEST", "month": "2026/06", "revenue_billion": 1.0}
    f._fetch_finmind_month_revenue = lambda s, d: {"accepted": True, "source": "FINMIND_TEST", "month": "2026/06", "revenue_billion": 1.0}
    before = threading.active_count()
    rows = f._run_sources_fast("6770.TW", "2026-07-11")
    after = threading.active_count()
    assert len(rows) == 2
    assert before == after
finally:
    f._fetch_mops_month_revenue, f._fetch_finmind_month_revenue = orig

# Foreground memory write keeps raw JSONL but skips mirror when disabled.
os.environ["TINO_INLINE_MEMORY_MIRROR"] = "0"
import memory_store
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "prediction_log.jsonl"
    memory_store.append_jsonl(p, {"id": "rc24_2_test"})
    assert p.exists() and "rc24_2_test" in p.read_text(encoding="utf-8")

print("RC24.2 post-render verification: PASS")
