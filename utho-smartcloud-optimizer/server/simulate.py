"""Cost Simulator: projects the monthly bill under a set of recommendations.

Savings are computed from the COMBINED after-state of every resource, so two
recommendations on the same VM (for example rightsizing and stopping) never
double count. The difference between the sum of standalone savings and the
combined saving is reported as an explicit overlap adjustment.
"""
from . import config as C
from .pricing import orphan_ip_monthly, orphan_volume_monthly, vm_cost

ACTIONABLE = {"rightsize", "schedule_off_hours", "idle_stop_test", "orphan_volume", "orphan_public_ip"}


def build_actions(recs: list):
    per_resource, deleted_volumes, released_ips = {}, set(), set()
    for r in recs:
        if r["type"] not in ACTIONABLE:
            continue
        if r["type"] == "orphan_volume":
            deleted_volumes.add(r["resource_id"])
        elif r["type"] == "orphan_public_ip":
            released_ips.add(r["resource_id"])
        else:
            act = per_resource.setdefault(r["resource_id"], {})
            if r["type"] == "rightsize":
                act["resize_to"] = r["proposed"]
            elif r["type"] == "schedule_off_hours":
                act["schedule"] = True
            elif r["type"] == "idle_stop_test":
                act["stop"] = True
    return per_resource, deleted_volumes, released_ips


def simulate(result: dict, recs: list, label: str, keep=lambda p: True, include_orphans=True) -> dict:
    wh = result["settings"]["working_hours"]
    per_res, del_vols, rel_ips = build_actions(recs)
    rows_by_env, rows_by_team, rows_by_region, by_rec = {}, {}, {}, []

    def add(bucket, key, base_val, after_val):
        b = bucket.setdefault(key, {"baseline": 0.0, "projected": 0.0})
        b["baseline"] += base_val
        b["projected"] += after_val

    baseline = projected = 0.0
    for p in result["profiles"].values():
        if not keep(p):
            continue
        before = p["cost"]["total"]
        after = vm_cost(p["base"], per_res.get(p["id"]), wh)["total"]
        baseline += before
        projected += after
        add(rows_by_env, p["environment"], before, after)
        add(rows_by_team, p["team"], before, after)
        add(rows_by_region, p["region"], before, after)

    if include_orphans:
        for v in result["orphans"]["volumes"]:
            cost = orphan_volume_monthly(v["size_gb"])
            after = 0.0 if v["volume_id"] in del_vols else cost
            baseline += cost
            projected += after
            add(rows_by_env, "Unattributed", cost, after)
        for ip in result["orphans"]["public_ips"]:
            cost = orphan_ip_monthly()
            after = 0.0 if ip["ip_id"] in rel_ips else cost
            baseline += cost
            projected += after
            add(rows_by_env, "Unattributed", cost, after)

    standalone_sum = 0.0
    for r in recs:
        if r["type"] in ACTIONABLE:
            standalone_sum += r["savings_monthly"]
        by_rec.append({"id": r["id"], "category": r["category"], "resource_name": r["resource_name"],
                       "resource_id": r["resource_id"], "standalone_savings": r["savings_monthly"],
                       "risk": r["risk"], "conditional": r["conditional_savings"],
                       "overlaps_with": r["overlaps_with"]})

    savings = baseline - projected
    overlap_adj = savings - standalone_sum if recs else 0.0

    def fmt(bucket):
        return {k: {"baseline": round(v["baseline"], 2), "projected": round(v["projected"], 2),
                    "savings": round(v["baseline"] - v["projected"], 2)} for k, v in sorted(bucket.items())}

    return {
        "label": label,
        "recommendation_count": len(recs),
        "baseline_monthly": round(baseline, 2),
        "projected_monthly": round(projected, 2),
        "savings_monthly": round(savings, 2),
        "savings_annual": round(savings * 12, 2),
        "savings_pct": round(savings / baseline * 100, 2) if baseline else 0.0,
        "standalone_sum": round(standalone_sum, 2),
        "overlap_adjustment": round(overlap_adj, 2),
        "by_environment": fmt(rows_by_env),
        "by_team": fmt(rows_by_team),
        "by_region": fmt(rows_by_region),
        "by_recommendation": by_rec,
        "formula": ("Projected = sum over resources of vm_cost(base, combined accepted actions) "
                    "+ orphan costs not removed. Savings = baseline - projected. Overlap adjustment = "
                    "combined savings - sum of standalone savings."),
    }
