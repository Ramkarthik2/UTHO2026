# Architecture

```
web/ (HTML, CSS, vanilla JS, SVG charts)
   │  fetch JSON / CSV
   ▼
server/api.py  ThreadingHTTPServer (stdlib): routes, validation, exports, static files
   │
   ├── server/analysis.py   validation → profiles → rules → alerts/anomalies/quality
   ├── server/simulate.py   combined per-resource costing and plan comparison
   ├── server/pricing.py    the single cost engine (730 h, storage, IP, egress, schedule)
   ├── server/stats.py      percentiles, stdev, runs
   ├── server/store.py      SQLite access, decisions, audit, acknowledgements, settings
   └── server/synth.py      seeded synthetic generator and CSV export
data/utho.sqlite  (created on first run; regenerated in about 4 s)
```

## Design decisions
- **Standard library only.** The build environment had no package index access, so pytest, FastAPI
  and Recharts could not be installed. The backend uses `http.server`, `sqlite3` and `unittest`,
  and the frontend is dependency-free. It runs offline with no install step.
- **Analysis is cached** in memory. Changing settings or running analysis invalidates the cache.
  Decisions and acknowledgements do not require re-analysis, because they are merged at read time.
- **Recommendation IDs are stable** (`rule:resource`), so decisions survive re-analysis.
- **Decisions are persisted** in SQLite with an audit record for every change.
- **The engine never touches cloud resources.** No code path calls a cloud API.
