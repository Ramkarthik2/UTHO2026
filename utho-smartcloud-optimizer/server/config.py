"""Central configuration for UTHO SmartCloud Optimizer.

Every price here is an ILLUSTRATIVE SYNTHETIC INPUT in INR. None of them is a
cloud-provider quote. Thresholds are defaults that can be overridden at runtime
through the Settings API (stored in SQLite). See docs/METHODOLOGY.md.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("UTHO_DB", ROOT / "data" / "utho.sqlite"))
CSV_DIR = ROOT / "data" / "csv"
WEB_DIR = ROOT / "web"

SEED = 20261023                       # deterministic seed for synthetic data
IST = timezone(timedelta(hours=5, minutes=30), "IST")   # Asia/Kolkata, no DST
WINDOW_END = datetime(2026, 10, 23, 0, 0, tzinfo=IST)   # observation window end
OBSERVATION_DAYS = 30
INTERVAL_SECONDS = 900                # 15-minute measurements
INTERVALS_PER_DAY = 96
EXPECTED_INTERVALS = OBSERVATION_DAYS * INTERVALS_PER_DAY   # 2880

# ---- Billing convention -------------------------------------------------
# Compute is billed per hour. A month is modelled as 730 hours (365 * 24 / 12).
HOURS_PER_MONTH = 730.0
DAYS_PER_MONTH = HOURS_PER_MONTH / 24.0          # 30.4167

# ---- Synthetic price book (INR, illustrative) ----------------------------
# name -> (family, vCPU, RAM GB, hourly price INR)
VM_CATALOG = {
    "gp-1x4":   ("general", 1, 4, 1.90),
    "gp-2x8":   ("general", 2, 8, 3.80),
    "gp-4x16":  ("general", 4, 16, 7.60),
    "gp-8x32":  ("general", 8, 32, 15.20),
    "cpu-2x4":  ("compute", 2, 4, 3.40),
    "cpu-4x8":  ("compute", 4, 8, 6.80),
    "cpu-8x16": ("compute", 8, 16, 13.60),
    "mem-2x16": ("memory", 2, 16, 4.60),
    "mem-4x32": ("memory", 4, 32, 9.20),
    "mem-8x64": ("memory", 8, 64, 18.40),
}
STORAGE_PER_GB_MONTH_INR = 6.0        # block storage, billed even when VM is stopped
PUBLIC_IP_PER_MONTH_INR = 290.0       # billed whether or not attached (synthetic rule)
EGRESS_PER_GB_INR = 4.5               # outbound data transfer

# ---- Working hours (default; overridable in Settings) --------------------
DEFAULT_WORKING_HOURS = {
    "start_hour": 9,
    "end_hour": 19,
    "weekdays": [0, 1, 2, 3, 4],      # Monday=0 ... Sunday=6
    "timezone": "Asia/Kolkata",
}

# ---- Analysis thresholds (default; overridable in Settings) --------------
# Reasons for each value are documented in docs/METHODOLOGY.md, section 4.
THRESHOLDS = {
    "min_completeness": 0.90,            # below this, evidence is insufficient
    "min_observation_days": 14,          # minimum history for any rightsizing claim
    "rightsize_cpu_p95_max": 20.0,       # steady-low test
    "rightsize_cpu_p99_max": 40.0,
    "rightsize_cpu_peak_max": 70.0,      # peak-sensitivity guard
    "rightsize_mem_p99_max": 60.0,       # memory-pressure guard
    "rightsize_mem_peak_max": 75.0,
    "target_cpu_util": 70.0,             # sizing target for p99 CPU after change
    "target_mem_util": 75.0,             # sizing target for peak memory after change
    "idle_cpu_p95_max": 3.0,             # "idle" if p95 CPU below this
    "offhours_max_cpu_p95": 10.0,        # after-hours CPU must stay below this to schedule
    "offhours_min_business_cpu_p95": 10.0,   # there must be real business-hours use
    "orphan_min_detached_days": 14,
    "ip_unattached_min_days": 7,
    "anomaly_z": 3.0,
    "anomaly_min_pct": 20.0,
    "anomaly_min_baseline_days": 7,
    "anomaly_baseline_days": 14,
    "anomaly_std_floor_pct": 1.0,        # low-variance guard: std >= 1% of baseline mean
    "alert_cpu_spike": 95.0,
    "alert_cpu_spike_intervals": 4,      # sustained for at least 1 hour
    "alert_mem_high": 90.0,
    "alert_mem_high_intervals": 8,       # sustained for at least 2 hours
    "alert_disk_limit": 85.0,
    "alert_low_util_p95": 10.0,
    "alert_stale_hours": 24,
    "budget_inr_monthly": 900000.0,
    "budget_warn_ratio": 0.90,
}

# Confidence thresholds used to label scores.
CONFIDENCE_HIGH = 80
CONFIDENCE_MEDIUM = 60

ENVIRONMENTS = ["Production", "Staging", "Development"]
TEAMS = ["Engineering", "Data", "Platform", "Analytics", "QA"]
REGIONS = ["Mumbai", "Hyderabad", "Singapore"]
