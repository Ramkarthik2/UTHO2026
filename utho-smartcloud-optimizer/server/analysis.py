"""Optimization analysis engine.

Pipeline:
  1. Validate raw 15-minute measurements (invalid values and duplicates are
     dropped and counted, never treated as zero).
  2. Build a utilization profile per VM (percentiles, business vs off-hours
     usage, daily series, completeness).
  3. Apply the recommendation rules: rightsizing (peak, memory and
     completeness guarded), idle review, off-hours scheduling, orphaned
     volumes and unattached IPs.
  4. Detect cost anomalies (rolling baseline, z-score with a variance floor)
     and build alerts and data-quality findings.

The engine recommends and simulates only. It never changes a cloud resource.
Rules and their reasons are documented in docs/METHODOLOGY.md.
"""
import hashlib
import math
import time
from datetime import datetime, timedelta

from . import config as C
from . import store
from .pricing import (egress_monthly, inr, orphan_ip_monthly, orphan_volume_monthly,
                      schedule_active_fraction, vm_cost)
from .stats import mean, percentile, stdev, summarize

CATEGORY_LABELS = {
    "rightsize": "Rightsizing",
    "schedule_off_hours": "Off-hours scheduling",
    "idle_stop_test": "Idle resource review",
    "orphan_volume": "Orphaned storage",
    "orphan_public_ip": "Unattached IP address",
    "collect_more_data": "Collect more data",
}
CONFLICT_WORDS = ("batch", "nightly", "reconciliation")
GB_PER_INTERVAL_FACTOR = C.INTERVAL_SECONDS / 8.0 / 1024.0   # Mbps -> GB per 15 min


def _hid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, C.IST).isoformat(timespec="minutes")


def _level(score: float) -> str:
    if score >= C.CONFIDENCE_HIGH:
        return "High"
    if score >= C.CONFIDENCE_MEDIUM:
        return "Medium"
    return "Low"


def _longest_run(rows, idx: int, threshold: float):
    """Longest consecutive run of rows with value >= threshold; returns (length, end_ts)."""
    best, best_end, cur = 0, None, 0
    for r in rows:
        if r[idx] >= threshold:
            cur += 1
            if cur > best:
                best, best_end = cur, r[0]
        else:
            cur = 0
    return best, best_end


# ----------------------------------------------------------------------------
# Profile construction
# ----------------------------------------------------------------------------
def _time_index(start: int, th_wh: dict, n: int):
    """Per-interval (is_business, day_index, hour_int) computed once per run."""
    out = []
    weekdays = set(th_wh["weekdays"])
    for i in range(n):
        ts = start + i * C.INTERVAL_SECONDS
        local = datetime.fromtimestamp(ts, C.IST)
        hour = local.hour + local.minute / 60.0
        is_bus = local.weekday() in weekdays and th_wh["start_hour"] <= hour < th_wh["end_hour"]
        out.append((is_bus, i // C.INTERVALS_PER_DAY, local.hour))
    return out


def build_profile(conn, res: dict, volumes: list, ips: list, ctx: dict) -> dict:
    rid = res["resource_id"]
    start, end = ctx["start"], ctx["end"]
    rows = store.metrics_for(conn, rid, start, end)

    seen, valid, invalid, dup = set(), [], 0, 0
    for r in rows:
        ts = r["ts"]
        if ts in seen:
            dup += 1
            continue
        seen.add(ts)
        vals = [r["cpu_pct"], r["mem_pct"], r["disk_pct"], r["disk_iops"],
                r["net_in_mbps"], r["net_out_mbps"]]
        if any(v is None for v in vals):
            invalid += 1
            continue
        cpu, mem, disk, iops, nin, nout = vals
        if not (0 <= cpu <= 100 and 0 <= mem <= 100 and 0 <= disk <= 100
                and iops >= 0 and nin >= 0 and nout >= 0):
            invalid += 1
            continue
        valid.append((ts, cpu, mem, disk, iops, nin, nout))

    n_expected = C.EXPECTED_INTERVALS
    completeness = min(1.0, len(valid) / n_expected)
    missing = max(0, n_expected - len(valid))

    cpu_all = [v[1] for v in valid]
    mem_all = [v[2] for v in valid]
    disk_all = [v[3] for v in valid]
    tidx = ctx["time_index"]

    bus_cpu, off_cpu, hour_bins = [], [], [[] for _ in range(24)]
    days = [dict(cpu=[], mem=[], disk=[], gb=[]) for _ in range(C.OBSERVATION_DAYS)]
    gb_all = []
    for ts, cpu, mem, disk, iops, nin, nout in valid:
        i = (ts - start) // C.INTERVAL_SECONDS
        is_bus, d, hour = tidx[i]
        (bus_cpu if is_bus else off_cpu).append(cpu)
        hour_bins[hour].append(cpu)
        days[d]["cpu"].append(cpu)
        days[d]["mem"].append(mem)
        days[d]["disk"].append(disk)
        gb = nout * GB_PER_INTERVAL_FACTOR
        days[d]["gb"].append(gb)
        gb_all.append(gb)

    avg_gb_per_interval = mean(gb_all) if gb_all else 0.0
    daily = []
    for d, b in enumerate(days):
        if not b["cpu"]:
            daily.append({"day": d, "cpu_mean": None, "cpu_p95": None, "mem_max": None,
                          "disk_mean": None, "egress_gb": None})
            continue
        # Imputation for partial days: scale the observed mean to a full day.
        # A day with no observations stays None (never silently zero).
        daily.append({
            "day": d,
            "cpu_mean": round(mean(b["cpu"]), 2),
            "cpu_p95": round(percentile(b["cpu"], 95), 2),
            "mem_max": round(max(b["mem"]), 2),
            "disk_mean": round(mean(b["disk"]), 2),
            "egress_gb": round(mean(b["gb"]) * C.INTERVALS_PER_DAY, 2),
        })
    day_gb = [x["egress_gb"] for x in daily if x["egress_gb"] is not None]
    avg_daily_gb = mean(day_gb) if day_gb else 0.0

    recent = [c for dd in days[-7:] for c in dd["cpu"]]
    early = [c for dd in days[:-7] for c in dd["cpu"]]

    family, vcpu, ram, hourly = C.VM_CATALOG[res["resource_type"]]
    attached_volumes = [v for v in volumes if v["resource_id"] == rid]
    attached_ips = [ip for ip in ips if ip["resource_id"] == rid]
    storage_gb = sum(v["size_gb"] for v in attached_volumes)
    base = {"hourly": hourly, "storage_gb": storage_gb,
            "public_ip": len(attached_ips) > 0, "egress_monthly": egress_monthly(avg_daily_gb)}
    cost = vm_cost(base, None, ctx["working_hours"])

    profile = {
        "id": rid, "name": res["name"], "type": res["resource_type"], "family": family,
        "vcpu": vcpu, "ram_gb": ram, "region": res["region"], "environment": res["environment"],
        "team": res["team"], "criticality": res["criticality"],
        "schedule_allowed": bool(res["schedule_allowed"]),
        "restriction_note": res["restriction_note"] or "", "scenario_note": res["scenario_note"] or "",
        "hourly": hourly, "base": base, "cost": cost,
        "volume_ids": [v["volume_id"] for v in attached_volumes],
        "public_ip_count": len(attached_ips),
        "completeness": round(completeness, 4), "valid": len(valid), "invalid": invalid,
        "first_ts": min((v[0] for v in valid), default=None), "last_ts": max((v[0] for v in valid), default=None),
        "duplicates": dup, "missing": missing,
        "days_observed": round(len(valid) / C.INTERVALS_PER_DAY, 2),
        "last_ts": valid[-1][0] if valid else None,
        "cpu": summarize(cpu_all) if cpu_all else None,
        "mem": summarize(mem_all) if mem_all else None,
        "disk": summarize(disk_all) if disk_all else None,
        "iops_mean": round(mean([v[4] for v in valid]), 2) if valid else None,
        "net_out_mean_mbps": round(mean([v[6] for v in valid]), 2) if valid else None,
        "cpu_peak_count": sum(1 for c in cpu_all if c >= th_peak(ctx)),
        "cpu_business_p95": round(percentile(bus_cpu, 95), 2) if bus_cpu else None,
        "cpu_offhours_p95": round(percentile(off_cpu, 95), 2) if off_cpu else None,
        "cpu_recent_p95": round(percentile(recent, 95), 2) if recent else None,
        "cpu_early_p95": round(percentile(early, 95), 2) if early else None,
        "hour_profile": [round(mean(h), 2) if h else None for h in hour_bins],
        "daily": daily,
        "avg_daily_gb": round(avg_daily_gb, 2),
        "cv_cpu": (stdev(cpu_all) / max(mean(cpu_all), 0.1)) if cpu_all else None,
    }
    profile["idle_state"] = _idle_state(profile, ctx["th"])
    profile["cpu_longest_spike"] = _longest_run(valid, 1, ctx["th"]["alert_cpu_spike"])
    profile["mem_longest_high"] = _longest_run(valid, 2, ctx["th"]["alert_mem_high"])
    profile["disk_trend_per_day"] = _disk_slope(daily)
    return profile


def th_peak(ctx) -> float:
    return ctx["th"]["rightsize_cpu_peak_max"]


def _disk_slope(daily):
    pts = [(x["day"], x["disk_mean"]) for x in daily if x["disk_mean"] is not None]
    if len(pts) < 7:
        return 0.0
    first = mean([v for d, v in pts[:7]])
    last = mean([v for d, v in pts[-7:]])
    span = pts[-1][0] - pts[0][0]
    return (last - first) / span if span else 0.0


def _idle_state(p: dict, th: dict) -> str:
    if p["completeness"] < th["min_completeness"] or p["cpu"] is None:
        return "insufficient_data"
    if p["cpu_recent_p95"] is not None and p["cpu_early_p95"] is not None:
        if p["cpu_recent_p95"] < th["idle_cpu_p95_max"] and p["cpu_early_p95"] >= th["idle_cpu_p95_max"]:
            return "temporarily_idle"
    if p["cpu"]["p95"] < th["idle_cpu_p95_max"]:
        return "consistently_idle"
    bus, off = p["cpu_business_p95"], p["cpu_offhours_p95"]
    if bus is not None and off is not None and bus >= th["offhours_min_business_cpu_p95"] \
            and off < th["offhours_max_cpu_p95"]:
        return "idle_outside_hours"
    return "active"


# ----------------------------------------------------------------------------
# Confidence and explanation helpers
# ----------------------------------------------------------------------------
def _confidence(p: dict, th: dict, margin_ratio: float) -> float:
    """Documented scoring (docs/METHODOLOGY.md, section 5). Starts at 100 and subtracts penalties."""
    score = 100.0
    score -= max(0.0, 0.95 - p["completeness"]) * 150.0        # missing evidence
    cv = p["cv_cpu"] or 0.0
    if cv > 1.0:
        score -= 10
    elif cv > 0.5:
        score -= 5
    score -= min(10.0, 2.0 * (p["invalid"] + p["duplicates"]))  # data-quality issues
    if margin_ratio < 0.25:                                     # thin headroom below limit
        score -= 10
    if p["environment"] == "Production":
        score -= 10
    return round(max(0.0, min(100.0, score)), 1)


def _rec(p, rtype, title, current, proposed, before, after, confidence, risk, conditional,
         evidence, assumptions, preconditions, validation, risks, explanation, formula):
    savings = round(before - after, 2)
    return {
        "id": f"{rtype}:{p['id']}",
        "type": rtype,
        "category": CATEGORY_LABELS[rtype],
        "resource_id": p["id"],
        "resource_name": p["name"],
        "environment": p["environment"],
        "team": p["team"],
        "region": p["region"],
        "title": title,
        "current": current,
        "proposed": proposed,
        "monthly_before": round(before, 2),
        "monthly_after": round(after, 2),
        "savings_monthly": savings,
        "savings_annual": round(savings * 12, 2),
        "savings_pct": round(savings / before * 100, 1) if before else 0.0,
        "confidence": confidence,
        "confidence_level": _level(confidence),
        "risk": risk,
        "conditional_savings": conditional,
        "evidence": evidence,
        "assumptions": assumptions,
        "preconditions": preconditions,
        "validation_steps": validation,
        "what_could_go_wrong": risks,
        "explanation": explanation,
        "calculation": formula,
        "overlaps_with": [],
    }


# ----------------------------------------------------------------------------
# Recommendation rules
# ----------------------------------------------------------------------------
def _rightsize_gate(p: dict, th: dict):
    """Return (blocks, proposed_type). blocks is a list of reasons that prevent a claim."""
    blocks = []
    cpu, mem = p["cpu"], p["mem"]
    if cpu["p95"] >= th["rightsize_cpu_p95_max"]:
        blocks.append(f"95th-percentile CPU is {cpu['p95']:.1f}%, above the {th['rightsize_cpu_p95_max']:.0f}% steady-low limit.")
    if cpu["p99"] >= th["rightsize_cpu_p99_max"]:
        blocks.append(f"99th-percentile CPU is {cpu['p99']:.1f}%, above the {th['rightsize_cpu_p99_max']:.0f}% limit.")
    if cpu["max"] >= th["rightsize_cpu_peak_max"]:
        blocks.append(f"Peak CPU reached {cpu['max']:.1f}%, above the {th['rightsize_cpu_peak_max']:.0f}% peak guard. "
                      "A smaller size could saturate during peaks.")
    if mem["p99"] >= th["rightsize_mem_p99_max"]:
        blocks.append(f"99th-percentile memory is {mem['p99']:.1f}%, above the {th['rightsize_mem_p99_max']:.0f}% memory-pressure limit.")
    if mem["max"] >= th["rightsize_mem_peak_max"]:
        blocks.append(f"Peak memory reached {mem['max']:.1f}%, above the {th['rightsize_mem_peak_max']:.0f}% limit.")
    if blocks:
        return blocks, None

    family, vcpu, ram, hourly = C.VM_CATALOG[p["type"]]
    need_vcpu = max(1, math.ceil(vcpu * cpu["p99"] / th["target_cpu_util"]))
    need_ram = max(1, math.ceil(ram * mem["max"] / th["target_mem_util"]))
    options = [(price, name) for name, (fam, vc, rm, price) in C.VM_CATALOG.items()
               if fam == family and vc >= need_vcpu and rm >= need_ram and price < hourly]
    if not options:
        return [f"No smaller {family} size has at least {need_vcpu} vCPU and {need_ram} GB RAM."], None
    proposed = min(options)[1]
    return [], proposed


def _rightsize_recs(p: dict, th: dict, out: dict):
    if p["cpu"] is None:
        return
    cpu, mem = p["cpu"], p["mem"]
    naive_flag = cpu["mean"] < 20.0       # what an average-only rule would flag
    blocks, proposed = _rightsize_gate(p, th)
    mem_constrained = mem["p99"] >= th["rightsize_mem_p99_max"]
    if blocks:
        if naive_flag or mem_constrained:
            out["protected"].append({
                "resource_id": p["id"], "resource_name": p["name"], "environment": p["environment"],
                "check": "rightsizing", "verdict": "Do not downsize",
                "reasons": blocks,
                "evidence": {"cpu_mean": cpu["mean"], "cpu_p95": cpu["p95"], "cpu_p99": cpu["p99"],
                             "cpu_max": cpu["max"], "mem_p99": mem["p99"], "mem_max": mem["max"]},
            })
        return
    if proposed is None:
        return

    cur_family, vcpu, ram, hourly = C.VM_CATALOG[p["type"]]
    new_hourly = C.VM_CATALOG[proposed][3]
    new_vcpu, new_ram = C.VM_CATALOG[proposed][1], C.VM_CATALOG[proposed][2]
    new_cpu_util = cpu["p99"] * vcpu / new_vcpu
    new_mem_util = mem["max"] * ram / new_ram
    before = vm_cost(p["base"], None, th_wh(th))["total"]
    after = vm_cost(p["base"], {"resize_to": proposed}, th_wh(th))["total"]
    margin = (th["rightsize_cpu_p99_max"] - cpu["p99"]) / th["rightsize_cpu_p99_max"]
    confidence = _confidence(p, th, margin)
    if p["environment"] == "Production":
        risk = "High"
    elif mem["max"] >= 60 or p["environment"] == "Staging" or _level(confidence) != "High":
        risk = "Medium"
    else:
        risk = "Low"

    evidence = [
        f"Average CPU {cpu['mean']:.1f}%, median {cpu['p50']:.1f}%.",
        f"95th-percentile CPU {cpu['p95']:.1f}%, 99th-percentile {cpu['p99']:.1f}%, peak {cpu['max']:.1f}%.",
        f"Memory 99th-percentile {mem['p99']:.1f}%, peak {mem['max']:.1f}%.",
        f"Observed for {p['days_observed']:.1f} days with {p['completeness']*100:.1f}% completeness.",
        f"Estimated utilisation after change: about {new_cpu_util:.0f}% CPU and {new_mem_util:.0f}% memory at peak.",
    ]
    assumptions = [
        "Future load resembles the 30-day observed pattern.",
        f"Synthetic prices: {p['type']} at {inr(hourly)}/h vs {proposed} at {inr(new_hourly)}/h; 730 modelled hours a month.",
        "Attached storage and egress are unchanged by the resize.",
    ]
    preconditions = [
        "Owner confirms no planned load increase in the next 30 days.",
        "Snapshot or machine image taken before any change.",
        "Change scheduled in a maintenance window; the VM restarts.",
        "Monitor for 7 days after the change before closing the recommendation.",
    ]
    validation = [
        f"Run a controlled test on {proposed} for at least 7 days.",
        "Confirm p99 CPU stays below 70% and peak memory below 75% after the change.",
        "Confirm no new out-of-memory events or latency regressions.",
    ]
    risks = [
        "A load peak larger than anything observed in the 30-day window.",
        "A release or new feature that increases memory use.",
        "Batch or background jobs that need headroom the sample did not capture.",
    ]
    explanation = (
        f"This {p['environment'].lower()} VM averaged {cpu['mean']:.1f}% CPU over "
        f"{p['days_observed']:.0f} days. Its 95th-percentile CPU was {cpu['p95']:.1f}% and its peak was "
        f"{cpu['max']:.1f}%. Peak memory reached {mem['max']:.1f}%. Moving from {p['type']} to {proposed} "
        f"would put the 99th-percentile CPU at about {new_cpu_util:.0f}% of the new capacity. "
        f"The estimated saving is {inr(before - after)} a month, based on the synthetic price assumptions, "
        "not a live cloud quote."
    )
    formula = (f"Savings = ({inr(hourly)} - {inr(new_hourly)}) per hour x 730 h = {inr(before - after)} per month. "
               f"Sizing: need {need_vcpu_for(p, th)} vCPU (p99 {cpu['p99']:.1f}% / {th['target_cpu_util']:.0f}% target) "
               f"and {need_ram_for(p, th)} GB RAM (peak {mem['max']:.1f}% / {th['target_mem_util']:.0f}% target).")
    rec = _rec(p, "rightsize", f"Downsize {p['name']} to {proposed}", p["type"], proposed,
               before, after, confidence, risk, False, evidence, assumptions, preconditions,
               validation, risks, explanation, formula)
    if p["completeness"] < th["min_completeness"]:
        rec = _as_collect_more_data(p, th, rec)
    out["recommendations"].append(rec)


def need_vcpu_for(p, th):
    vcpu = C.VM_CATALOG[p["type"]][1]
    return max(1, math.ceil(vcpu * p["cpu"]["p99"] / th["target_cpu_util"]))


def need_ram_for(p, th):
    ram = C.VM_CATALOG[p["type"]][2]
    return max(1, math.ceil(ram * p["mem"]["max"] / th["target_mem_util"]))


def th_wh(th):
    return th["_working_hours"]


def _as_collect_more_data(p, th, rec):
    """Low-evidence case: do not claim savings. Ask for a controlled test or more data."""
    confidence = min(rec["confidence"], 55.0)
    return _rec(
        p, "collect_more_data",
        f"Collect more data before judging {p['name']}",
        p["type"], rec["proposed"], rec["monthly_before"], rec["monthly_before"], confidence, "Low", False,
        [f"Only {p['completeness']*100:.1f}% of expected 15-minute measurements are present "
         f"({p['missing']} missing).",
         f"The available data suggests a possible downsize to {rec['proposed']}, but the gap is too large to rely on.",
         "Savings are not claimed for this item."],
        ["Missing intervals are not treated as zero; the statistics use observed values only."],
        ["Restore the missing measurement feed and recheck the 30-day window."],
        ["Run a controlled test on the proposed size and compare metrics over 7 days."],
        ["Without complete data, a downsize could hide a real peak."],
        (f"Only {p['completeness']*100:.1f}% of the expected measurements are present, so the sizing evidence is "
         "not reliable enough for a savings claim. The system recommends collecting more data first."),
        "Savings are not calculated for this item (set to ₹0).")


def _idle_rec(p, th, out):
    state = p["idle_state"]
    if state == "consistently_idle":
        if p["environment"] == "Production":
            out["protected"].append({
                "resource_id": p["id"], "resource_name": p["name"], "environment": p["environment"],
                "check": "idle", "verdict": "Confirm with owner",
                "reasons": ["Production VM with near-zero CPU. Confirm the owner and dependencies before any action."],
                "evidence": {"cpu_p95": p["cpu"]["p95"]}})
            return
        before = p["cost"]["total"]
        after = vm_cost(p["base"], {"stop": True}, th_wh(th))["total"]
        conf = _confidence(p, th, 0.5)
        rec = _rec(p, "idle_stop_test", f"Review {p['name']} for a stop test",
                   p["type"], "stopped (stop test, not delete)", before, after, conf, "Medium", True,
                   [f"95th-percentile CPU is {p['cpu']['p95']:.1f}% over {p['days_observed']:.0f} days.",
                    f"Memory averages {p['mem']['mean']:.1f}%.",
                    "Idle for the full window: no business-hours or after-hours peaks."],
                   ["Savings count compute only. Storage and IP charges continue while the VM is stopped."],
                   ["Owner confirms the VM is not needed.", "Snapshot taken before stopping.",
                    "Check for scheduled jobs, cron tasks, and inbound dependencies."],
                   ["Stop for 14 days and watch for errors or support tickets.",
                    "Delete only after the stop test and a retention review."],
                   ["A dependency or scheduled job that runs rarely.",
                    "A DR or failover role that looks idle until it is needed."],
                   f"This VM stayed near idle all window (95th-percentile CPU {p['cpu']['p95']:.1f}%). "
                   "A stop test can confirm whether anything depends on it. The estimated saving counts compute only, "
                   f"which is {inr(before - after)} a month on synthetic prices.",
                   "Compute = hourly price x 730 x 0 (stopped). Storage and IP remain.")
        out["recommendations"].append(rec)
    elif state == "temporarily_idle":
        out.setdefault("temp_idle", []).append(p["id"])


def _schedule_rec(p, th, out):
    wh = th["_working_hours"]
    if not p["schedule_allowed"]:
        return
    conflict = p["environment"] == "Production" or any(w in p["restriction_note"].lower() for w in CONFLICT_WORDS)
    if p["environment"] not in ("Development", "Staging") or p["criticality"] not in ("Low", "Medium"):
        out["protected"].append({"resource_id": p["id"], "resource_name": p["name"], "environment": p["environment"],
                                 "check": "schedule", "verdict": "Not schedulable",
                                 "reasons": ["Production or high-criticality VMs are never scheduled without explicit approval."],
                                 "evidence": {}})
        return
    if conflict:
        out["protected"].append({"resource_id": p["id"], "resource_name": p["name"], "environment": p["environment"],
                                 "check": "schedule", "verdict": "Conflicting restriction",
                                 "reasons": [f"The restriction note conflicts with scheduling: '{p['restriction_note']}'."],
                                 "evidence": {}})
        return
    bus, off = p["cpu_business_p95"], p["cpu_offhours_p95"]
    if p["completeness"] < th["min_completeness"]:
        return
    if off is None or bus is None:
        return
    if off >= th["offhours_max_cpu_p95"]:
        out["protected"].append({"resource_id": p["id"], "resource_name": p["name"], "environment": p["environment"],
                                 "check": "schedule", "verdict": "Used after hours",
                                 "reasons": [f"After-hours 95th-percentile CPU is {off:.1f}%, above the "
                                             f"{th['offhours_max_cpu_p95']:.0f}% limit. The VM is used outside working hours."],
                                 "evidence": {"cpu_offhours_p95": off}})
        return
    if bus is None or bus < th["offhours_min_business_cpu_p95"]:
        out["protected"].append({"resource_id": p["id"], "resource_name": p["name"], "environment": p["environment"],
                                 "check": "schedule", "verdict": "No business-hours use",
                                 "reasons": ["The VM shows little use in working hours, so a schedule would save little. "
                                             "Treat it under idle review instead."],
                                 "evidence": {"cpu_business_p95": bus}})
        return
    frac = schedule_active_fraction(wh)
    before = vm_cost(p["base"], None, wh)["total"]
    after = vm_cost(p["base"], {"schedule": True}, wh)["total"]
    margin = (th["offhours_max_cpu_p95"] - off) / th["offhours_max_cpu_p95"]
    conf = _confidence(p, th, margin)
    risk = "Medium" if p["environment"] == "Staging" else "Low"
    start, end = wh["start_hour"], wh["end_hour"]
    sched_text = f"Stop outside {start:02d}:00-{end:02d}:00 IST on weekdays and all weekends"
    rec = _rec(
        p, "schedule_off_hours", f"Schedule {p['name']} to run in working hours",
        "Running 24x7", sched_text, before, after, conf, risk, False,
        [f"Business-hours 95th-percentile CPU is {bus:.1f}%, so the VM is in use during the day.",
         f"After-hours 95th-percentile CPU is {off:.1f}%, below the {th['offhours_max_cpu_p95']:.0f}% validation limit.",
         f"Completeness {p['completeness']*100:.1f}%."],
        ["Schedule is active for the configured working hours only; the VM is stopped at other times.",
         f"Scheduled share of a 730-hour month: {frac*100:.1f}%."],
        ["Owner approves the schedule and the working-hours window.",
         "Confirm there are no nightly jobs or time-zone-dependent consumers."],
        ["Run the schedule in dry-run or notify-only mode for one week.",
         "Check for any after-hours alerts or support requests."],
        ["A nightly job or a partner in another time zone may need the VM.",
         "Stopped VMs take longer to start and can miss a health check."],
        f"Off-hours CPU stays low (95th-percentile {off:.1f}%), but business hours are busy ({bus:.1f}%). "
        f"Stopping the VM outside working hours avoids idle compute charges. Estimated saving "
        f"{inr(before - after)} a month on synthetic prices. Block storage and IP charges still apply.",
        f"Savings = {inr(p['hourly'])}/h x 730 h x (1 - {frac:.4f}) = {inr(before - after)} per month. "
        "Storage and IP charges are excluded from the saving.")
    out["recommendations"].append(rec)


def _orphan_recs(volumes, ips, th, window_end, out):
    for v in volumes:
        if v["resource_id"] is not None or not v["detached_at"]:
            continue
        detached = datetime.fromisoformat(v["detached_at"])
        days = (window_end - detached).days
        monthly = orphan_volume_monthly(v["size_gb"])
        pseudo = {"id": v["volume_id"], "name": v["volume_id"], "environment": "Unattributed",
                  "team": "Unattributed", "region": "Unattributed", "completeness": 1.0}
        if days < th["orphan_min_detached_days"]:
            out["protected"].append({"resource_id": v["volume_id"], "resource_name": v["volume_id"],
                                     "environment": "Unattributed", "check": "orphan_volume",
                                     "verdict": "Waiting period",
                                     "reasons": [f"Detached for {days} days, below the {th['orphan_min_detached_days']:.0f}-day rule."],
                                     "evidence": {"size_gb": v["size_gb"]}})
            continue
        conf = 75.0 if days >= 30 else 65.0
        rec = _rec(
            pseudo, "orphan_volume", f"Review orphaned volume {v['volume_id']}",
            f"{v['size_gb']} GB detached for {days} days", "Delete after snapshot and retention check",
            monthly, 0.0, conf, "Medium", True,
            [f"No attachment since {detached.date().isoformat()} ({days} days).",
             f"Size {v['size_gb']} GB at ₹{C.STORAGE_PER_GB_MONTH_INR:.0f}/GB-month on synthetic prices."],
            ["Savings apply only after the volume is deleted, and only if it is not needed for recovery."],
            ["Confirm owner and business purpose.", "Check retention policy and backup or snapshot status.",
             "Check for any DR or compliance hold."],
            ["Take a final snapshot, then review it for 7 days.", "Delete only after approval."],
            ["Data needed for audit or recovery is deleted.", "A volume detached for a planned migration."],
            f"This volume has had no attachment for {days} days and costs {inr(monthly)} a month on synthetic prices. "
            "It is a cleanup candidate, but deleting it is irreversible. Verify retention before any action.",
            f"Monthly cost = {v['size_gb']} GB x ₹{C.STORAGE_PER_GB_MONTH_INR:.0f}/GB = {inr(monthly)}.")
        rec["id"] = f"orphan_volume:{v['volume_id']}"
        rec["resource_id"] = v["volume_id"]
        rec["resource_name"] = v["volume_id"]
        rec["volume_id"] = v["volume_id"]
        out["recommendations"].append(rec)

    for ip in ips:
        if ip["resource_id"] is not None or not ip["detached_at"]:
            continue
        detached = datetime.fromisoformat(ip["detached_at"])
        days = (window_end - detached).days
        monthly = orphan_ip_monthly()
        pseudo = {"id": ip["ip_id"], "name": ip["address"], "environment": "Unattributed",
                  "team": "Unattributed", "region": "Unattributed", "completeness": 1.0}
        if days < th["ip_unattached_min_days"]:
            out["protected"].append({"resource_id": ip["ip_id"], "resource_name": ip["address"],
                                     "environment": "Unattributed", "check": "orphan_ip", "verdict": "Waiting period",
                                     "reasons": [f"Unattached for {days} days, below the "
                                                 f"{th['ip_unattached_min_days']:.0f}-day rule."],
                                     "evidence": {}})
            continue
        rec = _rec(
            pseudo, "orphan_public_ip", f"Review unattached IP {ip['address']}",
            f"Unattached for {days} days", "Release after DNS and firewall check",
            monthly, 0.0, 70.0, "Low", True,
            [f"Not attached to any VM for {days} days.", "Costs the same whether attached or not on synthetic prices."],
            ["Savings apply only after release, and only if no DNS record or allow-list uses the address."],
            ["Search DNS, load balancers, and firewall rules for this address.", "Confirm the owner."],
            ["Keep a reserved placeholder for 7 days, then release."],
            ["A DNS record or partner allow-list still points to the address."],
            f"This public IP has been unattached for {days} days and costs {inr(monthly)} a month on synthetic prices. "
            "Check DNS and allow-lists before releasing it.",
            f"Monthly cost = {inr(monthly)} (flat synthetic rate).")
        rec["id"] = f"orphan_public_ip:{ip['ip_id']}"
        rec["resource_id"] = ip["ip_id"]
        rec["resource_name"] = ip["address"]
        out["recommendations"].append(rec)


# ----------------------------------------------------------------------------
# Cost anomalies (environment level, rolling baseline)
def _anomaly_severity(z_score):
    # Single rule used by the anomaly summary and the Alerts table.
    return "critical" if z_score >= 5 else "warning"


# ----------------------------------------------------------------------------
def cost_anomalies(profiles: dict, th: dict):
    envs = {}
    for p in profiles.values():
        envs.setdefault(p["environment"], []).append(p)
    results = []
    for env, plist in sorted(envs.items()):
        daily_cost = {}
        for p in plist:
            series = []
            for d in range(C.OBSERVATION_DAYS):
                gb = p["daily"][d]["egress_gb"]
                gb = gb if gb is not None else p["avg_daily_gb"]     # imputed, flagged in docs
                c = (p["hourly"] * 24 + storage_monthly_safe(p["base"]) / C.DAYS_PER_MONTH
                     + (C.PUBLIC_IP_PER_MONTH_INR / C.DAYS_PER_MONTH if p["base"]["public_ip"] else 0)
                     + gb * C.EGRESS_PER_GB_INR)
                series.append(c)
            daily_cost[p["id"]] = series
        total = [sum(daily_cost[p["id"]][d] for p in plist) for d in range(C.OBSERVATION_DAYS)]
        flagged = set()
        for d in range(th["anomaly_min_baseline_days"], C.OBSERVATION_DAYS):
            lookback = [x for x in range(max(0, d - int(th["anomaly_baseline_days"])), d) if x not in flagged]
            if len(lookback) < th["anomaly_min_baseline_days"]:
                continue
            base_vals = [total[x] for x in lookback]
            bmean = mean(base_vals)
            bstd = max(stdev(base_vals), bmean * th["anomaly_std_floor_pct"] / 100.0)
            z = (total[d] - bmean) / bstd
            pct = (total[d] - bmean) / bmean * 100.0
            if z >= th["anomaly_z"] and pct >= th["anomaly_min_pct"]:
                flagged.add(d)
                contributors = []
                for p in plist:
                    s = daily_cost[p["id"]]
                    delta = s[d] - mean([s[x] for x in lookback])
                    if delta > 0:
                        contributors.append({"resource_id": p["id"], "resource_name": p["name"],
                                             "delta_inr": round(delta, 2)})
                contributors.sort(key=lambda c: -c["delta_inr"])
                contributors_total = len(contributors)
                day_date = (C.WINDOW_END - timedelta(days=C.OBSERVATION_DAYS - d)).date().isoformat()
                results.append({
                    "id": _hid("cost", env, d), "environment": env, "date": day_date,
                    "observed_inr": round(total[d], 2), "baseline_inr": round(bmean, 2),
                    "abs_change_inr": round(total[d] - bmean, 2), "pct_change": round(pct, 1),
                    "z_score": round(z, 2), "baseline_days": len(lookback),
                    "severity": _anomaly_severity(z), "contributors_total": contributors_total,
                    "contributors": contributors[:3],
                    "basis": "Synthetic pricing assumptions and observed egress; not a billing record.",
                    "suggested_steps": [
                        "Inspect the largest contributing resource first.",
                        "Review recent deployments and network egress activity.",
                        "Compare the increase with planned workloads and data transfers.",
                        "Decide whether the increase is expected. If it is not, review the cost optimization recommendations.",
                        "Nothing is changed automatically. Any resource change needs your approval.",
                    ],
                })
    return results


def storage_monthly_safe(base):
    return base["storage_gb"] * C.STORAGE_PER_GB_MONTH_INR


# ----------------------------------------------------------------------------
# Alerts and data quality
# ----------------------------------------------------------------------------
def build_alerts(profiles, orphan_volumes, orphan_ips, cost_anoms, totals, th, window_end, now_epoch):
    alerts = []

    def add(severity, atype, p_id, p_name, title, detail, observed_at, steps):
        alerts.append({
            "id": _hid("alert", atype, p_id, observed_at),
            "severity": severity, "type": atype, "resource_id": p_id, "resource_name": p_name,
            "title": title, "detail": detail, "observed_at": observed_at, "next_steps": steps,
            "basis": "Evaluated on the synthetic 30-day snapshot at analysis time, not a live stream.",
        })

    for p in profiles.values():
        rid, name = p["id"], p["name"]
        length, end_ts = p["cpu_longest_spike"]
        if length >= th["alert_cpu_spike_intervals"]:
            sev = "critical" if length >= 12 else "warning"
            add(sev, "cpu_spike", rid, name, "Sustained CPU spike",
                f"CPU stayed at or above {th['alert_cpu_spike']:.0f}% for {length * 15} minutes (longest run).",
                _iso(end_ts), ["Check for a recent deployment or traffic event.",
                               "Confirm autoscaling or queue depth response."])
        length, end_ts = p["mem_longest_high"]
        if length >= th["alert_mem_high_intervals"]:
            add("warning", "memory_pressure", rid, name, "Sustained memory pressure",
                f"Memory stayed at or above {th['alert_mem_high']:.0f}% for {length * 15} minutes (longest run).",
                _iso(end_ts), ["Check for leaks or a growing cache.", "Do not downsize this VM until the cause is known."])
        last_disk = p["daily"][-1]["disk_mean"]
        if last_disk is not None and last_disk >= th["alert_disk_limit"]:
            slope = p["disk_trend_per_day"]
            trend = (f"Trend is +{slope:.2f} points per day." if slope > 0 else "Trend is flat or falling.")
            add("warning", "storage_limit", rid, name, "Disk approaching configured limit",
                f"Disk usage is {last_disk:.1f}% against a {th['alert_disk_limit']:.0f}% limit. {trend}",
                _iso(p["last_ts"]), ["Plan a volume expansion or log cleanup.", "Confirm retention rules."])
        if p["completeness"] < th["min_completeness"]:
            add("warning", "incomplete_metrics", rid, name, "Incomplete metrics",
                f"Only {p['completeness']*100:.1f}% of expected measurements are present. "
                "Recommendations for this VM are low-confidence.",
                _iso(p["last_ts"]) if p["last_ts"] else _iso(int(window_end.timestamp())),
                ["Check the metrics agent and collection pipeline.", "Re-run analysis after the gap is fixed."])
        if p["last_ts"] is None or p["last_ts"] < int(window_end.timestamp()) - th["alert_stale_hours"] * 3600:
            add("critical", "stale_metrics", rid, name, "Stale metrics",
                "No recent measurement within the staleness window.",
                _iso(p["last_ts"]) if p["last_ts"] else "never", ["Verify the agent is running."])
        if p["idle_state"] == "temporarily_idle":
            add("info", "persistent_low_utilization", rid, name, "Recently idle",
                f"95th-percentile CPU over the last 7 days is {p['cpu_recent_p95']:.1f}%, after earlier activity. "
                "Review before changing.", _iso(int(window_end.timestamp())),
                ["Check whether the workload has moved or paused."])
        if p["idle_state"] == "consistently_idle" and p["environment"] != "Production":
            add("info", "persistent_low_utilization", rid, name, "Persistent low utilization",
                f"95th-percentile CPU is {p['cpu']['p95']:.1f}% for the full window.",
                _iso(int(window_end.timestamp())), ["Review the idle recommendation."])

    for a in cost_anoms:
        add(_anomaly_severity(a["z_score"]), "cost_anomaly", a["environment"], a["environment"] + " environment",
            "Unusual cost increase",
            f"{a['date']}: {inr(a['observed_inr'])} vs baseline {inr(a['baseline_inr'])} "
            f"({a['pct_change']:+.1f}%, z={a['z_score']}).",
            a["date"] + "T00:00:00+05:30", a["suggested_steps"])

    spend = totals["monthly_total"]
    budget = th["budget_inr_monthly"]
    if spend > budget:
        add("critical", "budget_overrun", "account", "Account", "Projected spend exceeds budget",
            f"Current run-rate {inr(spend)} a month exceeds the {inr(budget)} budget by {inr(spend - budget)}.",
            _iso(int(window_end.timestamp())), ["Review accepted plan in the Cost Simulator.", "Approve or defer top recommendations."])
    elif spend >= budget * th["budget_warn_ratio"]:
        add("warning", "budget_overrun", "account", "Account", "Approaching budget",
            f"Run-rate {inr(spend)} a month is {spend / budget * 100:.0f}% of the {inr(budget)} budget.",
            _iso(int(window_end.timestamp())), ["Review open recommendations."])

    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (order[a["severity"]], a["observed_at"]), reverse=False)
    return alerts


def data_quality(profiles: dict, th: dict):
    """Single source of truth for the Data Sources page and the analysis flags."""
    per = []
    for p in profiles.values():
        conflict = None
        if p["schedule_allowed"]:
            if p["environment"] == "Production":
                conflict = "Schedule is allowed on a Production VM."
            elif any(w in p["restriction_note"].lower() for w in CONFLICT_WORDS):
                conflict = f"Schedule is allowed but the restriction note says: '{p['restriction_note']}'."
        low_completeness = p["completeness"] < th["min_completeness"]
        short_history = p["days_observed"] < th["min_observation_days"]
        insufficient = low_completeness or short_history
        reasons = []
        if low_completeness:
            reasons.append(f"Only {p['completeness']*100:.1f}% of expected 15-minute measurements are present "
                           f"(minimum {th['min_completeness']*100:.0f}%).")
        if short_history:
            reasons.append(f"Only {p['days_observed']:.1f} days observed (minimum {th['min_observation_days']}).")
        if p["invalid"]:
            reasons.append(f"{p['invalid']} invalid row(s) excluded.")
        if p["duplicates"]:
            reasons.append(f"{p['duplicates']} duplicate row(s) excluded.")
        if conflict:
            reasons.append(conflict)
        if insufficient:
            status = "insufficient"
        elif p["invalid"] or p["duplicates"] or conflict:
            status = "issue"
        else:
            status = "ok"
        per.append({
            "resource_id": p["id"], "resource_name": p["name"],
            "valid": p["valid"], "invalid": p["invalid"], "duplicates": p["duplicates"],
            "missing": p["missing"], "completeness": p["completeness"],
            "expected_intervals": C.EXPECTED_INTERVALS,
            "insufficient_history": insufficient, "conflict": conflict,
            "status": status, "reasons": reasons,
            "first_ts": p.get("first_ts"), "last_ts": p.get("last_ts"),
        })
    order = {"insufficient": 0, "issue": 1, "ok": 2}
    per.sort(key=lambda x: (order[x["status"]], x["resource_name"]))
    total_valid = sum(x["valid"] for x in per)
    expected_total = sum(x["expected_intervals"] for x in per)
    return {
        "resources": per,
        "summary": {
            "total_rows_valid": total_valid,
            "invalid_rows": sum(x["invalid"] for x in per),
            "duplicate_rows": sum(x["duplicates"] for x in per),
            "missing_intervals": sum(x["missing"] for x in per),
            "expected_intervals_total": expected_total,
            "overall_completeness": round(total_valid / expected_total, 4) if expected_total else None,
            "resources_insufficient_history": sum(1 for x in per if x["insufficient_history"]),
            "resources_below_completeness": sum(1 for x in per if x["completeness"] < th["min_completeness"]),
            "resources_with_conflicts": sum(1 for x in per if x["conflict"]),
        },
    }


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------
def run_analysis(conn) -> dict:
    t0 = time.time()
    eff = store.effective_settings(conn)
    th = dict(eff["thresholds"])
    th["_working_hours"] = eff["working_hours"]
    wh = eff["working_hours"]
    start = int(C.WINDOW_END.timestamp()) - C.OBSERVATION_DAYS * 86400
    end = int(C.WINDOW_END.timestamp())
    ctx = {"start": start, "end": end, "th": th, "working_hours": wh,
           "time_index": _time_index(start, wh, C.EXPECTED_INTERVALS)}

    resources = store.load_resources(conn)
    volumes = store.load_volumes(conn)
    ips = store.load_public_ips(conn)
    profiles = {}
    for res in resources:
        profiles[res["resource_id"]] = build_profile(conn, res, volumes, ips, ctx)

    out = {"recommendations": [], "protected": []}
    for p in profiles.values():
        if p["cpu"] is None:
            out["protected"].append({"resource_id": p["id"], "resource_name": p["name"],
                                     "environment": p["environment"], "check": "data",
                                     "verdict": "No usable data", "reasons": ["No valid measurements."],
                                     "evidence": {}})
            continue
        _rightsize_recs(p, th, out)   # becomes collect_more_data when completeness is low
        _idle_rec(p, th, out)
        _schedule_rec(p, th, out)

    _orphan_recs(volumes, ips, th, datetime.fromisoformat(C.WINDOW_END.isoformat()), out)

    # Overlap annotation: resources with more than one recommendation.
    by_res = {}
    for rec in out["recommendations"]:
        by_res.setdefault(rec["resource_id"], []).append(rec["id"])
    for rec in out["recommendations"]:
        others = [x for x in by_res[rec["resource_id"]] if x != rec["id"]]
        rec["overlaps_with"] = others

    cost_anoms = cost_anomalies(profiles, th)

    orphan_v = [{"volume_id": v["volume_id"], "size_gb": v["size_gb"], "detached_at": v["detached_at"],
                 "monthly_inr": round(orphan_volume_monthly(v["size_gb"]), 2)}
                for v in volumes if v["resource_id"] is None and v["detached_at"]]
    orphan_i = [{"ip_id": ip["ip_id"], "address": ip["address"], "detached_at": ip["detached_at"],
                 "monthly_inr": round(orphan_ip_monthly(), 2)}
                for ip in ips if ip["resource_id"] is None and ip["detached_at"]]
    orphan_total = sum(x["monthly_inr"] for x in orphan_v) + sum(x["monthly_inr"] for x in orphan_i)
    vm_total = sum(p["cost"]["total"] for p in profiles.values())
    monthly_total = round(vm_total + orphan_total, 2)
    totals = {"monthly_total": monthly_total, "vm_total": round(vm_total, 2),
              "orphan_total": round(orphan_total, 2)}

    # Pending alert entries from rules that run after profiles.
    alerts = build_alerts(profiles, orphan_v, orphan_i, cost_anoms, totals, th,
                          C.WINDOW_END, int(time.time()))

    dq = data_quality(profiles, th)
    for rec in out["recommendations"]:
        rec.setdefault("status", "open")

    # Strip private helper fields from settings before returning.
    th_public = {k: v for k, v in th.items() if not k.startswith("_")}
    result = {
        "generated_at": store.now_iso(),
        "duration_ms": round((time.time() - t0) * 1000),
        "window": {"start": _iso(start), "end": _iso(end), "days": C.OBSERVATION_DAYS,
                   "expected_intervals_per_resource": C.EXPECTED_INTERVALS},
        "settings": {"thresholds": th_public, "working_hours": wh},
        "pricing": {
            "currency": "INR (synthetic)",
            "hours_per_month": C.HOURS_PER_MONTH,
            "storage_inr_per_gb_month": C.STORAGE_PER_GB_MONTH_INR,
            "public_ip_inr_per_month": C.PUBLIC_IP_PER_MONTH_INR,
            "egress_inr_per_gb": C.EGRESS_PER_GB_INR,
            "vm_catalog": {k: {"family": v[0], "vcpu": v[1], "ram_gb": v[2], "hourly_inr": v[3]}
                           for k, v in C.VM_CATALOG.items()},
            "schedule_active_fraction": round(schedule_active_fraction(wh), 4),
            "disclaimer": "All prices are illustrative synthetic inputs, not cloud-provider quotes.",
        },
        "profiles": profiles,
        "orphans": {"volumes": orphan_v, "public_ips": orphan_i},
        "recommendations": out["recommendations"],
        "protected": out["protected"],
        "alerts": alerts,
        "cost_anomalies": cost_anoms,
        "data_quality": dq,
        "totals": totals,
    }
    result["duration_ms"] = round((time.time() - t0) * 1000)
    return result
