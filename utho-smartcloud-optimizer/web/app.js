/* UTHO SmartCloud Optimizer: single-page frontend (no external dependencies).
 * All data is synthetic. Every number shown comes from the /api endpoints. */
(function () {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inr = (n) => {
    if (n == null || Number.isNaN(Number(n))) return "—";
    const neg = Number(n) < 0;
    let s = Math.round(Math.abs(Number(n))).toString();
    if (s.length > 3) {
      let head = s.slice(0, -3);
      const tail = s.slice(-3);
      const parts = [];
      while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2); }
      if (head) parts.unshift(head);
      s = parts.join(",") + "," + tail;
    }
    return (neg ? "-" : "") + "₹" + s;
  };
  const compactInr = (n) => (Math.abs(n) >= 100000 ? "₹" + (n / 100000).toFixed(1) + "L" : inr(n));
  const pct = (n, d = 1) => (n == null ? "—" : Number(n).toFixed(d) + "%");
  const num = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));
  const short = (s, n = 12) => (String(s).length > n ? String(s).slice(0, n - 1) + "…" : String(s));

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const r = await fetch(path, init);
    let data;
    try { data = await r.json(); } catch (e) { data = { error: "The server returned an unreadable response." }; }
    if (!r.ok) throw new Error(data.error || "Request failed (" + r.status + ")");
    return data;
  }
  async function apiText(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error("Request failed (" + r.status + ")");
    return r.text();
  }

  const state = {
    meta: null,
    filters: { region: "", team: "", environment: "", type: "", start: "", end: "" },
    seq: 0,
  };

  function qs(params) {
    const p = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v) p.set(k, v); });
    const s = p.toString();
    return s ? "?" + s : "";
  }

  function toast(msg, kind = "ok") {
    const el = document.createElement("div");
    el.className = "toast" + (kind === "err" ? " err" : "");
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  function setStatus(msg) { $("#status").textContent = msg || ""; }

  // ---------- SVG charts ----------
  function emptyState(msg) { return '<div class="empty">' + esc(msg) + "</div>"; }

  function barChart(items, opts = {}) {
    if (!items || !items.length) return emptyState(opts.empty || "No data for this selection.");
    const W = opts.w || 600, H = opts.h || 230;
    const pad = { l: 62, r: 10, t: 10, b: 42 };
    const fmt = opts.fmt || compactInr;
    const max = Math.max(1, ...items.map((i) => Number(i.value) || 0));
    const bw = (W - pad.l - pad.r) / items.length;
    const plotH = H - pad.t - pad.b;
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "Bar chart")}">`;
    for (let k = 0; k <= 4; k++) {
      const y = pad.t + plotH * (1 - k / 4);
      s += `<line class="grid" x1="${pad.l}" x2="${W - pad.r}" y1="${y}" y2="${y}"/>`;
      s += `<text class="axis" x="${pad.l - 6}" y="${y + 4}" text-anchor="end">${esc(fmt(max * k / 4))}</text>`;
    }
    items.forEach((it, i) => {
      const v = Number(it.value) || 0;
      const bh = plotH * (v / max);
      const x = pad.l + i * bw + bw * 0.16;
      const y = H - pad.b - bh;
      s += `<rect class="bar" x="${x}" y="${y}" width="${bw * 0.68}" height="${Math.max(bh, 0)}" rx="3" fill="${esc(it.color || "#38bdf8")}">`
        + `<title>${esc(it.label)}: ${esc(fmt === compactInr ? inr(v) : fmt(v))}</title></rect>`;
      s += `<text class="axis" x="${x + bw * 0.34}" y="${H - pad.b + 16}" text-anchor="middle">${esc(short(it.label, opts.labelLen || 12))}</text>`;
    });
    return s + "</svg>";
  }

  function lineChart(xLabels, series, opts = {}) {
    if (!xLabels || !xLabels.length) return emptyState(opts.empty || "No measurements in this range.");
    const W = opts.w || 640, H = opts.h || 240;
    const pad = { l: 42, r: 12, t: 12, b: 34 };
    const yMax = opts.yMax ?? Math.max(1, ...series.flatMap((s) => s.values.filter((v) => v != null)));
    const n = xLabels.length;
    const xAt = (i) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
    const yAt = (v) => pad.t + (H - pad.t - pad.b) * (1 - Math.min(v, yMax) / yMax);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "Line chart")}">`;
    for (let k = 0; k <= 4; k++) {
      const v = (yMax * k) / 4;
      const y = yAt(v);
      s += `<line class="grid" x1="${pad.l}" x2="${W - pad.r}" y1="${y}" y2="${y}"/>`;
      s += `<text class="axis" x="${pad.l - 6}" y="${y + 4}" text-anchor="end">${esc(v.toFixed(yMax <= 100 ? 0 : 0))}${opts.unit || ""}</text>`;
    }
    const tickEvery = Math.max(1, Math.ceil(n / 7));
    const showTick = (i) => (opts.tickIndexes ? opts.tickIndexes.includes(i) : i % tickEvery === 0);
    xLabels.forEach((lab, i) => {
      if (showTick(i)) s += `<text class="axis" x="${xAt(i)}" y="${H - pad.b + 16}" text-anchor="middle">${esc(lab)}</text>`;
    });
    series.forEach((ser) => {
      let d = "";
      ser.values.forEach((v, i) => {
        if (v == null) return;
        d += (d ? "L" : "M") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
      });
      if (d) s += `<path d="${d}" fill="none" stroke="${esc(ser.color)}" stroke-width="${ser.width || 2}" stroke-linejoin="round"/>`;
    });
    if (opts.tooltip) {
      const colW = (W - pad.l - pad.r) / n;
      xLabels.forEach((_, i) => {
        const text = opts.tooltip(i);
        if (!text) return;
        s += `<rect class="hit" x="${(xAt(i) - colW / 2).toFixed(1)}" y="${pad.t}" width="${colW.toFixed(1)}" height="${H - pad.t - pad.b}" fill="transparent" tabindex="0" aria-label="${esc(text)}"><title>${esc(text)}</title></rect>`;
      });
    }
    return s + "</svg>" + legend(series);
  }

  function legend(series) {
    return '<div class="legend">' + series.map((s) =>
      `<span><span class="swatch" style="background:${esc(s.color)}"></span>${esc(s.name)}</span>`).join("") + "</div>";
  }

  function card(label, value, note, tone = "") {
    return `<div class="card ${tone}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div>${note ? `<div class="note">${esc(note)}</div>` : ""}</div>`;
  }

  function tag(text, cls) { return `<span class="tag ${esc(cls || String(text).toLowerCase())}">${esc(text)}</span>`; }

  function filterBar(withDates) {
    const f = state.filters;
    const meta = state.meta;
    const sel = (key, label, values) => `<label>${label}<select data-filter="${key}">`
      + `<option value="">All</option>` + values.map((v) =>
        `<option value="${esc(v)}" ${f[key] === v ? "selected" : ""}>${esc(v)}</option>`).join("")
      + "</select></label>";
    let html = '<div class="filters" role="group" aria-label="Filters">';
    html += sel("region", "Region", meta.regions) + sel("team", "Team", meta.teams)
      + sel("environment", "Environment", meta.environments) + sel("type", "Resource type", meta.types);
    if (withDates) {
      html += `<label>From<input type="date" data-filter="start" value="${esc(f.start)}"></label>`
        + `<label>To<input type="date" data-filter="end" value="${esc(f.end)}"></label>`;
    }
    html += `<div class="spacer"></div><button class="ghost" data-action="clear-filters">Clear filters</button></div>`;
    return html;
  }

  // ---------- routing ----------
  const ICONS = {
    overview: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
    resources: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
    recommendations: '<path d="M9 18h6M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z"/>',
    simulator: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/>',
    alerts: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    reports: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/>',
    "data-sources": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    "ai-command": '<path d="M12 3l1.8 4.6L18.5 9.5l-4.7 1.9L12 16l-1.8-4.6L5.5 9.5l4.7-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    settings: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7.5"/><path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3M4.6 4.6l2.1 2.1M17.3 17.3l2.1 2.1M4.6 19.4l2.1-2.1M17.3 6.7l2.1-2.1"/>',
  };
  const NAV = [
    ["overview", "Executive Overview"],
    ["resources", "Resource Explorer"],
    ["recommendations", "Optimization Recommendations"],
    ["simulator", "Cost Simulator"],
    ["ai-command", "AI Command Center"],
    ["alerts", "Alerts and Insights"],
    ["reports", "Reports"],
    ["data-sources", "Data Sources"],
    ["settings", "Settings"],
  ];

  function renderNav(active) {
    $("#nav").innerHTML = NAV.map(([id, label]) =>
      `<a href="#/${id}" ${active === id ? 'aria-current="page"' : ""}>` +
      `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${ICONS[id] || ""}</svg><span>${esc(label)}</span></a>`).join("");
  }

  async function route() {
    const my = ++state.seq;
    const hash = location.hash.replace(/^#\/?/, "") || "overview";
    const parts = hash.split("/");
    const section = parts[0];
    const arg = parts.slice(1).map(decodeURIComponent).join("/");
    renderNav(section);
    const view = $("#view");
    view.innerHTML = '<div class="loading">Loading…</div>';
    try {
      let html;
      if (section === "resources" && arg) html = await viewResource(arg);
      else if (section === "recommendations" && arg) html = await viewRecommendation(arg);
      else if (section === "overview") html = await viewOverview();
      else if (section === "resources") html = await viewResources();
      else if (section === "recommendations") html = await viewRecommendations();
      else if (section === "simulator") html = await viewSimulator();
      else if (section === "alerts") html = await viewAlerts();
      else if (section === "reports") html = await viewReports();
      else if (section === "data-sources") html = await viewDataSources();
      else if (section === "ai-command") html = await viewAICommand();
      else if (section === "settings") html = await viewSettings();
      else html = '<div class="error">That page does not exist.</div>';
      if (my !== state.seq) return;
      view.innerHTML = html;
      setStatus("");
    } catch (err) {
      if (my !== state.seq) return;
      view.innerHTML = `<div class="error">${esc(err.message)}</div>`;
    }
  }

  function rerender() { route(); }

  // ---------- views ----------
  // Places each daily average on its observation day (day 1 = first day of the window).
  // Days without measurements stay null, so the line breaks there instead of being interpolated.
  function observationTrend(trend, win) {
    const days = win.days;
    const origin = Date.parse(String(win.start).slice(0, 10) + "T00:00:00Z");
    const cpu = Array(days).fill(null);
    const mem = Array(days).fill(null);
    trend.forEach((x) => {
      const i = Math.round((Date.parse(x.date + "T00:00:00Z") - origin) / 86400000);
      if (i >= 0 && i < days) { cpu[i] = x.cpu_mean; mem[i] = x.mem_mean; }
    });
    const markerIndexes = [5, 10, 15, 20, 25, 30].map((d) => d - 1).filter((i) => i < days);
    return { labels: Array.from({ length: days }, (_, i) => String(i + 1)), cpu, mem, markerIndexes };
  }

  async function viewOverview() {
    const d = await api("/api/overview" + qs({ ...state.filters, start: "", end: "" }));
    const k = d.kpis;
    const obs = observationTrend(d.trend, state.meta.window);
    const teams = [...new Set(d.spend_by_team_region.map((x) => x.team))];
    const regions = [...new Set(d.spend_by_team_region.map((x) => x.region))];
    const matrix = teams.map((tm) => `<tr><th scope="row">${esc(tm)}</th>${regions.map((r) => {
      const hit = d.spend_by_team_region.find((x) => x.team === tm && x.region === r);
      return `<td class="num">${inr(hit ? hit.value : 0)}</td>`;
    }).join("")}</tr>`).join("");
    const demoSteps = [
      ["#/overview", "Open this overview and read the estimated monthly bill."],
      ["#/resources/vm-0001", "Inspect a VM's 30-day CPU and memory history and its percentiles."],
      ["#/recommendations/rightsize:vm-0001", "Review the evidence, calculation and risk for a rightsizing recommendation."],
      ["#/simulator", "Compare the current bill with the accepted plan. Accept the item from step 3 first."],
      ["#/recommendations/collect_more_data:vm-0007", "See a low-confidence item that asks for more data instead of claiming savings."],
      ["#/recommendations", "Filter by risk to see which changes are low risk and which need a closer review."],
      ["#/reports", "Download the CSV exports and the management summary."],
    ];
    return `
    <header class="page-head">
      <div><h1>Executive Overview</h1>
      <p class="sub">Monitor your cloud spending, identify underutilized resources, and discover opportunities to reduce costs through safe, data-driven recommendations.</p></div>
    </header>
    ${filterBar(false)}
    <section class="cards" aria-label="Key metrics">
      ${card("Resources", num(k.resources))}
      ${card("Estimated monthly cost", inr(k.current_monthly))}
      ${card("Potential monthly savings", inr(k.potential_savings_monthly), pct(k.optimization_pct) + " of estimated cost, overlap-adjusted", "good")}
      <div class="card">
        <div class="label">Optimisation Recommendations</div><div class="value">${esc(num(k.resources_needing_investigation))}</div>
        <div class="note"><a href="#/recommendations">Open Optimisation Recommendations</a></div></div>
      ${card("High-risk recommendations", num(k.high_risk_recommendations), "Open items marked High risk", k.high_risk_recommendations ? "warn" : "")}
      <div class="card ${k.active_alerts ? "warn" : ""}"><div class="label">Active alerts</div><div class="value">${esc(num(k.active_alerts))}</div>
        <div class="note"><a href="#/alerts">Open Alerts and Insights</a></div></div>
    </section>
    <section class="grid2">
      <article class="panel"><h2>Estimated spend by environment</h2>
        ${barChart(d.spend_by_environment.map((x) => ({ label: x.label, value: x.value })), { label: "Spend by environment" })}
        <p class="hint">Unattributed covers unattached storage and IPs, which have no owning team.</p></article>
      <article class="panel"><h2>Potential savings by category</h2>
        ${barChart(d.savings_by_category.map((x) => ({ label: x.category, value: x.savings })), { label: "Savings by category", labelLen: 14 })}
        <p class="hint">Overlap-adjusted. Items with conditional savings still need approval.</p></article>
      <article class="panel"><h2>CPU utilization distribution</h2>
        ${barChart(d.cpu_distribution.map((v, i) => ({ label: d.distribution_labels[i], value: v })), { label: "CPU distribution", fmt: num, labelLen: 8, color: "#38bdf8" })}
        <p class="hint">Share of 15-minute measurements in each CPU band, for the selected date range.</p></article>
      <article class="panel"><h2>Memory utilization distribution</h2>
        ${barChart(d.memory_distribution.map((v, i) => ({ label: d.distribution_labels[i], value: v })), { label: "Memory distribution", fmt: num, labelLen: 8, color: "#7dd3fc" })}</article>
      <article class="panel wide"><h2>Utilization trend (daily average)</h2>
        ${lineChart(obs.labels, [
          { name: "CPU %", color: "#38bdf8", values: obs.cpu },
          { name: "Memory %", color: "#f5b948", values: obs.mem }],
          { yMax: 100, unit: "%", label: "Daily utilization trend by observation day", w: 1000,
            tickIndexes: obs.markerIndexes,
            tooltip: (i) => (obs.cpu[i] == null && obs.mem[i] == null ? "" : `Day ${i + 1}: CPU ${pct(obs.cpu[i])}, Memory ${pct(obs.mem[i])}`) })}</article>
      <article class="panel wide"><h2>Spend by team and region</h2>
        <div class="table-wrap"><table><thead><tr><th>Team</th>${regions.map((r) => `<th class="num">${esc(r)}</th>`).join("")}</tr></thead><tbody>${matrix}</tbody></table></div></article>
      <article class="panel wide"><h2>Guided demo</h2>
        <ol class="steps">${demoSteps.map(([href, text]) => `<li><a href="${href}">${esc(text)}</a></li>`).join("")}</ol></article>
    </section>`;
  }

  async function viewResources() {
    const sortKey = state.sort || "monthly_inr";
    const sortDir = state.dir || "desc";
    const d = await api("/api/resources" + qs({ ...state.filters, q: state.search || "", sort: sortKey, dir: sortDir }));
    // Only these four text columns can be sorted. Each click on another one starts ascending.
    const SORTABLE = ["environment", "region", "team", "type"];
    const head = (key, label, numeric = false) => {
      const cls = numeric ? ' class="num"' : "";
      if (!SORTABLE.includes(key)) return `<th scope="col"${cls}>${esc(label)}</th>`;
      const active = sortKey === key;
      const arrow = active ? (sortDir === "asc" ? " ↑" : " ↓") : "";
      const aria = active ? (sortDir === "asc" ? "ascending" : "descending") : "none";
      return `<th scope="col" aria-sort="${aria}"${cls}><button type="button" class="th-sort" data-action="sort" data-key="${key}">${esc(label)}${arrow}</button></th>`;
    };
    const rows = d.items.map((r) => `<tr class="click" data-open="#/resources/${esc(r.id)}" tabindex="0">
      <td><strong>${esc(r.name)}</strong><div class="muted" style="font-size:.78rem">${esc(r.id)}${r.scenario_note ? " · " + esc(r.scenario_note.replace(/^Demo: /, "demo: ")) : ""}</div></td>
      <td>${esc(r.environment)}</td><td>${esc(r.region)}</td><td>${esc(r.team)}</td><td>${esc(r.type)}</td>
      <td class="num">${inr(r.monthly_inr)}</td>
      <td class="num" title="Average CPU across all measurements">${pct(r.cpu_mean)}</td>
      <td class="num" title="95% of measurements were at or below this CPU level">${pct(r.cpu_p95)}</td>
      <td class="num">${pct(r.mem_mean)}</td>
      <td class="num">${r.recommendation_count ? tag(r.recommendation_count + " open", "open") : '<span class="muted">0</span>'}</td></tr>`).join("");
    return `
    <header class="page-head"><div><h1>Resource Explorer</h1></div></header>
    ${filterBar(false)}
    <div class="row" style="margin-bottom:.7rem">
      <input type="search" id="resource-search" aria-label="Search resources" placeholder="Search by name or ID" value="${esc(state.search || "")}" style="min-width:260px">
      <span class="muted">${num(d.count)} resources</span></div>
    <p class="hint">Open recommendations across all resources: ${num(d.summary.open_recommendations)}. ${num(d.summary.open_on_vms)} are on VMs (the Recs column) and ${num(d.summary.open_on_other_resources)} are on orphaned volumes and public IPs. ${num(d.summary.open_low_risk)} are low risk. These totals match the Cost Simulator plans.</p>
    <div class="table-wrap"><table><thead><tr>${head("name", "Resource")}${head("environment", "Environment")}${head("region", "Region")}${head("team", "Team")}${head("type", "Type")}${head("monthly_inr", "Est. monthly", true)}${head("cpu_mean", "CPU avg", true)}${head("cpu_p95", "CPU p95", true)}${head("mem_mean", "Mem avg", true)}${head("recommendation_count", "Recs", true)}</tr></thead>
      <tbody>${rows || `<tr><td colspan="10"><div class="empty">No resources match these filters.</div></td></tr>`}</tbody></table></div>
    <p class="hint">Percentiles use linear interpolation over observed measurements. Missing measurements are excluded, never treated as zero.</p>`;
  }

  async function viewResource(id) {
    const q = qs({ start: state.filters.start, end: state.filters.end });
    const d = await api("/api/resources/" + encodeURIComponent(id) + q);
    const r = d.resource;
    const c = r.cpu || {};
    const m = r.mem || {};
    const dd = d.daily;
    const dayLabels = dd.map((x) => x.date.slice(5));
    const hourLabels = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
    const hp = r.hour_profile.map((v) => (v == null ? null : v));
    const step = Math.max(1, Math.floor(d.recent_15min.length / 200));
    const recent = d.recent_15min.filter((_, i) => i % step === 0);
    const helpTip = (t) => `<span class="dotted" title="${esc(t)}">`;
    const statRow = (label, tip, v) => `<tr><th scope="row">${helpTip(tip)}${esc(label)}</span></th><td class="num">${pct(v)}</td></tr>`;
    const recs = d.recommendations.map((x) => `<tr class="click" data-open="#/recommendations/${esc(encodeURIComponent(x.id))}" tabindex="0">
      <td>${esc(x.category)}</td><td>${tag(x.risk, x.risk.toLowerCase())}</td><td class="num">${pct(x.confidence, 0)}</td>
      <td class="num">${inr(x.savings_monthly)}</td><td>${tag(x.status, x.status)}</td></tr>`).join("");
    const alerts = d.alerts.map((a) => `<li>${tag(a.severity, a.severity)} <strong>${esc(a.title)}</strong> — ${esc(a.detail)}${a.acknowledged ? ' <span class="muted">(acknowledged)</span>' : ""}</li>`).join("");
    const prot = d.protected.map((p) => `<li>${tag(p.verdict, "info")} ${esc(p.reasons.join(" "))}</li>`).join("");
    return `
    <header class="page-head"><div>
      <p class="muted" style="margin:0"><a href="#/resources">Resource Explorer</a> / ${esc(r.id)}</p>
      <h1>${esc(r.name)}</h1>
      <p class="sub">${esc(r.type)} · ${r.vcpu} vCPU · ${r.ram_gb} GB RAM · ${esc(r.region)} · ${esc(r.environment)} · ${esc(r.team)} · criticality ${esc(r.criticality)}</p>
      ${r.scenario_note ? `<p class="hint">${esc(r.scenario_note)}</p>` : ""}</div>
      <div class="actions"><a class="tag" href="/api/export/metrics.csv${qs({ resource: r.id })}" download>Export metrics CSV</a></div></header>
    ${filterBar(true)}
    <section class="cards">
      ${card("Estimated monthly cost", inr(r.cost.total), `Compute ${inr(r.cost.compute)} · storage ${inr(r.cost.storage)} · egress ${inr(r.cost.egress)}`)}
      ${card("CPU average / p95", `${pct(c.mean)} / ${pct(c.p95)}`, `p99 ${pct(c.p99)} · peak ${pct(c.max)}`)}
      ${card("Memory average / p99", `${pct(m.mean)} / ${pct(m.p99)}`, `Peak ${pct(m.max)}`, m.max >= 75 ? "warn" : "")}
      ${card("Data completeness", pct(r.completeness * 100, 1), `${num(r.valid)} valid · ${num(r.missing)} missing · ${num(r.invalid)} invalid`, r.completeness < 0.9 ? "warn" : "good")}
    </section>
    <section class="grid2">
      <article class="panel wide"><h2>CPU and memory history (daily)</h2>
        ${lineChart(dayLabels, [
          { name: "CPU mean", color: "#38bdf8", values: dd.map((x) => x.cpu_mean) },
          { name: "CPU p95", color: "#7dd3fc", values: dd.map((x) => x.cpu_p95), width: 1.5 },
          { name: "CPU peak", color: "#f47c7c", values: dd.map((x) => x.cpu_max), width: 1.5 },
          { name: "Memory mean", color: "#f5b948", values: dd.map((x) => x.mem_mean) },
          { name: "Memory peak", color: "#fb923c", values: dd.map((x) => x.mem_max), width: 1.5 }],
          { yMax: 100, unit: "%", label: "Daily CPU and memory", w: 1000 })}</article>
      <article class="panel"><h2>Last 7 days, 15-minute samples</h2>
        ${lineChart(recent.map((x) => new Date(x.ts * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).replace(",", "")), [
          { name: "CPU %", color: "#38bdf8", values: recent.map((x) => x.cpu), width: 1.2 },
          { name: "Memory %", color: "#f5b948", values: recent.map((x) => x.mem), width: 1.2 }],
          { yMax: 100, unit: "%", label: "Recent 15-minute samples", w: 520, h: 220 })}</article>
      <article class="panel"><h2>Daily pattern (average CPU by IST hour)</h2>
        ${barChart(hp.map((v, i) => ({ label: hourLabels[i], value: v ?? 0 })), { label: "CPU by hour", fmt: (v) => v.toFixed(0) + "%", labelLen: 3, color: "#38bdf8" })}
        <p class="hint">Weekday average CPU ${pct(d.weekday_cpu_mean)} · weekend average ${pct(d.weekend_cpu_mean)}. Business hours ${pct(r.cpu_business_p95)} at p95 · off-hours ${pct(r.cpu_offhours_p95)} at p95.</p></article>
      <article class="panel"><h2>Percentiles and load</h2>
        <table><tbody>
          ${statRow("Average CPU", "The arithmetic mean. Low averages alone do not justify downsizing.", c.mean)}
          ${statRow("Median CPU (p50)", "Half of the measurements were below this value.", c.p50)}
          ${statRow("95th-percentile CPU (p95)", d.percentile_help.p95, c.p95)}
          ${statRow("99th-percentile CPU (p99)", d.percentile_help.p99, c.p99)}
          ${statRow("Peak CPU", d.percentile_help.max, c.max)}
          ${statRow("Memory p99", "99% of memory readings were at or below this level.", m.p99)}
          ${statRow("Peak memory", d.percentile_help.max, m.max)}
          <tr><th scope="row">Disk mean</th><td class="num">${pct(r.disk && r.disk.mean)}</td></tr>
          <tr><th scope="row">Idle classification</th><td class="num">${esc(r.idle_state.replace(/_/g, " "))}</td></tr>
        </tbody></table></article>
      <article class="panel wide"><h2>Recommendations for this resource</h2>
        ${d.recommendations.length ? `<div class="table-wrap"><table><thead><tr><th>Category</th><th>Risk</th><th class="num">Confidence</th><th class="num">Savings / mo</th><th>Status</th></tr></thead><tbody>${recs}</tbody></table></div>` : emptyState("No open recommendation for this resource.")}
        ${prot ? `<h3>Protected checks</h3><ul class="plain">${prot}</ul>` : ""}</article>
      <article class="panel wide"><h2>Alerts</h2>${alerts ? `<ul class="plain">${alerts}</ul>` : emptyState("No alerts for this resource.")}</article>
    </section>`;
  }

  async function viewRecommendations() {
    const TABS = ["open", "accepted", "rejected", "all"];
    const tab = TABS.includes(state.recTab) ? state.recTab : "open";
    const status = tab === "all" ? "" : tab;
    const d = await api("/api/recommendations" + qs({ status, risk: state.recRisk || "", q: state.recSearch || "", region: state.filters.region, environment: state.filters.environment, team: state.filters.team }));
    const rows = d.items.map((r) => `<tr class="click" data-open="#/recommendations/${esc(encodeURIComponent(r.id))}" tabindex="0">
      <td><strong>${esc(r.title)}</strong><div class="muted" style="font-size:.78rem">${esc(r.category)} · ${esc(r.environment)} · ${esc(r.team)}${r.overlaps_with && r.overlaps_with.length ? " · overlaps with another item" : ""}</div></td>
      <td>${esc(r.current)} → ${esc(r.proposed)}</td>
      <td class="num">${inr(r.savings_monthly)}</td>
      <td>${tag(r.risk, r.risk.toLowerCase())}</td>
      <td class="num">${pct(r.confidence, 0)}<div class="muted" style="font-size:.74rem">${esc(r.confidence_level)}</div></td>
      <td>${tag(r.status, r.status)}</td></tr>`).join("");
    return `
    <header class="page-head"><div><h1>Optimization Recommendations</h1>
      <p class="sub">Every change is a proposal. Accepting adds it to the simulated plan only. Nothing changes in a cloud account.</p></div></header>
    ${recTabs(tab)}
    <div class="filters">
      <label>Risk<select data-rec-filter="recRisk"><option value="">All</option>${["Low", "Medium", "High"].map((x) => `<option ${state.recRisk === x ? "selected" : ""}>${x}</option>`).join("")}</select></label>
      <label>Search<input type="search" data-rec-filter="recSearch" value="${esc(state.recSearch || "")}" placeholder="Resource or title"></label>
    </div>
    <div class="table-wrap"><table><thead><tr><th>Recommendation</th><th>Change</th><th class="num">Savings / mo</th><th>Risk</th><th class="num">Confidence</th><th>Status</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6"><div class="empty">No recommendations in this view.</div></td></tr>`}</tbody></table></div>
    <p class="hint">Sorted by savings. Savings for overlapping items are counted once in the Cost Simulator.</p>`;
  }

  function recTabs(active) {
    const tabs = [["open", "Open"], ["accepted", "Accepted"], ["rejected", "Rejected"], ["all", "All"]];
    return `<div class="tabs" role="tablist">${tabs.map(([k, l]) =>
      `<button role="tab" aria-selected="${active === k}" data-action="rec-tab" data-tab="${k}">${esc(l)}</button>`).join("")}</div>`;
  }

  async function viewRecommendation(id) {
    const d = await api("/api/recommendations/" + encodeURIComponent(id));
    const r = d.recommendation;
    const list = (items) => (items && items.length ? `<ul class="plain">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="muted">None.</p>');
    const audit = d.audit.length ? d.audit.map((a) => `<li><span class="muted">${esc(a.at)}</span> · ${esc(a.event)}${a.detail ? " — " + esc(a.detail) : ""}</li>`).join("") : "<li class=\"muted\">No decisions recorded yet.</li>";
    const isOrphan = r.type === "orphan_volume" || r.type === "orphan_public_ip";
    return `
    <header class="page-head"><div>
      <p class="muted" style="margin:0"><a href="#/recommendations">Recommendations</a> / ${esc(r.category)}</p>
      <h1>${esc(r.title)}</h1>
      <p class="sub">${tag(r.risk + " risk", r.risk.toLowerCase())}</p></div></header>
    <section class="grid2">
      <article class="panel"><h2>What should change?</h2>
        <dl class="kv"><dt>Resource</dt><dd>${r.resource_id && !isOrphan ? `<a href="#/resources/${esc(r.resource_id)}">${esc(r.resource_name)}</a>` : esc(r.resource_name)}</dd>
          <dt>Current</dt><dd>${esc(r.current)}</dd><dt>Proposed</dt><dd>${esc(r.proposed)}</dd>
          <dt>Environment</dt><dd>${esc(r.environment)} · ${esc(r.team)} · ${esc(r.region)}</dd></dl>
        <h3>Why the system believes it is useful</h3>
        <div class="explain">${esc(r.explanation)}</div>
        <h3>Evidence</h3>${list(r.evidence)}
      </article>
      <article class="panel"><h2>How much could it save?</h2>
        ${barChart([{ label: "Current", value: r.monthly_before }, { label: "Proposed", value: r.monthly_after, color: "#4ade80" }], { label: "Current versus proposed monthly cost", h: 200 })}
        <dl class="kv" style="margin-top:.6rem"><dt>Estimated saving</dt><dd><strong>${inr(r.savings_monthly)}</strong> / month · ${inr(r.savings_annual)} / year (${pct(r.savings_pct)})</dd>
          <dt>Savings type</dt><dd>${r.conditional_savings ? "Conditional: counted only after approval and cleanup" : "Projected from synthetic pricing"}</dd></dl>
        <h3>Calculation</h3><div class="formula">${esc(r.calculation)}</div>
        <p class="hint">Projected savings are not realized savings until billing data confirms them.</p>
      </article>
      <article class="panel"><h2>Assumptions</h2>${list(r.assumptions)}
        <h3>Preconditions</h3>${list(r.preconditions)}</article>
      <article class="panel"><h2>Validation steps</h2>${list(r.validation_steps)}
        <h3>What could go wrong</h3>${list(r.what_could_go_wrong)}</article>
      <article class="panel wide"><h2>Your decision</h2>
        <p class="muted" style="margin-top:0">Accepting adds this item to the simulated plan. It does not change any cloud resource.</p>
        <label for="rec-notes" class="muted">Notes (saved with the decision)</label>
        <textarea id="rec-notes" maxlength="1000" placeholder="Why you accepted or rejected this">${esc(r.notes || "")}</textarea>
        <div class="decide">
          <button data-action="decide" data-rec="${esc(r.id)}" data-status="accepted" ${r.status === "accepted" ? 'disabled aria-disabled="true"' : ""}>${r.status === "accepted" ? "Accepted" : "Accept"}</button>
          <button class="danger" data-action="decide" data-rec="${esc(r.id)}" data-status="rejected">Reject</button>
        </div>
        ${r.overlaps_with && r.overlaps_with.length ? `<p class="hint">Overlaps with: ${r.overlaps_with.map((x) => esc(x)).join(", ")}. The simulator counts combined savings once.</p>` : ""}
      </article>
      <article class="panel wide"><h2>Decision history</h2><ul class="plain">${audit}</ul></article>
    </section>`;
  }

  async function viewSimulator() {
    const scope = state.simScope || "accepted";
    const d = await api("/api/simulate?scope=" + scope);
    const plans = d.plans;
    const acc = plans.accepted;
    const order = [["accepted", "Accepted plan"], ["low_risk", "All low-risk open"], ["everything", "Every open recommendation"]];
    const compare = order.map(([k, label]) => {
      const p = plans[k];
      return `<tr><td>${esc(label)}</td><td class="num">${num(p.recommendation_count)}</td><td class="num">${inr(p.baseline_monthly)}</td>
        <td class="num">${inr(p.projected_monthly)}</td><td class="num"><strong>${inr(p.savings_monthly)}</strong></td>
        <td class="num">${pct(p.savings_pct)}</td><td class="num">${inr(p.overlap_adjustment)}</td></tr>`;
    }).join("");
    const envRows = Object.entries(acc.by_environment).map(([e, v]) => `<tr><td>${esc(e)}</td><td class="num">${inr(v.baseline)}</td><td class="num">${inr(v.projected)}</td><td class="num">${inr(v.savings)}</td></tr>`).join("");
    const teamRows = Object.entries(acc.by_team).map(([e, v]) => `<tr><td>${esc(e)}</td><td class="num">${inr(v.baseline)}</td><td class="num">${inr(v.projected)}</td><td class="num">${inr(v.savings)}</td></tr>`).join("");
    const recRows = acc.by_recommendation.map((r) => `<tr><td><a href="#/recommendations/${esc(encodeURIComponent(r.id))}">${esc(r.resource_name)}</a></td><td>${esc(r.category)}</td><td class="num">${inr(r.standalone_savings)}</td><td>${r.overlaps_with.length ? tag("overlap", "medium") : ""}</td></tr>`).join("");
    return `
    <header class="page-head"><div><h1>Cost Simulator</h1>
      <p class="sub">Projected bills for the changes that are accepted. Every projection uses the same pricing engine as the recommendations.</p></div>
      <div class="actions"><button class="danger" data-action="reset-sim">Reset simulation</button></div></header>
    <section class="cards">
      ${card("Current monthly cost", inr(acc.baseline_monthly), "Price book x 730 h, storage, IP, egress")}
      ${card("Projected with accepted plan", inr(acc.projected_monthly), `${acc.recommendation_count} accepted item(s)`)}
      ${card("Projected savings / month", inr(acc.savings_monthly), `${inr(acc.savings_annual)} a year · ${pct(acc.savings_pct)}`, "good")}
      ${card("Overlap adjustment", inr(acc.overlap_adjustment), "Negative values remove double counting", acc.overlap_adjustment < 0 ? "warn" : "")}
    </section>
    <section class="grid2">
      <article class="panel wide"><h2>Compare plans</h2>
        <div class="table-wrap"><table><thead><tr><th>Plan</th><th class="num">Items</th><th class="num">Baseline</th><th class="num">Projected</th><th class="num">Savings / mo</th><th class="num">%</th><th class="num">Overlap adj.</th></tr></thead><tbody>${compare}</tbody></table></div>
        <p class="hint">Projected savings are not realized savings. Realized savings need billing data to confirm them.</p></article>
      <article class="panel"><h2>Impact by environment (accepted plan)</h2>
        <div class="table-wrap"><table><thead><tr><th>Environment</th><th class="num">Baseline</th><th class="num">Projected</th><th class="num">Savings</th></tr></thead><tbody>${envRows}</tbody></table></div></article>
      <article class="panel"><h2>Impact by team (accepted plan)</h2>
        <div class="table-wrap"><table><thead><tr><th>Team</th><th class="num">Baseline</th><th class="num">Projected</th><th class="num">Savings</th></tr></thead><tbody>${teamRows}</tbody></table></div></article>
      <article class="panel wide"><h2>Breakdown by recommendation (accepted plan)</h2>
        ${acc.by_recommendation.length ? `<div class="table-wrap"><table><thead><tr><th>Resource</th><th>Category</th><th class="num">Standalone savings</th><th></th></tr></thead><tbody>${recRows}</tbody></table></div>` : emptyState("No accepted items yet. Accept recommendations to build a plan.")}
      </article>
      <article class="panel wide"><h2>Formula</h2><div class="formula">${esc(d.pricing_formula)}</div>
        <p class="hint">${esc(d.realized_savings_note)}</p></article>
    </section>`;
  }

  async function viewAlerts() {
    const show = state.alertShow || "open";
    const d = await api("/api/alerts" + qs({ show, severity: state.alertSev || "" }));
    const rows = d.items.map((a) => `<tr>
      <td>${tag(a.severity, a.severity)}</td>
      <td><strong>${esc(a.title)}</strong><div class="muted" style="font-size:.8rem">${esc(a.resource_name)} · ${esc(a.observed_at)}</div></td>
      <td>${esc(a.detail)}<ul class="plain muted" style="font-size:.84rem">${a.next_steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></td>
      <td>${a.acknowledged ? `<span class="muted">Acknowledged ${esc((a.acked_at || "").slice(0, 16).replace("T", " "))}</span>${a.ack_note ? `<div class="muted" style="font-size:.8rem">“${esc(a.ack_note)}”</div>` : ""}`
        : `<div class="row"><input aria-label="Acknowledgement note" placeholder="Note (optional)" data-ack-note="${esc(a.id)}" style="width:150px"><button class="ghost" data-action="ack" data-alert="${esc(a.id)}">Acknowledge</button></div>`}</td></tr>`).join("");
    return `
    <header class="page-head"><div><h1>Alerts and Insights</h1>
      <p class="sub">Monitor cloud infrastructure health, identify potential cost inefficiencies, and detect resource performance issues early. Gain actionable insights into emerging risks and know when investigation is needed to maintain efficient and reliable operations.</p></div></header>
    <div class="filters">
      <label>Show<select data-alert-filter="alertShow">${[["open", "Open"], ["acknowledged", "Acknowledged"], ["all", "All"]].map(([v, l]) => `<option value="${v}" ${show === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Severity<select data-alert-filter="alertSev"><option value="">All</option>${["critical", "warning", "info"].map((v) => `<option value="${v}" ${state.alertSev === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    </div>
    <div class="table-wrap"><table><thead><tr><th>Severity</th><th>Alert</th><th>Detail and next steps</th><th>Acknowledgement</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4"><div class="empty">No alerts match this view.</div></td></tr>`}</tbody></table></div>
    <h2 style="margin-top:1.2rem">Cost anomalies</h2>
    <p class="hint">Compare each environment’s daily estimated costs against its preceding 14-day baseline to identify unusual spending patterns. Review detected anomalies, including cost variations, percentage changes, affected resources, and recommended investigation steps.</p>
    <div id="anomalies">${await anomaliesHtml()}</div>`;
  }

  async function anomaliesHtml() {
    const d = await api("/api/cost-anomalies");
    if (!d.items.length) return emptyState("No cost anomalies detected in the window.");
    return d.items.map((a) => {
      const contrib = a.contributors.map((c) => {
        const share = a.abs_change_inr > 0 ? (c.delta_inr / a.abs_change_inr) * 100 : null;
        return `<tr><td><a href="#/resources/${esc(c.resource_id)}">${esc(c.resource_name)}</a></td>`
          + `<td class="num">+${inr(c.delta_inr)}</td><td class="num">${share === null ? "—" : share.toFixed(1) + "%"}</td></tr>`;
      }).join("");
      const more = a.contributors_total > a.contributors.length
        ? `<p class="hint">Showing the top ${a.contributors.length} of ${a.contributors_total} resources with a cost increase.</p>` : "";
      return `<article class="panel anomaly">
        <div class="anomaly-head">
          <div><div class="muted">Environment</div><strong>${esc(a.environment)}</strong></div>
          <div><div class="muted">Anomaly date</div><strong>${esc(a.date)}</strong></div>
          <div>${tag(a.severity, a.severity)}</div>
        </div>
        <div class="anomaly-stats">
          <div><span class="muted">Observed cost</span><strong>${inr(a.observed_inr)}</strong><span class="muted">for the day</span></div>
          <div><span class="muted">Baseline cost</span><strong>${inr(a.baseline_inr)}</strong><span class="muted">average day</span></div>
          <div><span class="muted">Increase</span><strong class="increase-value">+${inr(a.abs_change_inr)}</strong><span class="muted">${a.pct_change > 0 ? "+" : ""}${a.pct_change}% above baseline</span></div>
          <div><span class="muted">Baseline period</span><strong>Previous ${a.baseline_days} days</strong><span class="muted">before the anomaly</span></div>
        </div>
        <h3>Identified contributors</h3>
        <p class="hint">Resources whose cost rose the most compared with their own baseline. This shows where the cost rose, not why.</p>
        <div class="table-wrap"><table><thead><tr><th>Resource</th><th class="num">Added cost</th><th class="num">Share of increase</th></tr></thead>
          <tbody>${contrib || '<tr><td colspan="3" class="muted">None identified.</td></tr>'}</tbody></table></div>
        ${more}
        <h3>Suggested investigation</h3>
        <ol class="steps-list">${a.suggested_steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
        <details class="tech"><summary>Technical details</summary>
          <p>Z-score ${a.z_score}. It measures how unusual this day's cost is compared with the normal day-to-day variation in the baseline period. Higher means more unusual. It is a statistical score, not a probability.</p>
          <p>${esc(d.method)}</p>
          <p class="hint">${esc(a.basis)}</p>
        </details>
      </article>`;
    }).join("");
  }

  async function viewReports() {
    const exp = [
      ["resources.csv", "Resources (CSV)", "Download your complete cloud resource list, including monthly costs, usage levels, and resource health."],
      ["recommendations.csv", "Recommendations (CSV)", "View all optimization suggestions, how much you could save, the risks involved, and their current status."],
      ["cost-report.csv", "Cost report (CSV)", "Compare current cloud costs with projected costs after applying your accepted recommendations."],
      ["audit.csv", "Audit history (CSV)", "Download a record of decisions, acknowledged alerts, settings changes, and simulation resets."],
      ["summary.md", "Management summary (Markdown)", "Get a quick overview of cloud spending, potential savings, key optimization opportunities, and important risks for management review."],
    ];
    const summary = await apiText("/api/export/summary.md");
    const audit = (await api("/api/audit")).items.slice(0, 15);
    return `
    <header class="page-head"><div><h1>Reports</h1>
      <p class="sub">Turn cloud insights into action. Explore spending, uncover savings opportunities, and export clear, data-driven reports to make smarter cloud cost decisions with confidence.</p></div></header>
    <section class="grid2">
      <article class="panel"><h2>Downloads</h2>
        <ul class="plain">${exp.map(([f, l, d]) => `<li><a href="/api/export/${f}" download><strong>${esc(l)}</strong></a><div class="muted" style="font-size:.85rem">${esc(d)}</div></li>`).join("")}</ul>
      </article>
      <article class="panel"><h2>Management summary</h2><pre class="md">${esc(summary)}</pre></article>
      <article class="panel wide"><h2>Recent audit history</h2>
        ${audit.length ? `<div class="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Event</th><th>Item</th><th>Detail</th></tr></thead><tbody>${audit.map((a) => `<tr><td class="muted">${esc(a.at)}</td><td>${esc(a.event)}</td><td>${esc(a.rec_id || "")}</td><td>${esc(a.detail)}</td></tr>`).join("")}</tbody></table></div>` : emptyState("No audit events yet.")}</article>
    </section>`;
  }

  // ---------- data sources ----------
  const DS_NOTE_DAY = (iso) => (iso ? new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric" }) : "Unavailable");

  async function viewDataSources() {
    const d = await api("/api/data-sources");
    const inv = d.inventory;
    const docs = d.documents;
    return `
    <header class="page-head"><div><h1>Data Sources</h1>
      <p class="sub">Where the data comes from, whether it is reliable, and whether the analysis can safely generate recommendations.</p></div>
</header>
    <div class="ds-notice" role="note"><strong>Demo data only.</strong> ${esc(d.notice.replace("Demo data only. ", ""))}</div>

    <article class="panel wide" style="margin-top:.9rem"><h2>Resource Data Sources and Documentation</h2>
      <h3>Where does our resource data come from?</h3>
      <p>Our resource inventory is generated by a deterministic synthetic dataset generator. It simulates the infrastructure of a fictional SaaS company, representing ${num(inv.resource_count)} virtual machines and related storage and public IP resources across simulated regions, environments and teams.</p>
      <p>Resource attributes such as CPU capacity, memory, storage, environment, state, ownership, utilization metrics and illustrative pricing are generated or assigned by the project's data-generation logic. Utilization is modeled at ${esc(String(d.interval_minutes))}-minute intervals over the configured ${inv.observation_days}-day observation period.</p>
      <p>These resources and prices are illustrative. They are not inventory or billing information retrieved from a live cloud provider. Generation is repeatable: seed ${esc(String(inv.seed))} produces the same inventory every time the demo is loaded.</p>

      <h3>Dataset coverage</h3>
      <div class="cards">
        ${card("Resource count", num(inv.resource_count), `${inv.environments.length} environments, ${inv.teams.length} teams`)}
        ${card("Observation period", `${DS_NOTE_DAY(d.window.start)} to ${DS_NOTE_DAY(d.window.end)}`, `${inv.observation_days} days, ${num(inv.expected_intervals_per_resource)} expected intervals per resource`)}
        ${card("Sampling interval", `${d.interval_minutes} minutes`, "Interval at which utilization is measured")}
        ${card("Simulated regions", inv.regions.join(", "), "Simulated deployment regions")}
        ${card("Pricing", "Synthetic", d.pricing)}
      </div>

    </article>
    <p style="margin-top:.9rem"><a class="doc-link" href="#/reports">Resources (CSV): go to Reports, Downloads section</a></p>`;
  }

  // ---------- ai command center ----------
  async function viewAICommand() {
    const list = await api("/api/ai/resources");
    const selected = list.items.some((x) => x.resource_id === state.aiResource) ? state.aiResource : null;
    const picks = list.items.map((x) => `<button type="button" class="ai-pick" data-action="ai-select" data-rid="${esc(x.resource_id)}" aria-pressed="${x.resource_id === selected}">`
      + `<strong>${esc(x.resource_name)}</strong><span class="muted">${esc(x.environment)} · ${num(x.open_count)} open · ${esc(x.top_title)}</span></button>`).join("");
    let detail = '<p class="muted">Select a recommended resource to see why it was suggested.</p>';
    if (selected) {
      const e = await api("/api/ai/explain?resource=" + encodeURIComponent(selected));
      detail = e.explanations.map((x, i) => `<div class="ai-expl"><h3>${i + 1}. ${esc(x.title)}</h3>`
        + `<ol class="ai-lines">${x.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ol></div>`).join("");
    }
    return `
    <header class="page-head"><div><h1>AI Command Center</h1></div></header>
    <p class="sub">The AI Command Center explains each recommendation in plain language, using the CPU, memory and working-hours evidence behind it. Explanations follow fixed rules; no external AI model is called.</p>
    <section class="grid2" style="margin-top:.9rem">
      <article class="panel"><h2>Recommended resources</h2>
        <p class="hint">${num(list.count)} resources have an open recommendation.</p>
        <div class="ai-list">${picks || emptyState("No open recommendations.")}</div></article>
      <article class="panel"><h2>Why this was recommended</h2>${detail}</article>
    </section>`;
  }

  async function viewSettings() {
    const ov = await api("/api/overview");
    const k = ov.kpis;
    const steps = [
      ["Collect", "Inventory, utilization and cost. Demo data now; read-only cloud APIs are planned."],
      ["Analyze", "Rules find idle, oversized and orphaned resources."],
      ["Recommend", "Savings, confidence, risk and evidence for each resource."],
      ["Simulate", "The simulator tests the plan without touching any resource."],
      ["Approve and verify", "People approve. Savings stay projected until an item is accepted."],
    ];
    const models = [
      ["Idle detection", "Rule", "95th-percentile CPU below 3% over the window", "Stop test (compute only; storage and IP stay billed)"],
      ["Rightsizing", "Rule", "At least 14 days of history; p95 CPU 20% or less, p99 CPU 40% or less, and the peak and memory guards not exceeded", "One size down, sized to 70% p99 CPU"],
      ["Off-hours scheduling", "Rule and formula", "Development and Staging only; after-hours p95 CPU below 10%; savings = hourly rate × 730 × (1 − scheduled hours ÷ 168)", "Schedule recommendation"],
      ["Orphan finder", "Rule", "Volume detached for 14+ days; public IP unattached for 7+ days", "Review for snapshot or release. Never removed automatically"],
      ["Confidence and risk", "Formula", "Starts at 100; minus penalties for missing data, variability, thin headroom and Production. Production is High risk", "Confidence score and risk level"],
      ["Savings de-duplication", "Rule", "Each resource is priced once, at its combined after-state", "No double counting in plan totals"],
      ["Cost anomaly", "Statistic", "Daily environment cost vs the previous 14 days; z-score 3 or more and change 20% or more", "Anomaly with top contributors"],
      ["Health score", "Planned", "Not in this build", "Not available"],
      ["30-day forecast", "Planned", "Not in this build", "Not available"],
      ["Carbon estimate", "Planned", "Not in this build", "Not available"],
    ];
    const guide = [
      ["Executive Overview", "Bill, savings, trend and utilization at a glance"],
      ["Resource Explorer", "Filterable inventory with percentiles and sorting"],
      ["Optimization Recommendations", "Open, accepted, rejected and all items, with evidence"],
      ["Cost Simulator", "Projected bills for the accepted and open plans"],
      ["Alerts and Insights", "Alerts, acknowledgements and cost anomalies"],
      ["Reports", "CSV exports and the management summary"],
      ["Settings", "This page: overview, models, safety rules and demo controls"],
    ];
    return `
    <div class="settings-page">
      <h2>Settings</h2>

      <section class="sx-card"><h3>Project overview</h3>
        <p><strong>Problem.</strong> Cloud bills grow because teams pay for idle VMs, oversized servers, unattached volumes and unused IPs.</p>
        <p><strong>Solution.</strong> UTHO SmartCloud Optimizer finds that waste, explains it with evidence, rates the safety of each fix, simulates the result, and only then asks a person to approve.</p>
        <p><strong>Right now.</strong> The demo bill is <strong>${inr(k.current_monthly)}</strong> per month, with <strong>${inr(k.potential_savings_monthly)}</strong> (${esc(String(k.optimization_pct))}%) addressable through ${num(k.open_recommendations)} open recommendations. These figures come from the same data as the Overview. All data is synthetic.</p>
      </section>

      <section class="sx-card"><h3>How it works</h3>
        <div class="sx-steps">${steps.map(([t, d], i) => `<div class="sx-step"><small>Step ${i + 1}</small><strong>${esc(t)}</strong><span class="muted">${esc(d)}</span></div>`).join("")}</div>
      </section>

      <section class="sx-card"><h3>Models inside UTHO</h3>
        <div class="sx-scroll"><table><thead><tr><th>Model</th><th>Type</th><th>Input and rule</th><th>Output</th></tr></thead>
          <tbody>${models.map(([m, t, i, o]) => `<tr><td><strong>${esc(m)}</strong></td><td>${esc(t)}</td><td>${esc(i)}</td><td>${esc(o)}</td></tr>`).join("")}</tbody></table></div>
        <p class="muted sx-note">Honest label: these are transparent rules and formulas, not machine learning. ML forecasting and an LLM assistant are planned.</p>
      </section>

      <div class="sx-two">
        <section class="sx-card"><h3>Page guide</h3>
          <ul class="sx-list">${guide.map(([p, d]) => `<li><strong>${esc(p)}</strong>: <span class="muted">${esc(d)}</span></li>`).join("")}</ul>
        </section>
      </div>

      <section class="sx-card"><h3>Safety rules</h3>
        <ul class="sx-list">
          <li>No recommendation changes a cloud resource. Accepting one only adds it to the simulated plan.</li>
          <li>Production resources are never scheduled off-hours, and rightsizing is blocked by peak-load guards.</li>
          <li>Stopping a VM does not stop its storage or public IP, so that cost stays in the estimates.</li>
          <li>This demo uses local synthetic data and never calls a cloud provider. Prices are illustrative INR inputs, not cloud quotes.</li>
        </ul>
        <div class="sx-actions">
          <button type="button" class="sx-btn primary" data-action="reset-demo">Reset demo state</button>
        </div>
        <p class="muted sx-note">Reset demo state reloads the seeded dataset and clears all decisions, acknowledgements and settings changes.</p>
      </section>
    </div>`;
  }

  // ---------- actions ----------
  const BUSY_ACTIONS = ["run-analysis", "load-demo", "reset-demo", "decide"];
  async function act(name, el) {
    if (BUSY_ACTIONS.includes(name)) {
      if (state.busy) return;
      state.busy = true;
      if (el) el.disabled = true;
      try { await actInner(name, el); } finally { state.busy = false; if (el) el.disabled = false; }
    } else {
      await actInner(name, el);
    }
  }

  async function actInner(name, el) {
    try {
      if (name === "clear-filters") {
        state.filters = { region: "", team: "", environment: "", type: "", start: "", end: "" };
        return rerender();
      }
      if (name === "sort") {
        const key = el.dataset.key;
        if (!["environment", "region", "team", "type"].includes(key)) return;
        if ((state.sort || "monthly_inr") === key) state.dir = (state.dir || "desc") === "asc" ? "desc" : "asc";
        else { state.sort = key; state.dir = "asc"; }
        return rerender();
      }
      if (name === "rec-tab") { state.recTab = el.dataset.tab; return rerender(); }
      if (name === "ai-select") {
        state.aiResource = el.dataset.rid;
        return rerender();
      }
      if (name === "decide") {
        const notes = ($("#rec-notes") || {}).value || "";
        await api(`/api/recommendations/${encodeURIComponent(el.dataset.rec)}/decision`, { method: "POST", body: { status: el.dataset.status, notes } });
        toast("Decision saved: " + el.dataset.status);
        return rerender();
      }
      if (name === "ack") {
        const note = (document.querySelector(`[data-ack-note="${CSS.escape(el.dataset.alert)}"]`) || {}).value || "";
        await api(`/api/alerts/${encodeURIComponent(el.dataset.alert)}/ack`, { method: "POST", body: { note } });
        toast("Alert acknowledged");
        return rerender();
      }
      if (name === "reset-sim") {
        if (!confirm("Remove every accepted item from the simulated plan? Rejected items stay in the history.")) return;
        const r = await api("/api/simulate/reset", { method: "POST" });
        toast(`Simulation reset: ${r.reset_count} item(s) removed from the plan`);
        return rerender();
      }
      if (name === "run-analysis") {
        const r = await api("/api/analysis/run", { method: "POST" });
        toast(`Analysis complete in ${r.duration_ms} ms: ${r.recommendations} recommendations, ${r.alerts} alerts`);
        return rerender();
      }
      if (name === "load-demo") {
        if (!confirm("Load Demo Scenario? This regenerates the seeded synthetic data and clears all decisions, acknowledgements and settings.")) return;
        const r = await api("/api/demo/load", { method: "POST" });
        state.filters = { region: "", team: "", environment: "", type: "", start: "", end: "" };
        toast(`Demo loaded (seed ${r.summary.seed}): ${r.recommendations} recommendations`);
        location.hash = "#/overview";
        return rerender();
      }
      if (name === "reset-demo") {
        if (!confirm("Reset the demo to its starting state? All decisions, acknowledgements and settings changes are cleared.")) return;
        const r = await api("/api/demo/load", { method: "POST", body: {} });
        toast("Demo reset");
        return rerender();
      }
    } catch (err) {
      toast(err.message, "err");
    }
  }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (btn) { e.preventDefault(); act(btn.dataset.action, btn); return; }
    const row = e.target.closest("[data-open]");
    if (row && !e.target.closest("a,button,input,select")) location.hash = row.dataset.open;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches("[data-open]")) location.hash = e.target.dataset.open;
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.filter) {
      state.filters[t.dataset.filter] = t.value;
      return rerender();
    }
    if (t.dataset.recFilter) {
      state[t.dataset.recFilter] = t.value;
      return rerender();
    }
    if (t.dataset.alertFilter) {
      state[t.dataset.alertFilter] = t.value;
      return rerender();
    }
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "resource-search") {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(() => { state.search = e.target.value; rerender(); }, 350);
    }
    if (e.target.dataset.recFilter === "recSearch") {
      clearTimeout(state.recTimer);
      state.recTimer = setTimeout(() => { state.recSearch = e.target.value; rerender(); }, 350);
    }
  });

  window.addEventListener("hashchange", () => {
    state.recTab = state.recTab && location.hash.startsWith("#/recommendations") ? state.recTab : state.recTab;
    rerender();
  });

  async function boot() {
    await window.UTHO_AUTH_READY;
    try {
      state.meta = await api("/api/meta");
    } catch (err) {
      $("#view").innerHTML = `<div class="error">Could not reach the UTHO server: ${esc(err.message)}</div>`;
      return;
    }
    if (!location.hash) location.hash = "#/overview";
    route();
  }
  boot();
})();
