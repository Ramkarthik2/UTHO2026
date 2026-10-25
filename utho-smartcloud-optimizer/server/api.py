"""HTTP API and static file server (standard library only).

Run:  python3 -m server.api            (serves http://127.0.0.1:8080)
"""
import csv
import io
import json
import mimetypes
import re
import sys
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import analysis, config as C, explain, resource_pdf, store, synth
from .pricing import inr
from .pricing import vm_cost
from .simulate import build_actions, simulate

VALID_DECISIONS = {"open", "accepted", "rejected", "deferred"}
IST_OFFSET_S = 5 * 3600 + 30 * 60
ENVIRONMENT_NAMES = set(C.ENVIRONMENTS)
MAX_NOTE = 1000


class Service:
    """Holds the database path and a cached analysis result."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            synth.generate(self.db_path)
        self._lock = threading.Lock()
        self._result = None

    def conn(self):
        return store.connect(self.db_path)

    def analysis(self, force: bool = False) -> dict:
        with self._lock:
            if self._result is None or force:
                conn = self.conn()
                try:
                    self._result = analysis.run_analysis(conn)
                finally:
                    conn.close()
            return self._result

    def invalidate(self):
        with self._lock:
            self._result = None


# ---------- filter helpers ----------
def _filters(q: dict) -> dict:
    def one(key):
        v = q.get(key, [""])[0].strip()
        return v or None
    return {"region": one("region"), "team": one("team"), "environment": one("environment"),
            "type": one("type")}


def _predicate(f: dict):
    def keep(p):
        return ((not f["region"] or p["region"] == f["region"])
                and (not f["team"] or p["team"] == f["team"])
                and (not f["environment"] or p["environment"] == f["environment"])
                and (not f["type"] or p["type"] == f["type"]))
    return keep


def _has_filter(f: dict) -> bool:
    return any(f.values())


def _date_range(q: dict):
    """Return (start_epoch, end_epoch) for optional start/end (YYYY-MM-DD, IST days, end inclusive)."""
    window_start = int(C.WINDOW_END.timestamp()) - C.OBSERVATION_DAYS * 86400
    window_end = int(C.WINDOW_END.timestamp())
    s = q.get("start", [""])[0]
    e = q.get("end", [""])[0]
    try:
        start = int(datetime.fromisoformat(s).replace(tzinfo=C.IST).timestamp()) if s else window_start
        end = int((datetime.fromisoformat(e).replace(tzinfo=C.IST) + timedelta(days=1)).timestamp()) if e else window_end
    except ValueError as exc:
        raise ValueError("start and end must be YYYY-MM-DD") from exc
    start = max(start, window_start)
    end = min(end, window_end)
    if end <= start:
        raise ValueError("end must be after start within the 30-day window")
    return start, end


def _dates_of(start: int, end: int):
    return [(datetime.fromtimestamp(start, C.IST) + timedelta(days=i)).date().isoformat()
            for i in range((end - start) // 86400)]


def _recs_with_status(result, conn):
    dec = store.decisions_map(conn)
    out = []
    for r in result["recommendations"]:
        rr = dict(r)
        d = dec.get(r["id"])
        rr["status"] = d["status"] if d else "open"
        rr["notes"] = d["notes"] if d else ""
        rr["decided_at"] = d["updated_at"] if d else None
        out.append(rr)
    return out


# ---------- view builders ----------
def overview(svc: Service, q: dict) -> dict:
    res = svc.analysis()
    f = _filters(q)
    keep = _predicate(f)
    start, end = _date_range(q)
    conn = svc.conn()
    try:
        recs = _recs_with_status(res, conn)
        acks = store.acks_map(conn)
        profiles = [p for p in res["profiles"].values() if keep(p)]
        ids = [p["id"] for p in profiles]
        id_set = set(ids)

        def in_scope(r):
            # Orphans are unattributed; they belong to the unfiltered view only.
            if r["environment"] == "Unattributed":
                return not _has_filter(f)
            return r["resource_id"] in id_set

        open_recs = [r for r in recs if r["status"] in ("open", "accepted", "deferred") and in_scope(r)]
        current = simulate(res, [], "current", keep, include_orphans=not _has_filter(f))
        potential_recs = [r for r in recs if r["status"] == "open" and in_scope(r)]
        potential = simulate(res, potential_recs, "all open", keep, include_orphans=not _has_filter(f))
        accepted = [r for r in recs if r["status"] == "accepted" and in_scope(r)]
        plan = simulate(res, accepted, "accepted plan", keep, include_orphans=not _has_filter(f))

        flagged_ids = {r["resource_id"] for r in open_recs}
        if _has_filter(f):
            alerts_scope = [a for a in res["alerts"] if a["resource_id"] in id_set]
        else:
            alerts_scope = res["alerts"]
        alerts_active = [a for a in alerts_scope
                         if a["severity"] in ("critical", "warning") and a["id"] not in acks]
        high_risk = [r for r in open_recs if r["risk"] == "High" and r["status"] == "open"]

        # Distributions and trend come from the raw metrics for the selected date range.
        cpu_buckets, mem_buckets = [0] * 10, [0] * 10
        day_map = {}
        for rid in ids:
            for row in store.metrics_for(conn, rid, start, end):
                cpu_buckets[min(9, int(row["cpu_pct"] // 10))] += 1
                mem_buckets[min(9, int(row["mem_pct"] // 10))] += 1
                day = (row["ts"] + IST_OFFSET_S) // 86400
                d = day_map.setdefault(day, [0.0, 0.0, 0])
                d[0] += row["cpu_pct"]
                d[1] += row["mem_pct"]
                d[2] += 1
        trend = []
        for day in sorted(day_map):
            cpu_sum, mem_sum, n = day_map[day]
            trend.append({"date": datetime.fromtimestamp(day * 86400 - IST_OFFSET_S, C.IST).date().isoformat(),
                          "cpu_mean": round(cpu_sum / n, 2), "mem_mean": round(mem_sum / n, 2)})

        spend_env = {}
        team_region = {}
        for p in profiles:
            spend_env[p["environment"]] = spend_env.get(p["environment"], 0) + p["cost"]["total"]
            key = (p["team"], p["region"])
            team_region[key] = team_region.get(key, 0) + p["cost"]["total"]
        if not _has_filter(f):
            spend_env["Unattributed"] = current["by_environment"].get("Unattributed", {}).get("baseline", 0)

        by_category = {}
        for r in potential_recs:
            if r["type"] in ("collect_more_data",):
                continue
            cat = by_category.setdefault(r["category"], {"savings": 0.0, "count": 0})
            cat["count"] += 1
        for cat_name in by_category:
            by_category[cat_name]["savings"] = round(sum(
                r["savings_monthly"] for r in potential_recs if r["category"] == cat_name), 2)

        budget = res["settings"]["thresholds"]["budget_inr_monthly"]
        kpis = {
            "resources": len(profiles),
            "current_monthly": round(current["baseline_monthly"], 2),
            "projected_monthly_accepted": round(plan["projected_monthly"], 2),
            "potential_savings_monthly": round(potential["savings_monthly"], 2),
            "potential_savings_annual": round(potential["savings_monthly"] * 12, 2),
            "optimization_pct": round(potential["savings_pct"], 1),
            "resources_needing_investigation": len(flagged_ids & id_set) if ids else 0,
            "high_risk_recommendations": len(high_risk),
            "active_alerts": len(alerts_active),
            "open_recommendations": len([r for r in open_recs if r["status"] == "open"]),
        }
        return {
            "filters": f,
            "kpis": kpis,
            "synthetic_notice": "All data is synthetic. All prices are illustrative INR inputs, not cloud quotes.",
            "spend_by_environment": [{"label": k, "value": round(v, 2)} for k, v in sorted(spend_env.items())],
            "spend_by_team_region": [{"team": t, "region": r, "value": round(v, 2)}
                                     for (t, r), v in sorted(team_region.items())],
            "cpu_distribution": cpu_buckets,
            "memory_distribution": mem_buckets,
            "distribution_labels": ["0-10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-70", "70-80", "80-90", "90-100"],
            "trend": trend,
            "current_vs_projected": {"current": round(current["baseline_monthly"], 2),
                                     "projected": round(plan["projected_monthly"], 2),
                                     "accepted_count": len(accepted)},
            "savings_by_category": [{"category": k, **v} for k, v in sorted(by_category.items())],
            "budget": {"monthly_inr": budget, "run_rate_inr": round(current["baseline_monthly"], 2),
                       "warn_ratio": res["settings"]["thresholds"]["budget_warn_ratio"]},
            "window": res["window"],
            "generated_at": res["generated_at"],
        }
    finally:
        conn.close()


def resources_list(svc: Service, q: dict) -> dict:
    res = svc.analysis()
    f = _filters(q)
    keep = _predicate(f)
    text = (q.get("q", [""])[0] or "").lower()
    conn = svc.conn()
    try:
        recs = _recs_with_status(res, conn)
        counts = {}
        for r in recs:
            if r["status"] == "open":
                counts[r["resource_id"]] = counts.get(r["resource_id"], 0) + 1
        out = []
        for p in res["profiles"].values():
            if not keep(p):
                continue
            if text and text not in p["name"].lower() and text not in p["id"].lower():
                continue
            out.append({
                "id": p["id"], "name": p["name"], "type": p["type"], "vcpu": p["vcpu"], "ram_gb": p["ram_gb"],
                "region": p["region"], "environment": p["environment"], "team": p["team"],
                "criticality": p["criticality"], "monthly_inr": p["cost"]["total"],
                "cpu_mean": p["cpu"]["mean"] if p["cpu"] else None,
                "cpu_p95": p["cpu"]["p95"] if p["cpu"] else None,
                "mem_mean": p["mem"]["mean"] if p["mem"] else None,
                "mem_p95": p["mem"]["p95"] if p["mem"] else None,
                "completeness": p["completeness"], "idle_state": p["idle_state"],
                "recommendation_count": counts.get(p["id"], 0),
                "scenario_note": p["scenario_note"],
            })
        sort = q.get("sort", ["monthly_inr"])[0]
        desc = (q.get("dir", ["desc"])[0] == "desc")
        text_keys = ("name", "environment", "region", "team", "type")
        number_keys = ("monthly_inr", "cpu_mean", "cpu_p95", "mem_mean", "completeness", "recommendation_count")
        if sort in text_keys or sort in number_keys:
            # Fixed secondary order (by ID) so rows with equal values keep the same position on every request.
            out.sort(key=lambda r: r["id"])
            if sort in text_keys:
                out.sort(key=lambda r: str(r[sort] or "").lower(), reverse=desc)
            else:
                out.sort(key=lambda r: (r[sort] is None, r[sort] if r[sort] is not None else 0), reverse=desc)
        open_all = [r for r in recs if r["status"] == "open"]
        on_vms = sum(counts.get(pid, 0) for pid in res["profiles"])
        summary = {
            "open_recommendations": len(open_all),
            "open_on_vms": on_vms,
            "open_on_other_resources": len(open_all) - on_vms,
            "open_low_risk": sum(1 for r in open_all if r["risk"] == "Low"),
        }
        return {"count": len(out), "items": out, "summary": summary}
    finally:
        conn.close()


def resource_detail(svc: Service, rid: str, q: dict) -> dict:
    res = svc.analysis()
    p = res["profiles"].get(rid)
    if p is None:
        raise KeyError(rid)
    start, end = _date_range(q)
    conn = svc.conn()
    try:
        rows = store.metrics_for(conn, rid, start, end)
        day_index = {}
        for row in rows:
            d = (row["ts"] - (int(C.WINDOW_END.timestamp()) - C.OBSERVATION_DAYS * 86400)) // 86400
            day_index.setdefault(int(d), []).append(row)
        series_daily = []
        for d in sorted(day_index):
            rr = day_index[d]
            cpu = [r["cpu_pct"] for r in rr if r["cpu_pct"] is not None]
            mem = [r["mem_pct"] for r in rr if r["mem_pct"] is not None]
            disk = [r["disk_pct"] for r in rr if r["disk_pct"] is not None]
            nout = [r["net_out_mbps"] for r in rr if r["net_out_mbps"] is not None]
            ts0 = rr[0]["ts"]
            series_daily.append({
                "date": (datetime.fromtimestamp(ts0, C.IST)).date().isoformat(),
                "cpu_mean": round(sum(cpu) / len(cpu), 2) if cpu else None,
                "cpu_p95": round(_pct(cpu, 95), 2) if cpu else None,
                "cpu_max": round(max(cpu), 2) if cpu else None,
                "mem_mean": round(sum(mem) / len(mem), 2) if mem else None,
                "mem_max": round(max(mem), 2) if mem else None,
                "disk_mean": round(sum(disk) / len(disk), 2) if disk else None,
                "net_out_mean_mbps": round(sum(nout) / len(nout), 2) if nout else None,
            })
        recent = store.metrics_for(conn, rid, end - 7 * 86400, end) if end - start > 0 else []
        recent_series = [{"ts": r["ts"], "cpu": r["cpu_pct"], "mem": r["mem_pct"]} for r in recent[::1]]
        # Weekday vs weekend pattern from the complete window.
        wk, we = [], []
        for r in store.metrics_for(conn, rid):
            local = datetime.fromtimestamp(r["ts"], C.IST)
            (wk if local.weekday() < 5 else we).append(r["cpu_pct"])
        recs = [r for r in _recs_with_status(res, conn) if r["resource_id"] == rid]
        alerts = [a for a in res["alerts"] if a["resource_id"] == rid]
        acks = store.acks_map(conn)
        for a in alerts:
            a["acknowledged"] = a["id"] in acks
        protected = [x for x in res["protected"] if x["resource_id"] == rid]
        return {
            "resource": {k: p[k] for k in ("id", "name", "type", "family", "vcpu", "ram_gb", "region",
                                           "environment", "team", "criticality", "schedule_allowed",
                                           "restriction_note", "scenario_note", "hourly", "volume_ids",
                                           "public_ip_count", "completeness", "valid", "invalid",
                                           "duplicates", "missing", "days_observed", "idle_state",
                                           "cpu", "mem", "disk", "iops_mean", "net_out_mean_mbps",
                                           "cpu_peak_count", "cpu_business_p95", "cpu_offhours_p95",
                                           "cpu_recent_p95", "cpu_early_p95", "hour_profile",
                                           "avg_daily_gb", "cost")},
            "daily": series_daily,
            "recent_15min": recent_series,
            "weekday_cpu_mean": round(sum(wk) / len(wk), 2) if wk else None,
            "weekend_cpu_mean": round(sum(we) / len(we), 2) if we else None,
            "recommendations": recs,
            "alerts": alerts,
            "protected": protected,
            "percentile_help": {
                "p95": "95 out of 100 measurements were at or below this value.",
                "p99": "99 out of 100 measurements were at or below this value. Shows the near-peak load.",
                "max": "The single highest measurement. A useful peak signal, but sensitive to one outlier.",
            },
            "range": {"start": _iso_day(start), "end": _iso_day(end - 1)},
        }
    finally:
        conn.close()


def _pct(vals, p):
    from .stats import percentile
    return percentile(vals, p)


def _iso_day(epoch):
    return datetime.fromtimestamp(epoch, C.IST).date().isoformat()


def recommendations_list(svc: Service, q: dict) -> dict:
    res = svc.analysis()
    conn = svc.conn()
    try:
        recs = _recs_with_status(res, conn)
        f = _filters(q)
        keep = _predicate(f)
        status = q.get("status", [""])[0]
        rtype = q.get("type", [""])[0]
        risk = q.get("risk", [""])[0]
        text = (q.get("q", [""])[0] or "").lower()
        out = []
        for r in recs:
            p = res["profiles"].get(r["resource_id"])
            if p is not None and not keep(p):
                continue
            if status and r["status"] != status:
                continue
            if rtype and r["type"] != rtype:
                continue
            if risk and r["risk"] != risk:
                continue
            if text and text not in r["resource_name"].lower() and text not in r["title"].lower():
                continue
            out.append(_strip_detail(r))
        out.sort(key=lambda r: -r["savings_monthly"])
        return {"count": len(out), "items": out}
    finally:
        conn.close()


def _strip_detail(r):
    return {k: r[k] for k in ("id", "type", "category", "resource_id", "resource_name", "environment",
                               "team", "region", "title", "current", "proposed", "monthly_before",
                               "monthly_after", "savings_monthly", "savings_annual", "savings_pct",
                               "confidence", "confidence_level", "risk", "conditional_savings",
                               "status", "notes", "decided_at", "overlaps_with")}


def recommendation_detail(svc: Service, rec_id: str) -> dict:
    res = svc.analysis()
    conn = svc.conn()
    try:
        recs = _recs_with_status(res, conn)
        rec = next((r for r in recs if r["id"] == rec_id), None)
        if rec is None:
            raise KeyError(rec_id)
        audit = [a for a in store.audit_log(conn, 2000) if a["rec_id"] == rec_id]
        return {"recommendation": rec, "audit": audit}
    finally:
        conn.close()


def decide(svc: Service, rec_id: str, body: dict) -> dict:
    res = svc.analysis()
    if not any(r["id"] == rec_id for r in res["recommendations"]):
        raise KeyError(rec_id)
    status = body.get("status")
    if status not in VALID_DECISIONS or status == "open":
        raise ValueError("status must be accepted, rejected or deferred")
    notes = str(body.get("notes", ""))[:MAX_NOTE]
    conn = svc.conn()
    try:
        saved = store.set_decision(conn, rec_id, status, notes)
    finally:
        conn.close()
    return saved


def protected_list(svc: Service) -> dict:
    res = svc.analysis()
    return {"count": len(res["protected"]), "items": res["protected"],
            "explanation": "Resources that look oversized on average, or that carry risk signals, "
                           "where the engine chose not to recommend a change."}


def alerts_list(svc: Service, q: dict) -> dict:
    res = svc.analysis()
    conn = svc.conn()
    try:
        acks = store.acks_map(conn)
        sev = q.get("severity", [""])[0]
        atype = q.get("type", [""])[0]
        show = q.get("show", ["open"])[0]
        items = []
        for a in res["alerts"]:
            ack = acks.get(a["id"])
            if sev and a["severity"] != sev:
                continue
            if atype and a["type"] != atype:
                continue
            if show == "open" and ack:
                continue
            if show == "acknowledged" and not ack:
                continue
            items.append({**a, "acknowledged": bool(ack), "acked_at": ack["acked_at"] if ack else None,
                          "ack_note": ack["note"] if ack else ""})
        return {"count": len(items), "items": items,
                "basis": "Evaluated on the synthetic snapshot at analysis time; not real-time monitoring."}
    finally:
        conn.close()


def acknowledge(svc: Service, alert_id: str, body: dict) -> dict:
    res = svc.analysis()
    if not any(a["id"] == alert_id for a in res["alerts"]):
        raise KeyError(alert_id)
    conn = svc.conn()
    try:
        return store.ack_alert(conn, alert_id, str(body.get("note", ""))[:MAX_NOTE])
    finally:
        conn.close()


def simulate_view(svc: Service, q: dict) -> dict:
    res = svc.analysis()
    conn = svc.conn()
    try:
        recs = _recs_with_status(res, conn)
    finally:
        conn.close()
    accepted = [r for r in recs if r["status"] == "accepted"]
    open_recs = [r for r in recs if r["status"] == "open"]
    low_risk = [r for r in open_recs if r["risk"] == "Low"]
    everything = open_recs
    plans = {
        "accepted": simulate(res, accepted, "Accepted plan"),
        "low_risk": simulate(res, low_risk, "All low-risk open recommendations"),
        "everything": simulate(res, everything, "Every open recommendation"),
        "baseline": simulate(res, [], "Current baseline"),
    }
    scope = q.get("scope", ["accepted"])[0]
    return {"selected": scope, "plans": plans,
            "pricing_formula": "Monthly = hourly x 730 x active fraction + storage + public IP + egress. "
                               "Schedules use the active fraction from working hours.",
            "realized_savings_note": "Projected savings are not realized savings. Realized savings require "
                                     "actual billing data confirming them."}


def settings_get(svc: Service) -> dict:
    conn = svc.conn()
    try:
        eff = store.effective_settings(conn)
        return {"thresholds": eff["thresholds"], "working_hours": eff["working_hours"],
                "defaults": {"thresholds": C.THRESHOLDS, "working_hours": C.DEFAULT_WORKING_HOURS},
                "editable_thresholds": sorted(C.THRESHOLDS.keys())}
    finally:
        conn.close()


def settings_update(svc: Service, body: dict) -> dict:
    conn = svc.conn()
    try:
        if "thresholds" in body:
            cur = store.get_setting(conn, "thresholds", {}) or {}
            for k, v in body["thresholds"].items():
                if k not in C.THRESHOLDS:
                    raise ValueError(f"unknown threshold {k}")
                if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                    raise ValueError(f"{k} must be a non-negative number")
                if k in ("min_completeness", "budget_warn_ratio") and v > 1:
                    raise ValueError(f"{k} must be between 0 and 1")
                cur[k] = float(v)
            store.set_setting(conn, "thresholds", cur)
        if "working_hours" in body:
            wh = body["working_hours"]
            sh, eh = int(wh["start_hour"]), int(wh["end_hour"])
            days = sorted({int(d) for d in wh["weekdays"]})
            if not (0 <= sh < eh <= 24) or not days or any(d < 0 or d > 6 for d in days):
                raise ValueError("working hours need 0 <= start < end <= 24 and weekdays 0-6")
            store.set_setting(conn, "working_hours", {"start_hour": sh, "end_hour": eh,
                                                      "weekdays": days, "timezone": "Asia/Kolkata"})
        store.add_audit(conn, "settings:updated", None, json.dumps({k: body[k] for k in body if k in ("thresholds", "working_hours")})[:900])
        conn.commit()
    finally:
        conn.close()
    svc.invalidate()
    return settings_get(svc)


def audit_view(svc: Service) -> dict:
    conn = svc.conn()
    try:
        return {"items": store.audit_log(conn, 500)}
    finally:
        conn.close()


def data_quality_view(svc: Service) -> dict:
    return svc.analysis()["data_quality"]


def _open_recs_by_resource(svc: Service):
    res = svc.analysis()
    conn = svc.conn()
    try:
        recs = _recs_with_status(res, conn)
    finally:
        conn.close()
    grouped = {}
    for r in recs:
        if r["status"] == "open":
            grouped.setdefault(r["resource_id"], []).append(r)
    return res, grouped


def ai_resources_view(svc: Service) -> dict:
    _, grouped = _open_recs_by_resource(svc)
    items = []
    for rid, rs in grouped.items():
        top = max(rs, key=lambda r: (r["savings_monthly"], r["id"]))
        items.append({"resource_id": rid, "resource_name": top["resource_name"],
                      "environment": top["environment"], "open_count": len(rs),
                      "top_title": top["title"], "top_savings": top["savings_monthly"]})
    items.sort(key=lambda x: (-x["top_savings"], x["resource_name"]))
    return {"count": len(items), "items": items}


def ai_explain_view(svc: Service, rid: str) -> dict:
    res, grouped = _open_recs_by_resource(svc)
    if rid not in grouped:
        raise KeyError(rid)
    rs = sorted(grouped[rid], key=lambda r: (-r["savings_monthly"], r["id"]))
    profile = res["profiles"].get(rid)
    wh = res["settings"]["working_hours"]
    return {"resource_id": rid, "resource_name": rs[0]["resource_name"],
            "explanations": [{"rec_id": r["id"], "title": r["title"],
                              "lines": explain.explain_recommendation(r, profile, wh)} for r in rs]}


def data_sources_view(svc: Service) -> dict:
    """Read-only description of the loaded dataset. Every value comes from the analysis run."""
    res = svc.analysis()
    dq = res["data_quality"]
    th = res["settings"]["thresholds"]
    firsts = [r["first_ts"] for r in dq["resources"] if r["first_ts"] is not None]
    lasts = [r["last_ts"] for r in dq["resources"] if r["last_ts"] is not None]
    return {
        "source_type": "Synthetic inventory and 15-minute utilization metrics",
        "environment": "Demo / synthetic data",
        "notice": ("Demo data only. Resource metrics and prices are synthetic and do not represent a live "
                   "cloud-provider connection or actual billing data."),
        "window": {"start": analysis._iso(min(firsts)) if firsts else None,
                   "end": analysis._iso(max(lasts)) if lasts else None},
        "interval_minutes": C.INTERVAL_SECONDS // 60,
        "pricing": res["pricing"]["disclaimer"],
        "last_analysis_at": res.get("generated_at"),
        "thresholds": {"min_completeness": th["min_completeness"],
                       "min_observation_days": th["min_observation_days"]},
        "inventory": {
            "resource_count": len(res["profiles"]),
            "regions": sorted({p["region"] for p in res["profiles"].values()}),
            "environments": sorted({p["environment"] for p in res["profiles"].values()}),
            "teams": sorted({p["team"] for p in res["profiles"].values()}),
            "seed": C.SEED,
            "observation_days": C.OBSERVATION_DAYS,
            "expected_intervals_per_resource": C.EXPECTED_INTERVALS,
        },
        "documents": {
            "inventory_pdf": "/api/export/resource-inventory.pdf",
            "inventory_csv": "/api/export/resources.csv",
        },
        "summary": dq["summary"],
        "resources": dq["resources"],
    }


def cost_anomalies_view(svc: Service) -> dict:
    res = svc.analysis()
    return {"items": res["cost_anomalies"], "method": (
        "For each environment, the daily cost series is compared with the previous 14 days (excluding days "
        "already flagged). Flag when z-score >= 3 and change >= 20%. The standard deviation is floored at 1% "
        "of the baseline mean. At least 7 baseline days are required.")}


def csv_bytes(headers, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def export(svc: Service, name: str, q: dict):
    """Return (content_type, bytes, filename)."""
    res = svc.analysis()
    conn = svc.conn()
    try:
        if name == "resources.csv":
            recs = _recs_with_status(res, conn)
            counts = {}
            for r in recs:
                if r["status"] == "open":
                    counts[r["resource_id"]] = counts.get(r["resource_id"], 0) + 1
            rows = []
            for p in res["profiles"].values():
                rows.append([p["id"], p["name"], p["type"], p["vcpu"], p["ram_gb"], p["region"], p["environment"],
                             p["team"], p["criticality"], p["hourly"], p["cost"]["total"],
                             p["cpu"]["mean"], p["cpu"]["p95"], p["cpu"]["p99"], p["cpu"]["max"],
                             p["mem"]["mean"], p["mem"]["p99"], p["mem"]["max"], p["completeness"],
                             p["idle_state"], counts.get(p["id"], 0)])
            return "text/csv", csv_bytes(
                ["resource_id", "name", "type", "vcpu", "ram_gb", "region", "environment", "team",
                 "criticality", "hourly_inr", "monthly_inr", "cpu_mean", "cpu_p95", "cpu_p99", "cpu_max",
                 "mem_mean", "mem_p99", "mem_max", "completeness", "idle_state", "open_recommendations"], rows), name
        if name == "recommendations.csv":
            rows = []
            for r in _recs_with_status(res, conn):
                rows.append([r["id"], r["type"], r["category"], r["resource_id"], r["resource_name"],
                             r["environment"], r["team"], r["region"], r["current"], r["proposed"],
                             r["monthly_before"], r["monthly_after"], r["savings_monthly"],
                             r["savings_annual"], r["savings_pct"], r["confidence"], r["confidence_level"],
                             r["risk"], r["status"], r["conditional_savings"], r["notes"],
                             r["calculation"]])
            return "text/csv", csv_bytes(
                ["recommendation_id", "type", "category", "resource_id", "resource_name", "environment",
                 "team", "region", "current", "proposed", "monthly_before_inr", "monthly_after_inr",
                 "savings_monthly_inr", "savings_annual_inr", "savings_pct", "confidence", "confidence_level",
                 "risk", "status", "savings_conditional", "notes", "calculation"], rows), name
        if name == "metrics.csv":
            f = _filters(q)
            keep = _predicate(f)
            rid = q.get("resource", [""])[0]
            start, end = _date_range(q)
            if not rid and (end - start) > 7 * 86400:
                raise ValueError("Without a resource, the metrics export covers at most 7 days. Pick a resource or shorter range.")
            ids = [rid] if rid else [p["id"] for p in res["profiles"].values() if keep(p)]
            rows = []
            for r_id in ids:
                if r_id not in res["profiles"]:
                    raise KeyError(r_id)
                for row in store.metrics_for(conn, r_id, start, end):
                    rows.append([r_id, datetime.fromtimestamp(row["ts"], C.IST).isoformat(timespec="minutes"),
                                 row["cpu_pct"], row["mem_pct"], row["disk_pct"], row["disk_iops"],
                                 row["net_in_mbps"], row["net_out_mbps"]])
            return "text/csv", csv_bytes(
                ["resource_id", "timestamp_ist", "cpu_pct", "mem_pct", "disk_pct", "disk_iops",
                 "net_in_mbps", "net_out_mbps"], rows), name
        if name == "cost-report.csv":
            recs = _recs_with_status(res, conn)
            accepted = [r for r in recs if r["status"] == "accepted"]
            sim = simulate(res, accepted, "accepted")
            per_res, _, _ = build_actions(accepted)
            rows = []
            for p in res["profiles"].values():
                after = vm_cost(p["base"], per_res.get(p["id"]), res["settings"]["working_hours"])["total"]
                rows.append([p["id"], p["name"], p["environment"], p["team"], p["region"],
                             p["cost"]["total"], after, round(p["cost"]["total"] - after, 2)])
            rows.append(["orphans", "Unattached storage and IPs", "Unattributed", "Unattributed", "Unattributed",
                         round(res["totals"]["orphan_total"], 2), round(sim["projected_monthly"] - sum(r[6] for r in rows), 2),
                         "see accepted plan"])
            return "text/csv", csv_bytes(
                ["resource_id", "name", "environment", "team", "region", "current_monthly_inr",
                 "projected_monthly_inr", "savings_monthly_inr"], rows), name
        if name == "audit.csv":
            rows = [[a["at"], a["event"], a["rec_id"], a["detail"]] for a in store.audit_log(conn, 5000)]
            return "text/csv", csv_bytes(["at_utc", "event", "recommendation_or_alert_id", "detail"], rows), name
        if name == "summary.md":
            return "text/markdown", _summary_markdown(res, conn).encode("utf-8"), name
        raise KeyError(name)
    finally:
        conn.close()


def _summary_markdown(res, conn) -> str:
    recs = _recs_with_status(res, conn)
    accepted = [r for r in recs if r["status"] == "accepted"]
    open_recs = [r for r in recs if r["status"] == "open"]
    base = simulate(res, [], "baseline")
    plan = simulate(res, accepted, "accepted")
    potential = simulate(res, [r for r in recs if r["status"] in ("open", "accepted")], "potential")
    top = sorted(open_recs + accepted, key=lambda r: -r["savings_monthly"])[:5]
    lines = [
        "# UTHO SmartCloud Optimizer: management summary",
        "",
        "> All data is synthetic. All prices are illustrative INR inputs, not cloud-provider quotes.",
        "",
        f"Observation window: {res['window']['start'][:10]} to {res['window']['end'][:10]} "
        f"({res['window']['days']} days, 15-minute measurements).",
        "",
        f"- Estimated monthly cost (synthetic): {inr(base['baseline_monthly'])}",
        f"- Potential monthly savings, all open and accepted recommendations (overlap-adjusted): "
        f"{inr(potential['savings_monthly'])} ({potential['savings_pct']:.1f}%)",
        f"- Accepted plan: {len(accepted)} recommendations, projected monthly cost {inr(plan['projected_monthly'])}, "
        f"projected savings {inr(plan['savings_monthly'])} a month.",
        f"- Projected savings are not realized savings until billing data confirms them.",
        "",
        "## Largest opportunities",
    ]
    for r in top:
        lines.append(f"- {r['title']} ({r['category']}, risk {r['risk']}, confidence {r['confidence']:.0f}): "
                     f"{inr(r['savings_monthly'])} a month")
    lines += ["", "## Protected resources",
              f"{len(res['protected'])} resources were screened out. Typical reasons: peak CPU, memory pressure, "
              "after-hours use, or production approval rules.",
              "", "## Alerts",
              f"{len(res['alerts'])} alerts from the latest analysis (critical, warning and info).",
              "", "## Method and limits",
              "Rightsizing requires low p95 and p99 CPU, peak CPU below 70%, and memory below the pressure "
              "limits. Low-completeness evidence produces a data-collection item, not a savings claim. Storage "
              "and IP charges continue while VMs are stopped. The tool recommends and simulates only; it does "
              "not change cloud resources."]
    return "\n".join(lines) + "\n"


# ---------- HTTP plumbing ----------
class Handler(BaseHTTPRequestHandler):
    service: Service = None
    server_version = "UTHO/1.0"

    def log_message(self, fmt, *args):   # keep console quiet
        return

    def _send(self, status, body: bytes, ctype: str, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        if n > 100_000:
            raise ValueError("request body too large")
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("body must be valid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def _route_get(self, path, q):
        svc = self.service
        if path == "/api/health":
            return 200, {"status": "ok", "synthetic_data": True}
        if path == "/api/meta":
            res = svc.analysis()
            return 200, {"regions": C.REGIONS, "environments": C.ENVIRONMENTS, "teams": C.TEAMS,
                         "types": sorted(C.VM_CATALOG), "window": res["window"], "pricing": res["pricing"],
                         "settings": res["settings"], "synthetic_notice": "Synthetic dataset for a fictional company."}
        if path == "/api/overview":
            return 200, overview(svc, q)
        if path == "/api/resources":
            return 200, resources_list(svc, q)
        m = re.fullmatch(r"/api/resources/([A-Za-z0-9_-]+)", path)
        if m:
            return 200, resource_detail(svc, m.group(1), q)
        if path == "/api/recommendations":
            return 200, recommendations_list(svc, q)
        m = re.fullmatch(r"/api/recommendations/(.+)", path)
        if m:
            return 200, recommendation_detail(svc, m.group(1))
        if path == "/api/protected":
            return 200, protected_list(svc)
        if path == "/api/alerts":
            return 200, alerts_list(svc, q)
        if path == "/api/cost-anomalies":
            return 200, cost_anomalies_view(svc)
        if path == "/api/data-quality":
            return 200, data_quality_view(svc)
        if path == "/api/ai/resources":
            return 200, ai_resources_view(svc)
        if path == "/api/ai/explain":
            return 200, ai_explain_view(svc, (q.get("resource", [""])[0] or "").strip())
        if path == "/api/data-sources":
            return 200, data_sources_view(svc)
        if path == "/api/simulate":
            return 200, simulate_view(svc, q)
        if path == "/api/audit":
            return 200, audit_view(svc)
        if path == "/api/settings":
            return 200, settings_get(svc)
        return None

    def _route_post(self, path, body):
        svc = self.service
        m = re.fullmatch(r"/api/recommendations/(.+)/decision", path)
        if m:
            return 200, decide(svc, m.group(1), body)
        m = re.fullmatch(r"/api/alerts/(.+)/ack", path)
        if m:
            return 200, acknowledge(svc, m.group(1), body)
        if path == "/api/settings":
            return 200, settings_update(svc, body)
        if path == "/api/analysis/run":
            res = svc.analysis(force=True)
            return 200, {"ran_at": res["generated_at"], "duration_ms": res["duration_ms"],
                         "recommendations": len(res["recommendations"]), "alerts": len(res["alerts"]),
                         "protected": len(res["protected"])}
        if path == "/api/simulate/reset":
            conn = svc.conn()
            try:
                n = store.reset_accepted(conn)
            finally:
                conn.close()
            return 200, {"reset_count": n}
        if path == "/api/demo/load":
            summary = synth.generate(svc.db_path)
            conn = svc.conn()
            try:
                store.add_audit(conn, "demo:loaded", None, f"Synthetic scenario regenerated, seed {summary['seed']}")
                conn.commit()
            finally:
                conn.close()
            svc.invalidate()
            res = svc.analysis()
            return 200, {"loaded": True, "summary": summary, "recommendations": len(res["recommendations"])}
        return None

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        path = unquote(parsed.path)
        try:
            if path == "/api/export/resource-inventory.pdf":
                try:
                    body = resource_pdf.build_report(self.service)
                except Exception as exc:  # report the failure; never send a broken file
                    return self._json(500, {"error": f"Could not generate the resource inventory PDF: {exc}"})
                return self._send(200, body, "application/pdf",
                                  {"Content-Disposition": 'inline; filename="utho-resource-inventory.pdf"'})
            if path.startswith("/api/export/"):
                name = path[len("/api/export/"):]
                ctype, body, fname = export(self.service, name, q)
                return self._send(200, body, ctype + "; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{fname}"'})
            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)
            if method == "GET":
                out = self._route_get(path, q)
            else:
                out = self._route_post(path, self._body())
            if out is None:
                return self._json(404, {"error": "not found"})
            return self._json(out[0], out[1])
        except KeyError as exc:
            self._json(404, {"error": f"not found: {exc.args[0] if exc.args else ''}"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:   # pragma: no cover - last-resort guard
            self._json(500, {"error": "internal error", "detail": type(exc).__name__})

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (C.WEB_DIR / rel).resolve()
        if C.WEB_DIR.resolve() not in target.parents and target != C.WEB_DIR.resolve():
            return self._json(404, {"error": "not found"})
        if not target.is_file():
            return self._json(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype + "; charset=utf-8" if ctype.startswith("text") else ctype)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def make_server(host="127.0.0.1", port=8080, db_path: Path = C.DB_PATH):
    svc = Service(db_path)
    svc.analysis()
    handler = type("BoundHandler", (Handler,), {"service": svc})
    return ThreadingHTTPServer((host, port), handler)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    httpd = make_server(port=port)
    print(f"UTHO SmartCloud Optimizer (synthetic data) running at http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
