"""SQLite persistence: data access, recommendation decisions, audit trail,
alert acknowledgements and settings."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config as C


def connect(path: Path = C.DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- inventory ----------
def load_resources(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM resources ORDER BY resource_id")]


def load_volumes(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM volumes ORDER BY volume_id")]


def load_public_ips(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM public_ips ORDER BY ip_id")]


def metrics_for(conn, resource_id: str, start: int | None = None, end: int | None = None):
    sql = "SELECT ts, cpu_pct, mem_pct, disk_pct, disk_iops, net_in_mbps, net_out_mbps FROM metrics WHERE resource_id = ?"
    args = [resource_id]
    if start is not None:
        sql += " AND ts >= ?"
        args.append(start)
    if end is not None:
        sql += " AND ts < ?"
        args.append(end)
    sql += " ORDER BY ts"
    return conn.execute(sql, args).fetchall()


# ---------- settings ----------
def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row["value"])


def set_setting(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (key, json.dumps(value)))
    conn.commit()


def effective_settings(conn) -> dict:
    """Defaults overlaid with user overrides. Only known keys are accepted."""
    thresholds = dict(C.THRESHOLDS)
    for k, v in (get_setting(conn, "thresholds", {}) or {}).items():
        if k in C.THRESHOLDS and isinstance(v, (int, float)) and not isinstance(v, bool):
            thresholds[k] = float(v)
    wh = dict(C.DEFAULT_WORKING_HOURS)
    stored_wh = get_setting(conn, "working_hours", None)
    if stored_wh:
        wh.update(stored_wh)
    return {"thresholds": thresholds, "working_hours": wh}


# ---------- decisions ----------
def decisions_map(conn) -> dict:
    return {r["rec_id"]: dict(r) for r in conn.execute("SELECT * FROM decisions")}


def set_decision(conn, rec_id: str, status: str, notes: str) -> dict:
    ts = now_iso()
    conn.execute(
        "INSERT INTO decisions (rec_id, status, notes, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(rec_id) DO UPDATE SET status=excluded.status, notes=excluded.notes, "
        "updated_at=excluded.updated_at", (rec_id, status, notes, ts))
    add_audit(conn, f"decision:{status}", rec_id, notes)
    conn.commit()
    return {"rec_id": rec_id, "status": status, "notes": notes, "updated_at": ts}


def reset_accepted(conn) -> int:
    rows = conn.execute("SELECT rec_id FROM decisions WHERE status='accepted'").fetchall()
    conn.execute("DELETE FROM decisions WHERE status='accepted'")
    for r in rows:
        add_audit(conn, "simulation:reset_accepted", r["rec_id"], "removed from simulated plan")
    conn.commit()
    return len(rows)


# ---------- audit ----------
def add_audit(conn, event: str, rec_id: str | None, detail: str) -> None:
    conn.execute("INSERT INTO audit (at, event, rec_id, detail) VALUES (?,?,?,?)",
                 (now_iso(), event, rec_id, detail))


def audit_log(conn, limit: int = 500) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]


# ---------- alert acknowledgements ----------
def acks_map(conn) -> dict:
    return {r["alert_id"]: dict(r) for r in conn.execute("SELECT * FROM alert_acks")}


def ack_alert(conn, alert_id: str, note: str) -> dict:
    ts = now_iso()
    conn.execute("INSERT OR REPLACE INTO alert_acks (alert_id, acked_at, note) VALUES (?,?,?)",
                 (alert_id, ts, note))
    add_audit(conn, "alert:acknowledged", alert_id, note)
    conn.commit()
    return {"alert_id": alert_id, "acked_at": ts, "note": note}
