"""Resource inventory report as a PDF, using only the Python standard library.

The report reads the same analysis result (profiles, data quality, pricing) that the
screens and CSV exports read, so it cannot drift from the application. Text streams are
left uncompressed so the report stays searchable and can be checked by tests.
"""
import textwrap
from collections import Counter
from datetime import datetime

from . import config as C
from . import synth

PAGE_W, PAGE_H = 595, 842          # A4 in points
MARGIN = 46
FONTS = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Courier"}
INK = (0.10, 0.12, 0.20)
MUTED = (0.38, 0.43, 0.53)
ACCENT = (0.05, 0.45, 0.65)
HEAD_BG = (0.86, 0.93, 0.98)
RULE = (0.75, 0.80, 0.86)


def _ascii(s) -> str:
    for src, dst in (("₹", "INR "), ("–", "-"), ("—", "-"), ("×", "x"), ("≥", ">="), ("≤", "<="),
                     ("’", "'"), ("“", '"'), ("”", '"'), ("·", "|"), ("→", "->")):
        s = str(s).replace(src, dst)
    return s.encode("ascii", "replace").decode("ascii")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _fit(value, width: int) -> str:
    s = _ascii(value)
    if len(s) > width:
        s = s[: max(0, width - 2)] + ".."
    return s.ljust(width)


class _Layout:
    def __init__(self):
        self.pages = []
        self.ops = []
        self.y = PAGE_H - MARGIN

    # ---- primitives -------------------------------------------------------
    def _color(self, rgb):
        return "%.3f %.3f %.3f rg" % rgb

    def text(self, x, y, font, size, s, rgb=INK):
        self.ops.append(f"{self._color(rgb)} BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({_esc(_ascii(s))}) Tj ET")

    def box(self, x, y, w, h, rgb):
        self.ops.append(f"{self._color(rgb)} {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")

    def rule(self, y):
        self.ops.append(f"0.75 0.80 0.86 RG 0.5 w {MARGIN} {y:.1f} m {PAGE_W - MARGIN} {y:.1f} l S")

    def new_page(self, header=True):
        self.ops = []
        self.pages.append(self.ops)
        self.y = PAGE_H - MARGIN
        if header:
            self.text(MARGIN, PAGE_H - 30, "F1", 7.5,
                      "UTHO SmartCloud Optimizer  |  Resource Inventory and Data Source Documentation", MUTED)
            self.rule(PAGE_H - 36)
            self.y = PAGE_H - MARGIN

    def ensure(self, height: float):
        if self.y - height < MARGIN + 18:
            self.new_page()

    # ---- flow content -----------------------------------------------------
    def heading(self, s: str):
        self.ensure(40)
        self.y -= 6
        self.text(MARGIN, self.y, "F2", 13, s, ACCENT)
        self.y -= 8
        self.rule(self.y)
        self.y -= 14

    def subheading(self, s: str):
        self.ensure(26)
        self.text(MARGIN, self.y, "F2", 10, s, INK)
        self.y -= 15

    def paragraph(self, s: str, size: float = 9.5, indent: float = 0, rgb=INK):
        width_chars = int((PAGE_W - 2 * MARGIN - indent) / (size * 0.5))
        lines = textwrap.wrap(_ascii(s), width=width_chars) or [""]
        lh = size * 1.45
        self.ensure(lh * len(lines) + 4)
        for line in lines:
            self.text(MARGIN + indent, self.y, "F1", size, line, rgb)
            self.y -= lh
        self.y -= 5

    def bullets(self, items, size: float = 9.5):
        for item in items:
            self.paragraph("- " + item, size=size, indent=10)

    def table(self, headers, rows, widths, size: float = 7.0):
        char = size * 0.6
        row_h = size + 4.5
        line = " ".join(_fit(h, w) for h, w in zip(headers, widths))

        def draw_header():
            self.ensure(row_h + 4)
            self.box(MARGIN - 2, self.y - 3, PAGE_W - 2 * MARGIN + 4, row_h + 1, HEAD_BG)
            self.text(MARGIN, self.y, "F3", size, line, INK)
            self.y -= row_h

        draw_header()
        for r in rows:
            if self.y - row_h < MARGIN + 18:
                self.new_page()
                draw_header()
            cells = " ".join(_fit(v, w) for v, w in zip(r, widths))
            self.text(MARGIN, self.y, "F3", size, cells, INK)
            self.y -= row_h
        self.y -= 6

    def finish(self) -> list:
        total = len(self.pages)
        for idx, ops in enumerate(self.pages, start=1):
            if idx == 1:
                continue
            self.ops = ops
            self.text(MARGIN, 26, "F1", 7.5,
                      "Synthetic demo data. Not a billing record and not a cloud-provider inventory.", MUTED)
            self.text(PAGE_W - MARGIN - 70, 26, "F1", 7.5, f"Page {idx} of {total}", MUTED)
        return self.pages


def _pdf_bytes(pages: list) -> bytes:
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    catalog = add(None)
    pages_obj = add(None)
    font_ids = {name: add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>")
                for name, base in FONTS.items()}
    page_refs = []
    for ops in pages:
        content = "\n".join(ops).encode("ascii")
        cnum = add(f"<< /Length {len(content)} >>\nstream\n{content.decode('ascii')}\nendstream")
        pnum = add(None)
        page_refs.append((pnum, cnum))
    fonts = " ".join(f"/{k} {v} 0 R" for k, v in font_ids.items())
    for pnum, cnum in page_refs:
        objs[pnum - 1] = (f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                          f"/Resources << /Font << {fonts} >> >> /Contents {cnum} 0 R >>")
    kids = " ".join(f"{p} 0 R" for p, _ in page_refs)
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>"
    objs[pages_obj - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_refs)} >>"

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii")
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
            ).encode("ascii")
    return bytes(out)


def _money(v) -> str:
    return f"INR {v:,.2f}"


def _fmt_dt(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b %Y %H:%M") + " IST"


def _fmt_day(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b %Y")


def build_report(svc) -> bytes:
    """Build the inventory PDF from the live analysis. Raises on failure; the caller reports it."""
    res = svc.analysis()
    profiles = list(res["profiles"].values())
    if not profiles:
        raise ValueError("No resources are loaded. Load the demo scenario first.")
    dq = res["data_quality"]["summary"]
    pricing = res["pricing"]
    window = res["window"]
    orphans = res["orphans"]
    conn = svc.conn()
    try:
        states = dict(conn.execute("SELECT resource_id, state FROM resources").fetchall())
    finally:
        conn.close()

    n = len(profiles)
    generated = datetime.now(C.IST).strftime("%d %b %Y, %H:%M IST")
    by_env = Counter(p["environment"] for p in profiles)
    by_region = Counter(p["region"] for p in profiles)
    by_team = Counter(p["team"] for p in profiles)
    by_type = Counter(p["type"] for p in profiles)
    by_crit = Counter(p["criticality"] for p in profiles)
    by_idle = Counter(p["idle_state"] for p in profiles)
    by_state = Counter(states.get(p["id"], "unknown") for p in profiles)
    attached_volumes = sum(len(p["volume_ids"]) for p in profiles)
    attached_ips = sum(p["public_ip_count"] for p in profiles)
    named = len(synth.NAMED)

    doc = _Layout()

    # ---- title page -------------------------------------------------------
    doc.new_page(header=False)
    doc.y = PAGE_H - 230
    doc.text(MARGIN, doc.y, "F2", 24, "UTHO SmartCloud Optimizer", INK)
    doc.y -= 30
    doc.text(MARGIN, doc.y, "F2", 15, "Resource Inventory and Data Source Documentation", ACCENT)
    doc.y -= 34
    doc.text(MARGIN, doc.y, "F1", 10.5, f"Report generated: {generated}", MUTED)
    doc.y -= 16
    doc.text(MARGIN, doc.y, "F1", 10.5, f"Dataset: {n} synthetic virtual machines, seed {C.SEED}", MUTED)
    doc.y -= 60
    doc.box(MARGIN, doc.y - 58, PAGE_W - 2 * MARGIN, 72, (0.93, 0.96, 1.0))
    doc.text(MARGIN + 12, doc.y - 14, "F2", 10, "Synthetic demo data", ACCENT)
    doc.text(MARGIN + 12, doc.y - 30, "F1", 9.5,
             "Every resource, metric and price in this report is generated for demonstration and testing.", INK)
    doc.text(MARGIN + 12, doc.y - 44, "F1", 9.5,
             "None of it was retrieved from a live cloud provider, and none of it is billing information.", INK)
    doc.y -= 100

    # ---- 1. origin --------------------------------------------------------
    doc.new_page()
    doc.heading("1. Where the resource data comes from")
    doc.paragraph(
        "Our resource inventory is generated by a deterministic synthetic dataset generator "
        "(server/synth.py). It simulates the infrastructure of a fictional SaaS company. The dataset "
        "represents virtual machines and related storage and public IP resources across simulated regions, "
        "environments and teams.")
    doc.paragraph("How the dataset is generated:")
    doc.bullets([
        f"The generator is deterministic. It uses a seeded random number generator with seed {C.SEED}, so running "
        "it again with the same seed produces identical data.",
        f"The inventory holds {n} virtual machines: {named} named demo machines, plus {n - named} machines "
        f"drawn from a seeded mix of {len(synth.RANDOM_PATTERNS)} workload patterns.",
        f"Attached resources: {attached_volumes} attached storage volumes and {attached_ips} attached public IPs. "
        f"Detached (orphan) resources: {len(orphans['volumes'])} volumes and {len(orphans['public_ips'])} public IPs.",
        f"Utilization measurements are modeled at {C.INTERVAL_SECONDS // 60}-minute intervals for "
        f"{C.OBSERVATION_DAYS} days, from {_fmt_day(window['start'])} to {_fmt_day(window['end'])}. "
        f"Each resource has {C.EXPECTED_INTERVALS:,} expected intervals.",
        "Named demo machines carry scenario notes that explain the situation they model, for example "
        "underused development machines or production machines with peak-hour load.",
    ])
    doc.paragraph(
        "Resource attributes (CPU and memory capacity, storage, environment, state, ownership and utilization "
        "patterns) are generated or assigned by the project's data-generation logic. Prices are illustrative "
        "inputs defined in the project's price book (server/config.py). They are not live cloud-provider quotes.")
    doc.paragraph(
        "Distinction: the resource inventory is generated. The utilization measurements are generated from "
        "those workload patterns. The prices are illustrative assumptions applied to the generated resources.",
        rgb=MUTED)

    # ---- 2. coverage ------------------------------------------------------
    doc.heading("2. Inventory summary")
    doc.paragraph(f"Resource count: {n} virtual machines. Each has a unique ID, a name, a size from the VM catalog, "
                  "a region, an environment, a team, a criticality rating and a state.")
    for title, counter in (("By environment", by_env), ("By region", by_region), ("By team", by_team),
                           ("By criticality", by_crit), ("By state", by_state), ("By idle state", by_idle)):
        doc.subheading(title)
        rows = [[k, v] for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
        doc.table(["Value", "Resources"], rows, [40, 12], size=8)
    doc.subheading("By VM size (catalog)")
    size_rows = []
    for t, count in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0])):
        sample = next(p for p in profiles if p["type"] == t)
        size_rows.append([t, sample["family"], sample["vcpu"], sample["ram_gb"], f"{sample['hourly']:.2f}", count])
    doc.table(["Size", "Family", "vCPU", "RAM GB", "Hourly INR", "Resources"], size_rows,
              [12, 12, 8, 8, 12, 10], size=8)
    doc.subheading("Attachments")
    doc.table(["Item", "Count"], [
        ["Attached storage volumes", attached_volumes],
        ["Attached public IPs", attached_ips],
        ["Detached (orphan) volumes", len(orphans["volumes"])],
        ["Detached (orphan) public IPs", len(orphans["public_ips"])],
    ], [40, 12], size=8)

    # ---- 3. inventory table ------------------------------------------------
    doc.new_page()
    doc.heading("3. Resource inventory table")
    doc.paragraph(f"All {n} resources in the loaded dataset. The same records are available as CSV at "
                  "/api/export/resources.csv. Monthly INR is the estimated compute, storage and public IP cost "
                  "for the resource, using the price book.")
    rows = []
    for p in profiles:
        rows.append([p["id"], p["name"], p["type"], p["region"], p["environment"], p["team"],
                     p["vcpu"], p["ram_gb"], states.get(p["id"], "unknown"),
                     f"{p['hourly']:.2f}", f"{p['cost']['total']:,.2f}"])
    doc.table(["ID", "Name", "Type", "Region", "Environment", "Team", "vCPU", "RAM", "State",
               "Hourly", "Monthly"], rows,
              [8, 24, 9, 9, 11, 11, 4, 4, 7, 8, 10], size=6.6)

    # ---- 4. utilization ----------------------------------------------------
    doc.new_page()
    doc.heading("4. Utilization data methodology")
    doc.bullets([
        "Measured fields: CPU percent, memory percent, disk percent, disk IOPS, network in and out (Mbps).",
        f"Sampling interval: {C.INTERVAL_SECONDS // 60} minutes. Observation period: {C.OBSERVATION_DAYS} days.",
        "Invalid rows (missing or out-of-range values) are excluded from the analysis.",
        "Duplicate observations (same resource and timestamp) are counted once; the extra copies are excluded.",
        "Missing intervals are not filled with zero. They are counted as missing, and completeness is "
        "valid measurements divided by expected intervals.",
        "Where a daily egress value is missing, the cost calculation uses the resource's average daily "
        "egress in its place.",
        "Each resource's observed percentiles, such as p95 and p99 CPU, are calculated from its valid "
        "measurements only.",
    ])
    doc.paragraph(
        f"Quality results for the loaded dataset: {dq['total_rows_valid']:,} valid measurements, "
        f"{dq['missing_intervals']:,} missing intervals, {dq['invalid_rows']} invalid rows excluded, "
        f"{dq['duplicate_rows']} duplicate rows excluded.")

    # ---- 5. pricing --------------------------------------------------------
    doc.heading("5. Pricing methodology")
    doc.bullets([
        f"Currency: {pricing['currency']}. All prices are illustrative synthetic inputs.",
        f"Month convention: {pricing['hours_per_month']:.0f} hours per month. Hourly compute cost = hourly rate "
        f"x {pricing['hours_per_month']:.0f}.",
        "Compute is billed only while a VM runs. Stopping a VM removes its compute cost.",
        f"Storage: INR {pricing['storage_inr_per_gb_month']:.2f} per GB-month. Storage keeps billing while the "
        "VM is stopped.",
        f"Public IP: INR {pricing['public_ip_inr_per_month']:.2f} per month, billed whether or not it is attached "
        "(synthetic rule).",
        f"Outbound data transfer (egress): INR {pricing['egress_inr_per_gb']:.2f} per GB.",
        f"Off-hours scheduling savings use a schedule active fraction of {pricing['schedule_active_fraction']:.4f} "
        "(scheduled hours divided by 168 hours per week).",
        pricing["disclaimer"],
    ])
    doc.paragraph("Assumptions and limitations: these price inputs are not tied to any cloud provider's price list, "
                  "discount, commitment or region-specific tariff. Savings derived from them are estimates for "
                  "demonstration, not guaranteed reductions.", rgb=MUTED)

    # ---- 6. quality and limitations ---------------------------------------
    doc.heading("6. Data quality and limitations")
    doc.bullets([
        f"Resources with insufficient history: {dq['resources_insufficient_history']}. A resource is flagged when "
        f"completeness is below the minimum threshold or it has too few observed days. Recommendations for "
        "flagged resources are given lower confidence or a request for more data.",
        f"Resources below minimum completeness: {dq['resources_below_completeness']}.",
        f"Configuration conflicts: {dq['resources_with_conflicts']}.",
        "Some resources contain demo data-quality faults on purpose (invalid and duplicate rows, and incomplete "
        "metrics), so the quality checks can be seen working.",
        "Optimization recommendations depend on the completeness and history of each resource. Savings are "
        "projections, not guaranteed changes to any real infrastructure.",
    ])

    # ---- 7. safety ---------------------------------------------------------
    doc.heading("7. Safety disclaimer")
    doc.paragraph(
        "The dataset is for demonstration and decision-support purposes. The resources in this report do not "
        "represent verified live infrastructure unless an actual cloud integration has been explicitly "
        "confirmed. This report does not constitute an actual cloud bill or a guarantee of savings. No "
        "recommendation in this application changes any cloud resource.")

    return _pdf_bytes(doc.finish())
