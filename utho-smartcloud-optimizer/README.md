# UTHO SmartCloud Optimizer (MVP)

*See the waste. Prove the savings. Optimize with confidence.*

UTHO turns cloud utilization data into explainable, risk-aware cost-saving recommendations. It finds
over-provisioned VMs, idle and off-hours resources, orphaned storage and unattached IPs. Each
recommendation shows its evidence, calculation, risk, confidence and preconditions. Nothing is applied
to a real account.

> **All data is synthetic.** A fictional mid-sized SaaS company with 100 VMs, 30 days of 15-minute
> measurements and illustrative INR prices. It is not connected to any cloud provider.

## Run it

Requirements: Python 3.10+ (tested on 3.12). No packages to install.

```bash
cd utho
python3 scripts/generate_data.py      # optional: builds data/utho.sqlite and CSV inventory (about 4 s)
python3 -m server.api 8080            # the database is created automatically if missing
# open http://127.0.0.1:8080
```

Run the tests:

```bash
python3 -m unittest discover -s tests -t . -p "test_*.py" -v
```

## What works
- **Executive Overview**: KPIs, spend by environment and team/region, CPU and memory distributions,
  daily trend (observation days 1 to 30), savings by category, filters (region, team,
  environment, type). Current vs projected cost is on the Cost Simulator page.
- **Resource Explorer**: sortable, searchable inventory. Detail view with percentiles, daily history,
  last-7-days samples, hour-of-day pattern, weekday/weekend comparison, data completeness, alerts.
- **Optimization Recommendations**: rightsizing, off-hours scheduling, idle stop tests, orphaned volumes,
  unattached IPs, and collect-more-data items. Detail panel with evidence, calculation, assumptions,
  preconditions, validation, risks, Accepted / Reject with notes, and decision history.
- **Cost Simulator**: accepted plan, plan comparison (accepted, all low-risk open, every open recommendation),
  impact by environment and team, overlap-adjusted savings, reset.
- **Alerts and Insights**: sustained CPU spikes, memory pressure, storage limit, incomplete or stale
  metrics, persistent low utilization, budget overrun, cost anomalies. Acknowledge with a note.
- **Reports**: CSV exports (resources, recommendations, cost report, metrics, audit), management summary.
- **AI Command Center**: select a recommended resource to see a 3-4 line, rule-based explanation built from its CPU, memory and working-hours evidence (`GET /api/ai/resources`, `GET /api/ai/explain?resource=`). No external AI model is called.
- **Data Sources**: where the data comes from, the synthetic dataset coverage, and a link to Reports > Downloads > Resources (CSV)
  Load Demo Scenario (regenerates seeded data), Run Analysis.

## Repository layout
```
server/   backend: analysis, pricing, simulator, store, synthetic generator, HTTP API
web/      single-page frontend (no external dependencies)
scripts/  generate_data.py
tests/    unittest suite (56 tests)
docs/     METHODOLOGY, DATA_DICTIONARY, ARCHITECTURE, SAFETY_AND_LIMITATIONS, DEMO_SCRIPT
data/     created at runtime (database and CSV)
```

## Key documents
- [Methodology and thresholds](docs/METHODOLOGY.md): how every number and recommendation is computed.
- [Data dictionary](docs/DATA_DICTIONARY.md): what each synthetic field means.
- [Architecture](docs/ARCHITECTURE.md): components and design decisions.
- [Safety and limitations](docs/SAFETY_AND_LIMITATIONS.md): what the tool will not do and what is not finished.
- [Demo script](docs/DEMO_SCRIPT.md): a 5–7 minute walkthrough.

## Settings and reset
- Thresholds and working hours use the defaults in `server/config.py`. The Settings page does not edit them. **Reset demo state** reloads the seeded dataset and clears all decisions.
- **Load Demo Scenario** rebuilds the seeded dataset and clears decisions, acknowledgements and settings.
- **Reset simulation** removes accepted items from the plan. Rejected items stay in history.


## Employee login prototype
The web app now starts with an employee selector for 20 `UTHO_######` demo accounts. Selecting an account opens a password form with a Show/Hide control, followed by an optional password-change step. Changed demo passwords persist in this browser's local storage; the signed-in demo user persists for the current browser session.

**Test credentials file:** the sign-in page has an "Open Usernames & Passwords file" link under "Welcome back" that opens `web/credentials.txt` (all 20 usernames and passwords) for testing. This file exists only for test cases; in a real deployment no such file would exist.

**Security limitation:** this is a front-end prototype gate, not production authentication. Demo credentials are embedded in `web/auth.js`, and the API endpoints are not protected by server-side sessions. Do not use real credentials or deploy this as a security boundary. Production authentication should use server-side password hashing, session cookies, rate limiting, CSRF protection, and API authorization.
