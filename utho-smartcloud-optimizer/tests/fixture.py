"""Shared test fixture: one synthetic database generated per test run."""
from __future__ import annotations
import tempfile
from pathlib import Path

from server import analysis, store, synth

_DB = None
_RESULT = None


def db_path() -> Path:
    global _DB
    if _DB is None:
        tmp = Path(tempfile.mkdtemp(prefix="utho-test-")) / "test.sqlite"
        synth.generate(tmp)
        _DB = tmp
    return _DB


def result() -> dict:
    global _RESULT
    if _RESULT is None:
        conn = store.connect(db_path())
        try:
            _RESULT = analysis.run_analysis(conn)
        finally:
            conn.close()
    return _RESULT


def rec_by_id(rec_id: str) -> dict | None:
    return next((r for r in result()["recommendations"] if r["id"] == rec_id), None)
