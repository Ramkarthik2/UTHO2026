"""The single pricing and savings engine used by the dashboard, recommendations,
simulator and reports. All inputs are illustrative synthetic INR values.

Conventions (documented in docs/METHODOLOGY.md):
  * A month is modelled as 730 hours.
  * Compute cost = hourly price x 730 x active fraction.
  * Active fraction is 1.0 normally, 0.0 when a VM is stopped, and
    (scheduled hours per week / 168) when an off-hours schedule applies.
  * Block storage and public IPs continue to be billed while a VM is stopped.
  * Savings for a resource are computed from the COMBINED after-state of all its
    accepted actions, never by summing standalone savings (prevents double counting).
"""
from __future__ import annotations
import math

from . import config as C


def inr(amount: float) -> str:
    """Format INR with Indian digit grouping, e.g. 1234567 -> '₹12,34,567'."""
    sign = "-" if amount < 0 else ""
    whole = str(int(round(abs(amount))))
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{sign}₹{whole}"


def schedule_active_fraction(working_hours: dict) -> float:
    """Share of a modelled 730-hour month that falls inside working hours.

    Example: 10 h/day on 5 weekdays -> 50 / 168 = 0.2976 of all hours.
    """
    hours_per_day = working_hours["end_hour"] - working_hours["start_hour"]
    weekly_hours = hours_per_day * len(working_hours["weekdays"])
    return weekly_hours / 168.0


def compute_monthly(hourly_inr: float, active_fraction: float = 1.0) -> float:
    return hourly_inr * C.HOURS_PER_MONTH * active_fraction


def storage_monthly(size_gb: float) -> float:
    return size_gb * C.STORAGE_PER_GB_MONTH_INR


def egress_monthly(avg_daily_gb: float) -> float:
    return avg_daily_gb * C.DAYS_PER_MONTH * C.EGRESS_PER_GB_INR


def vm_cost(base: dict, action: dict | None = None, working_hours: dict | None = None) -> dict:
    """Monthly cost of one VM after applying its combined actions.

    base keys: hourly, storage_gb, public_ip (bool), egress_monthly
    action keys (all optional): resize_to (catalogue name), schedule (bool), stop (bool)
    """
    action = action or {}
    wh = working_hours or C.DEFAULT_WORKING_HOURS
    hourly = C.VM_CATALOG[action["resize_to"]][3] if action.get("resize_to") else base["hourly"]
    if action.get("stop"):
        active = 0.0
    elif action.get("schedule"):
        active = schedule_active_fraction(wh)
    else:
        active = 1.0
    compute = compute_monthly(hourly, active)
    storage = storage_monthly(base["storage_gb"])        # billed even when stopped
    ip = C.PUBLIC_IP_PER_MONTH_INR if base.get("public_ip") else 0.0
    egress = base.get("egress_monthly", 0.0)
    total = compute + storage + ip + egress
    return {"compute": round(compute, 2), "storage": round(storage, 2),
            "public_ip": round(ip, 2), "egress": round(egress, 2), "total": round(total, 2)}


def orphan_volume_monthly(size_gb: float) -> float:
    return storage_monthly(size_gb)


def orphan_ip_monthly() -> float:
    return C.PUBLIC_IP_PER_MONTH_INR


def round_savings(x: float) -> float:
    return round(x, 2)


def is_finite_nonneg(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0
