# 5–7 minute demo script

Start with `python3 -m server.api` and open http://127.0.0.1:8080. Click **Load Demo Scenario** first
so the seeded state is known.

1. **Executive Overview (60 s).** Read the estimated monthly bill (about ₹10 lakh, synthetic) and the
   potential savings. Point out that every number comes from the same pricing engine, and the synthetic
   label on screen.
2. **Resource Explorer (60 s).** Open `dev-sandbox-api-01`. Show the 30-day daily history, the p95 and
   p99 values, and the tooltips that explain percentiles.
3. **Recommendation evidence (90 s).** Open the rightsizing item for that VM. Walk through evidence,
   the calculation (₹3.80 saving x 730 h = ₹2,774/month), the risk, the confidence score and the
   validation steps. Click **Accept into plan**.
4. **Cost Simulator (60 s).** Show the projected bill falling by the accepted amount and the plan
   comparison table.
5. **Safety demonstration (90 s).** Open the Resource Explorer and select `prod-checkout-api-01`: average CPU
   is low, but peak CPU reaches the 90s, so it is not downsized. Show `batch-reconcile-01` and the
   memory-constrained VM for the same reason. Then open `dev-qa-regression-01` and show it asks for more
   data with zero claimed savings.
6. **Alerts and anomalies (45 s).** Show the sustained CPU spike and the Production cost anomaly on
   2026-10-22. Acknowledge one alert with a note.
7. **Reports and close (45 s).** Download the recommendations CSV and open the management summary.
   State the limits: synthetic data, projected rather than realized savings, and no cloud writes.
