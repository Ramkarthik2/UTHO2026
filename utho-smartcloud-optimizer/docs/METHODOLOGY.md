# Methodology: assumptions, pricing, thresholds and rules

All data is synthetic. All prices are illustrative INR inputs, not cloud-provider quotes.

## 1. Billing convention
- A month is modelled as **730 hours**. Compute = hourly price x 730 x active fraction.
- Active fraction: 1.0 normally; 0.0 when a VM is **stopped** (idle stop test); and
  `scheduled weekly hours / 168` when an off-hours schedule applies.
  Default working hours are 09:00–19:00 IST, Monday–Friday, which gives 50/168 = 0.2976.
- **Block storage and public IPs keep being billed while a VM is stopped.** Savings from stopping
  or scheduling count compute only.
- Egress = average daily outbound GB x 30.4167 days x ₹4.50/GB. Average daily GB comes from
  observed `net_out_mbps`: each 15-minute interval contributes `Mbps x 900 / 8 / 1024` GB.

## 2. Price book (synthetic)
See `server/config.py` (`VM_CATALOG`). Examples: `gp-2x8` ₹3.80/h, `gp-4x16` ₹7.60/h,
`mem-8x64` ₹18.40/h. Block storage ₹6/GB-month. Public IP ₹290/month, billed whether or not attached.

## 3. One engine for every number
`server/pricing.py::vm_cost` is the only cost function. The overview, recommendations,
simulator, exports and summary all call it.

## 4. Data validation
- Rows with missing fields, CPU/memory/disk outside 0–100, or negative IOPS/network are **dropped
  and counted**, never converted to zero.
- Duplicate timestamps keep the first occurrence and are counted.
- Missing intervals reduce **completeness** = valid intervals / 2,880 (30 days x 96). Missing values
  are excluded from statistics.
- Daily egress for a partially observed day is scaled from the observed mean (mean x 96). A day with
  no observations is excluded from the average and not counted as zero.

## 5. Percentiles
Linear interpolation between order statistics (numpy default). `p95([1..100]) = 95.05`.
Averages are reported but are never sufficient evidence for a downsize.

## 6. Thresholds (defaults in server/config.py)

| Threshold | Default | Why |
|---|---|---|
| min_completeness | 0.90 | Below this, the sample has too many gaps to rely on |
| min_observation_days | 14 | Two weeks covers a weekly cycle; less is too short |
| rightsize_cpu_p95_max | 20% | p95 this low means the VM rarely works hard |
| rightsize_cpu_p99_max | 40% | Headroom for near-peak load after a downsize |
| rightsize_cpu_peak_max | 70% | Any observed peak above this blocks downsizing (peak guard) |
| rightsize_mem_p99_max | 60% | Memory pressure guard |
| rightsize_mem_peak_max | 75% | Peak memory guard |
| target_cpu_util / target_mem_util | 70% / 75% | Sizing target after the change, used to pick the size |
| idle_cpu_p95_max | 3% | Below this, the VM is treated as idle |
| offhours_max_cpu_p95 | 10% | After-hours use above this blocks scheduling |
| offhours_min_business_cpu_p95 | 10% | No business-hours use means nothing to schedule around |
| orphan_min_detached_days | 14 | Recovery and retention windows |
| ip_unattached_min_days | 7 | Short gaps are often deployment churn |
| anomaly_z / anomaly_min_pct | 3.0 / 20% | Statistically unusual and materially large |
| anomaly_std_floor_pct | 1% | Stops a flat baseline from making tiny changes look huge |
| alert_cpu_spike | 95% for 4 intervals (1 h) | Sustained saturation, not one sample |
| alert_mem_high | 90% for 8 intervals (2 h) | Sustained memory pressure |
| alert_disk_limit | 85% | Early warning before a volume fills |
| budget_inr_monthly | ₹9,00,000 | Demo budget; the run-rate exceeds it so the overrun alert appears |

## 7. Rightsizing rule (`analysis._rightsize_gate`)
A VM is a candidate only if **all** hold:
1. p95 CPU < 20%, and p99 CPU < 40%.
2. Peak CPU < 70%.
3. p99 memory < 60%, and peak memory < 75%.
4. A smaller size in the **same family** has at least `ceil(vCPU x p99 / 70%)` vCPU and
   `ceil(RAM x peak memory / 75%)` GB, and costs less per hour.

Otherwise the VM goes to the **protected** list with the reasons. Protected entries are recorded when
an average-only rule (mean CPU < 20%) would have flagged the VM, or when memory is constrained.

If completeness is below the minimum, the same sizing is reported as **collect_more_data**: no savings,
confidence capped at 55.

## 8. Confidence score (`analysis._confidence`)
Start at 100 and subtract:
- `150 x max(0, 0.95 − completeness)` (missing evidence)
- 10 if the CPU coefficient of variation > 1.0, or 5 if > 0.5 (unstable load)
- `min(10, 2 x (invalid + duplicate rows))` (data quality)
- 10 if the headroom below the p99 limit is less than 25% (thin margin)
- 10 for Production (higher impact of error)

Clamped to 0–100. Levels: High ≥ 80, Medium 60–79, Low < 60.

## 9. Risk level
- **High**: Production, or peak memory ≥ 60%.
- **Medium**: Staging, confidence below 80, or any item with conditional savings.
- **Low**: other non-production items with high confidence.

## 10. Idle, schedule and orphan rules
- **Temporarily idle**: last-7-day p95 CPU < 3% after an earlier p95 ≥ 3%.
- **Consistently idle**: whole-window p95 CPU < 3%. Gets a *stop test* (compute savings,
  conditional on owner approval). Production is never auto-recommended.
- **Off-hours scheduling**: schedule allowed, Development or Staging, Low or Medium criticality, no
  conflicting restriction note (for example "nightly batch"), after-hours p95 < 10%, and business-hours
  p95 ≥ 10%. Savings = hourly x 730 x (1 − active fraction).
- **Orphaned volumes**: no attachment for at least 14 days. Savings are conditional on deletion after
  snapshot and retention review. **Unattached IPs**: at least 7 days.

## 11. Cost anomalies (`analysis.cost_anomalies`)
For each environment, a daily cost series is built from hourly price x 24, storage, IP and egress.
Each day from day 7 onward is compared with the previous 14 days, excluding days already flagged.
At least 7 baseline days are required. Flag when `z = (observed − mean) / max(stdev, 1% of mean) ≥ 3`
and the change is ≥ 20%. Contributors are the resources with the largest positive change against
their own baseline.

Note: egress scales with CPU, so the first day of a sudden workload spike can rise by less than the
second day. The detector therefore flags the second day in the synthetic spike scenario.

## 12. Simulator (`server/simulate.py`)
For each resource, the projected cost uses the **combined** actions of every accepted item on it.
Rightsizing and stopping together cost only the stopped VM's storage and IP. Overlap adjustment =
combined savings − sum of standalone savings (always ≤ 0 when items overlap).
Realized savings are never claimed; they require billing data.

## 13. Alerts
Alerts are evaluated on the static synthetic snapshot at analysis time. They are not real-time.
Budget projection assumes the current run-rate with no growth.

## 7. Missing daily egress

When a resource's daily outbound-transfer (egress) value is missing, the cost calculation uses that
resource's average daily egress in its place. This is a stand-in for cost purposes only. It is not
counted as a measurement, and it does not change completeness or the data-quality counts.

