"""Deterministic SYNTHETIC cloud inventory and utilization generator.

Everything produced here is fictional. It models a fictional mid-sized SaaS
company. Nothing is collected from a real cloud provider. Running with the same
seed always yields identical data, so the demo is repeatable.
"""
import csv
import math
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import config as C

SCHEMA = """
CREATE TABLE resources (
    resource_id TEXT PRIMARY KEY, name TEXT, resource_type TEXT, family TEXT,
    vcpu INTEGER, ram_gb INTEGER, region TEXT, environment TEXT, team TEXT,
    state TEXT, created_at TEXT, hourly_inr REAL, criticality TEXT,
    schedule_allowed INTEGER, restriction_note TEXT, public_ip INTEGER,
    scenario_note TEXT
);
CREATE TABLE volumes (
    volume_id TEXT PRIMARY KEY, resource_id TEXT, size_gb INTEGER,
    volume_type TEXT, created_at TEXT, detached_at TEXT
);
CREATE TABLE public_ips (
    ip_id TEXT PRIMARY KEY, address TEXT, resource_id TEXT,
    allocated_at TEXT, detached_at TEXT
);
CREATE TABLE metrics (
    resource_id TEXT, ts INTEGER, cpu_pct REAL, mem_pct REAL, disk_pct REAL,
    disk_iops REAL, net_in_mbps REAL, net_out_mbps REAL
);
CREATE TABLE decisions (
    rec_id TEXT PRIMARY KEY, status TEXT, notes TEXT, updated_at TEXT
);
CREATE TABLE audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, event TEXT, rec_id TEXT, detail TEXT
);
CREATE TABLE alert_acks (alert_id TEXT PRIMARY KEY, acked_at TEXT, note TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
"""

# Named demo scenarios (first 12 VMs). Each one exists to exercise a specific
# rule. "scenario_note" is shown in the UI as a demo label.
NAMED = [
    dict(name="dev-sandbox-api-01", env="Development", team="Engineering", region="Mumbai",
         type="gp-4x16", pattern="steady_low", crit="Low", sched=0, restr="Developer API sandbox; owner asked for weekday access only",
         cpu=7.0, mem=26.0, net_out=0.8, public_ip=0,
         note="Demo: underutilized development VM (rightsizing candidate)"),
    dict(name="prod-checkout-api-01", env="Production", team="Platform", region="Mumbai",
         type="gp-8x32", pattern="prod_peaks", crit="Critical", sched=0, restr="Customer checkout path; peak-hour traffic",
         public_ip=1, note="Demo: production VM with CPU peaks that must not be downsized"),
    dict(name="staging-reporting-mem-01", env="Staging", team="Data", region="Hyderabad",
         type="mem-4x32", pattern="mem_bound", crit="Medium", sched=0, restr="",
         public_ip=0, note="Demo: memory-constrained workload"),
    dict(name="dev-ml-train-01", env="Development", team="Data", region="Hyderabad",
         type="cpu-4x8", pattern="dev_office", crit="Low", sched=1, restr="",
         public_ip=0, note="Demo: development workload busy only in working hours"),
    dict(name="staging-web-01", env="Staging", team="Platform", region="Singapore",
         type="gp-4x16", pattern="moderate", crit="Medium", sched=0, restr="",
         public_ip=1, note="Demo: data-quality issues injected (invalid and duplicate rows)",
         inject_quality=True),
    dict(name="prod-pipeline-ingest-01", env="Production", team="Data", region="Mumbai",
         type="mem-8x64", pattern="spike", crit="High", sched=0, restr="Ingest pipeline; sustained load during partner uploads",
         public_ip=0, net_out=6.0, note="Demo: sudden CPU spike and egress spike in the last 2 days"),
    dict(name="dev-qa-regression-01", env="Development", team="QA", region="Mumbai",
         type="gp-2x8", pattern="incomplete", crit="Low", sched=0, restr="",
         cpu=6.0, mem=30.0, net_out=2.0, public_ip=0, drop_fraction=0.40,
         note="Demo: incomplete metrics (40% of intervals missing)"),
    dict(name="batch-reconcile-01", env="Production", team="Analytics", region="Hyderabad",
         type="mem-4x32", pattern="batch", crit="High", sched=0, restr="Nightly reconciliation 01:00-04:00 IST",
         public_ip=0, note="Demo: batch server, busy only 01:00-04:00 IST"),
    dict(name="staging-bursty-api-01", env="Staging", team="Platform", region="Mumbai",
         type="cpu-8x16", pattern="bursty", crit="Medium", sched=0, restr="",
         public_ip=0, note="Demo: bursty workload with occasional peaks"),
    dict(name="qa-legacy-idle-01", env="Development", team="QA", region="Singapore",
         type="gp-2x8", pattern="idle", crit="Low", sched=1, restr="",
         public_ip=0, note="Demo: consistently idle VM"),
    dict(name="dev-analytics-temp-01", env="Development", team="Analytics", region="Hyderabad",
         type="gp-4x16", pattern="temp_idle", crit="Low", sched=1, restr="",
         public_ip=0, note="Demo: idle only in the last 10 days"),
    dict(name="prod-reports-storage-01", env="Production", team="Data", region="Mumbai",
         type="gp-4x16", pattern="prod_steady", crit="High", sched=0, restr="",
         public_ip=0, disk0=82.5, drift=4.0, note="Demo: disk approaching the configured limit"),
]

ENV_SHORT = {"Production": "prod", "Staging": "stg", "Development": "dev"}

# Mix for the remaining VMs. Weights sum to 100.
RANDOM_PATTERNS = [
    ("steady_low", 15), ("bursty", 8), ("mem_bound", 8), ("prod_peaks", 8),
    ("prod_steady", 12), ("batch", 4), ("dev_office", 20), ("idle", 5),
    ("temp_idle", 4), ("moderate", 16),
]
PATTERN_ENV = {
    "steady_low": "Development", "idle": "Development", "temp_idle": "Development",
    "dev_office": "Development", "prod_peaks": "Production", "prod_steady": "Production",
    "batch": "Production", "mem_bound": "Staging", "bursty": "Staging", "moderate": "Staging",
    "spike": "Production", "incomplete": "Development",
}
PATTERN_TYPES = {
    "steady_low": ["gp-2x8", "gp-4x16", "gp-8x32"], "idle": ["gp-2x8", "gp-4x16"],
    "temp_idle": ["gp-4x16", "gp-8x32"], "dev_office": ["cpu-4x8", "cpu-2x4"],
    "prod_peaks": ["gp-8x32", "cpu-8x16"], "prod_steady": ["gp-4x16", "gp-8x32"],
    "batch": ["mem-4x32", "mem-8x64"], "mem_bound": ["mem-2x16", "mem-4x32"],
    "bursty": ["cpu-8x16", "cpu-4x8"], "moderate": ["gp-4x16", "cpu-4x8"],
}


def _rng(seed: int, salt: str) -> random.Random:
    return random.Random(f"{seed}:{salt}")


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, C.IST).isoformat()


class VMProfileParams:
    """Per-VM generation parameters (synthetic, drawn from the seeded RNG)."""

    def __init__(self, rng: random.Random, pattern: str, overrides: dict):
        pick = lambda lo, hi: rng.uniform(lo, hi)  # noqa: E731
        self.cpu = overrides.get("cpu", pick(6, 9))
        self.mem = overrides.get("mem", {
            "mem_bound": pick(89, 93), "prod_peaks": pick(60, 68), "prod_steady": pick(50, 60),
            "batch": pick(28, 34), "dev_office": pick(30, 40), "idle": pick(18, 22),
            "temp_idle": pick(28, 35), "moderate": pick(45, 55), "bursty": pick(35, 45),
        }.get(pattern, pick(25, 35)))
        self.disk0 = overrides.get("disk0", pick(20, 60))
        self.drift = overrides.get("drift", pick(0.1, 0.6))     # percentage points per window
        if "net_out" in overrides:
            self.net_out = overrides["net_out"]
        elif pattern in ("prod_peaks", "prod_steady", "batch", "moderate", "bursty", "spike"):
            self.net_out = pick(1.5, 4.0)      # Mbps, average outbound traffic
        else:
            self.net_out = pick(0.2, 1.0)
        self.net_in = self.net_out * pick(0.6, 1.0)


def _sample(pattern: str, b: VMProfileParams, rng: random.Random, hour: float,
            wd: int, day: int, i: int, n: int):
    """Return (cpu, mem) for one 15-minute interval."""
    biz = wd < 5 and 9 <= hour < 19
    diurnal = 0.5 * (1 + math.sin(2 * math.pi * (hour - 9) / 24))
    g = rng.gauss
    if pattern in ("steady_low", "incomplete"):
        cpu = b.cpu + g(0, 1.0) + 2 * diurnal
        if rng.random() < 0.003:
            cpu += rng.uniform(12, 20)
        mem = b.mem + g(0, 0.8)
    elif pattern == "bursty":
        cpu = b.cpu + g(0, 1.5)
        if rng.random() < 0.025:
            cpu = rng.uniform(78, 92)
        mem = b.mem + g(0, 1.2)
    elif pattern == "mem_bound":
        cpu = 16 + 8 * diurnal + g(0, 2)
        mem = b.mem + g(0, 1.0)
    elif pattern == "prod_peaks":
        cpu = (46 if biz else 18) + 5 * diurnal + g(0, 3)
        if biz and rng.random() < 0.02:
            cpu = rng.uniform(90, 98)
        mem = b.mem + g(0, 1.5)
    elif pattern == "prod_steady":
        cpu = 26 + 8 * diurnal + g(0, 2.5)
        mem = b.mem + g(0, 1.2)
    elif pattern == "batch":
        in_window = 1 <= hour < 4
        cpu = rng.uniform(84, 92) if in_window else 6 + g(0, 1)
        mem = b.mem + (30 if in_window else 0) + g(0, 1)
    elif pattern == "dev_office":
        cpu = (30 + 16 * diurnal + g(0, 3)) if biz else (1.2 + abs(g(0, 0.4)))
        mem = b.mem + (12 if biz else 0) + g(0, 1)
    elif pattern == "idle":
        cpu = 0.6 + abs(g(0, 0.25))
        mem = b.mem + g(0, 0.4)
    elif pattern == "temp_idle":
        if day >= 20:
            cpu = 0.6 + abs(g(0, 0.25))
        else:
            cpu = 14 + 10 * diurnal * (1 if biz else 0.2) + g(0, 2)
        mem = b.mem + g(0, 1)
    elif pattern == "spike":
        cpu = b.cpu + g(0, 1.0) + 2 * diurnal
        if i >= n - 120:                       # last 30 hours
            cpu = rng.uniform(95, 99)
        mem = b.mem + g(0, 0.8)
    elif pattern == "moderate":
        cpu = 22 + 10 * diurnal + g(0, 3)
        mem = b.mem + g(0, 1.5)
    else:
        raise ValueError(f"unknown pattern {pattern}")
    return _clip(cpu), _clip(mem)


def _metric_rows(resource_id: str, pattern: str, b: VMProfileParams, rng: random.Random,
                 start_epoch: int, drop_fraction: float, inject_quality: bool):
    n = C.EXPECTED_INTERVALS
    rows = []
    for i in range(n):
        ts = start_epoch + i * C.INTERVAL_SECONDS
        local = datetime.fromtimestamp(ts, C.IST)
        hour = local.hour + local.minute / 60.0
        wd = local.weekday()
        day = i // C.INTERVALS_PER_DAY
        cpu, mem = _sample(pattern, b, rng, hour, wd, day, i, n)
        disk = _clip(b.disk0 + b.drift * (i / n) + rng.gauss(0, 0.15))
        iops = 30 + 3 * cpu + abs(rng.gauss(0, 8))
        surge = 8.0 if (pattern == "spike" and i >= n - 192) else 1.0   # last 48 hours
        net_out = b.net_out * (0.5 + cpu / 60) * max(0.2, rng.gauss(1, 0.15)) * surge
        net_in = b.net_in * (0.5 + cpu / 80) * max(0.2, rng.gauss(1, 0.15))
        rows.append([resource_id, ts, round(cpu, 2), round(mem, 2), round(disk, 2),
                     round(iops, 1), round(net_in, 2), round(net_out, 2)])

    if drop_fraction:
        keep = [r for r in rows if rng.random() >= drop_fraction]
        rows = keep
    if inject_quality:
        # Deliberate data-quality problems the validator must catch and report.
        rows[100][2] = -1.0          # negative CPU
        rows[500][2] = 104.0         # CPU above 100
        rows[900][3] = 101.5         # memory above 100
        rows.append(list(rows[200]))  # exact duplicate observation
        rows.append(list(rows[300]))
    return rows


def generate(db_path: Path, seed: int = C.SEED) -> dict:
    """(Re)build the synthetic SQLite database from scratch. Returns a summary."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        leftover = Path(str(db_path) + suffix)
        if leftover.exists():
            leftover.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    window_end = int(C.WINDOW_END.timestamp())
    start_epoch = window_end - C.OBSERVATION_DAYS * 86400
    inv_rng = _rng(seed, "inventory")

    # ---- Build the VM list: named demo VMs first, then a seeded random mix ----
    vms = []
    for idx, spec in enumerate(NAMED, start=1):
        vms.append(dict(spec, index=idx))
    total = 100
    weights = [w for _, w in RANDOM_PATTERNS]
    for idx in range(len(NAMED) + 1, total + 1):
        pattern = inv_rng.choices([p for p, _ in RANDOM_PATTERNS], weights=weights)[0]
        env = PATTERN_ENV.get(pattern, "Staging")
        ptype = inv_rng.choice(PATTERN_TYPES[pattern])
        crit = ("Critical" if inv_rng.random() < 0.3 else "High") if env == "Production" \
            else inv_rng.choice(["Medium", "Medium", "Low"])
        sched = 1 if (env == "Development" and crit == "Low") else 0
        vms.append(dict(
            index=idx, name=f"{ENV_SHORT[env]}-{pattern.replace('_', '-')}-{idx:03d}",
            env=env, team=inv_rng.choice(C.TEAMS), region=inv_rng.choice(C.REGIONS),
            type=ptype, pattern=pattern, crit=crit, sched=sched, restr="",
            public_ip=1 if env == "Production" and inv_rng.random() < 0.6 else 0,
            note="", ))

    resources, volumes, ips, all_metric_rows = [], [], [], []
    orphan_volumes = [
        ("vol-orphan-0001", 500, 45), ("vol-orphan-0002", 120, 5),
        ("vol-orphan-0003", 200, 30), ("vol-orphan-0004", 80, 60),
    ]
    orphan_ips = [("ip-orphan-0001", 30), ("ip-orphan-0002", 2), ("ip-orphan-0003", 12)]
    created_rng = _rng(seed, "created")

    for vm in vms:
        rid = f"vm-{vm['index']:04d}"
        pattern = vm["pattern"]
        prng = _rng(seed, f"metrics:{rid}")
        overrides = {k: vm[k] for k in ("cpu", "mem", "net_out", "disk0", "drift") if k in vm}
        b = VMProfileParams(prng, pattern, overrides)
        created = datetime(2024, 1, 1, tzinfo=C.IST) + timedelta(days=created_rng.randint(0, 900))
        ptype = vm["type"]
        family, vcpu, ram, hourly = C.VM_CATALOG[ptype]
        resources.append((
            rid, vm["name"], ptype, family, vcpu, ram, vm["region"], vm["env"], vm["team"],
            "running", created.isoformat(), hourly, vm["crit"], vm["sched"], vm["restr"],
            vm["public_ip"], vm["note"],
        ))
        size = created_rng.choice([100, 150, 200, 300, 500]) if vm["env"] == "Production" \
            else created_rng.choice([50, 100, 150, 200])
        volumes.append((f"vol-{vm['index']:04d}", rid, size, "block-ssd", created.isoformat(), None))
        if vm["public_ip"]:
            ips.append((f"ip-{vm['index']:04d}", f"203.0.113.{vm['index']}", rid,
                        created.isoformat(), None))
        rows = _metric_rows(rid, pattern, b, prng, start_epoch,
                            vm.get("drop_fraction", 0.0), vm.get("inject_quality", False))
        all_metric_rows.extend(rows)

    for vid, size, days in orphan_volumes:
        detached = (C.WINDOW_END - timedelta(days=days)).isoformat()
        volumes.append((vid, None, size, "block-ssd", "2025-06-01T00:00:00+05:30", detached))
    for ipid, days in orphan_ips:
        detached = (C.WINDOW_END - timedelta(days=days)).isoformat()
        addr = f"198.51.100.{len(ips) % 250 + 1}"
        ips.append((ipid, addr, None, "2025-03-01T00:00:00+05:30", detached))

    conn.executemany("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", resources)
    conn.executemany("INSERT INTO volumes VALUES (?,?,?,?,?,?)", volumes)
    conn.executemany("INSERT INTO public_ips VALUES (?,?,?,?,?)", ips)
    conn.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?)", all_metric_rows)
    conn.execute("CREATE INDEX idx_metrics_rid_ts ON metrics(resource_id, ts)")
    conn.execute("INSERT INTO settings VALUES ('generated_seed', ?)", (str(seed),))
    conn.commit()
    summary = {
        "seed": seed,
        "vms": len(resources),
        "volumes": len(volumes),
        "public_ips": len(ips),
        "metric_rows": len(all_metric_rows),
        "window_start": _iso(start_epoch),
        "window_end": _iso(window_end),
    }
    conn.close()
    return summary


def write_csvs(db_path: Path, out_dir: Path, include_metrics: bool = False) -> list:
    """Export the synthetic inventory (and optionally metrics) to CSV."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    written = []
    tables = {"resources": "resources.csv", "volumes": "volumes.csv", "public_ips": "public_ips.csv"}
    if include_metrics:
        tables["metrics"] = "metrics.csv"
    for table, fname in tables.items():
        cur = conn.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        path = out_dir / fname
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for row in cur:
                w.writerow(row)
        written.append(str(path))
    conn.close()
    return written
