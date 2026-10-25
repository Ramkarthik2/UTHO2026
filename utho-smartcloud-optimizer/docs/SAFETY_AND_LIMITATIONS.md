# Safety, assumptions and limitations

## Safety design
- Recommendations are **proposals**. Accepting one adds it to a simulated plan. It does not alter any
  resource.
- **Production** is never scheduled, and rightsizing carries High risk and requires approval.
- Downsizing is blocked by peak CPU, p99 CPU, peak memory and memory pressure, not only by averages.
- Insufficient data produces a request to collect more data, not a savings claim.
- Storage and IP charges are never removed when a VM is stopped.
- Orphaned volumes are never recommended for deletion without snapshot, retention and dependency
  checks. Deletion is listed as a validation step.
- Savings are labelled **projected**. They are not realized savings until billing data confirms them.

## Assumptions
- Synthetic prices, the 730-hour month, and the egress rate are illustrative.
- Future load resembles the 30-day window.
- Attached storage and egress do not change with a resize.

## Limitations (honest list)
- Browser rendering was **not** tested in a real browser in this environment. The JavaScript passes
  `node --check`, and every API it calls is covered by the HTTP tests, but visual layout and
  interaction still need a manual check.
- No cloud provider integration. All data is synthetic, and the app says so on every screen.
- Alerts are computed on a static snapshot, not in real time.
- The date-range filter affects utilization views (distributions, trend, resource detail). Recommendations
  always use the full 30-day window.
- Single-node SQLite and a single process. Not designed for many concurrent users.
- The cost-anomaly detector needs at least 7 baseline days and reacts to sustained changes. It can
  under-react on the first day of a ramping spike.
- Not implemented (future extensions): 30-day cost forecasting, sustainability/carbon estimate,
  Excel/PDF export, authentication, multi-account support, and live cloud billing ingestion.
