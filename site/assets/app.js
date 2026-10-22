// Renders the project page from data/results.json, which `pmcal analyze` writes.
// Every number on the page comes from that file; nothing here is hard-coded.

const HORIZON_LABELS = { "30d": "30 days", "7d": "7 days", "1d": "1 day", "1h": "1 hour" };
const state = { platform: "Kalshi", horizon: "1d", view: "curve", forest: "Kalshi" };
let data = null;

// ---------- formatting ----------
const fmtInt = (v) => Math.round(v).toLocaleString("en-US");
const fmtPct = (v, digits = 1) => `${(v * 100).toFixed(digits)}%`;
const fmtSigned = (v, digits = 1) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(digits)}%`;
const fmtPp = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pp`;
const fmtCents = (v) => `${+(v * 100).toFixed(1)}¢`;
const fmtB = (v) => v.toFixed(2);
const fmtP = (v) => (v == null ? "–" : v < 0.001 ? "<0.001" : v.toFixed(3));
const bucket = (r) => `${+(r.bin_low * 100).toFixed(0)}–${+(r.bin_high * 100).toFixed(0)}¢`;
const monthYear = (iso) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function theme() {
  return {
    ink: token("--ink"),
    ink2: token("--ink-2"),
    muted: token("--muted"),
    grid: token("--grid"),
    axis: token("--axis"),
    surface: token("--surface"),
    raised: token("--surface-raised"),
    Kalshi: token("--kalshi"),
    Polymarket: token("--polymarket"),
    yes: token("--yes"),
    no: token("--no"),
  };
}

function baseStyle(t) {
  return {
    background: "transparent",
    color: t.ink2,
    fontFamily: getComputedStyle(document.body).fontFamily,
    fontSize: "12.5px",
    overflow: "visible",
  };
}

function width(el) {
  return Math.max(280, Math.floor(el.clientWidth));
}

function setField(name, text) {
  document.querySelectorAll(`[data-field="${name}"]`).forEach((el) => (el.textContent = text));
}

function table(headers, rows) {
  const head = `<tr>${headers.map((h) => `<th scope="col">${h}</th>`).join("")}</tr>`;
  const body = rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("");
  return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

// ---------- static text ----------
function fillText() {
  const k = data.headline.kalshi;
  const p = data.headline.polymarket;
  const cheap = data.headline.cheapest_bucket;
  setField("total-markets", fmtInt(data.totals.markets));
  setField("total-observations", fmtInt(data.totals.observations));
  setField("kalshi-slope", fmtB(k.b));
  setField("poly-slope", fmtB(p.b));
  setField("longshot-return", fmtSigned(cheap.yes_return, 0));
  setField("longshot-bucket", `under ${+(cheap.bin_high * 100).toFixed(0)}¢`);
  setField("kalshi-mid", fmtB(k.b_mid));
  setField("kalshi-mid-ci", `(95% CI ${fmtB(k.b_mid_low)}–${fmtB(k.b_mid_high)})`);
  setField("early-share", fmtPct(data.early_close_share, 0));
  const onTime = data.polymarket_on_time.find((r) => r.horizon === data.headline_horizon);
  if (onTime) setField("ontime-slope", fmtB(onTime.b));
  setField("carry-30", `${(data.carry_distortion["30"] * 100).toFixed(2)}¢`);
  setField("generated", data.generated);
  setField("generated-month", monthYear(data.generated));
  setField("date-range", `${monthYear(data.totals.first_close)} – ${monthYear(data.totals.last_close)}`);

  document.getElementById("data-table").innerHTML = table(
    ["Platform", "Markets", "Events", "Price observations", "Markets closing"],
    data.platforms.map((r) => [
      r.platform,
      fmtInt(r.markets),
      fmtInt(r.events),
      fmtInt(r.observations),
      `${monthYear(r.first_close)} – ${monthYear(r.last_close)}`,
    ])
  );
}

// ---------- 1. calibration ----------
function renderCalibration(t) {
  const el = document.getElementById("calibration-chart");
  const rows = data.reliability.filter((r) => r.platform === state.platform && r.horizon === state.horizon);
  const summary = data.summary.find((r) => r.platform === state.platform && r.horizon === state.horizon);
  const color = t[state.platform];
  const w = width(el);
  const isGap = state.view === "gap";

  document.getElementById("calibration-caption").textContent =
    `${state.platform}, ${HORIZON_LABELS[state.horizon]} before close` +
    (isGap ? ": resolved Yes minus price" : ": resolved Yes vs price");
  const stat = (label, value, extra = "") =>
    `<div><dt>${label}</dt><dd>${value}${extra ? `<span>${extra}</span>` : ""}</dd></div>`;
  document.getElementById("calibration-stats").innerHTML =
    stat("Price observations", fmtInt(summary.n), `${fmtInt(summary.events)} events`) +
    stat("Average gap from the diagonal", fmtPct(summary.ece), "calibration error") +
    stat("Calibration slope", fmtB(summary.b), `95% CI ${fmtB(summary.b_low)}–${fmtB(summary.b_high)}`);
  document.getElementById("calibration-note").textContent = isGap
    ? "Above zero, the outcome happened more often than the price implied. The shaded band is the 95% confidence interval."
    : "Points above the diagonal are underpriced; points below are overpriced. Hover a point for its numbers.";

  const tipTitle = (d) =>
    [
      `${bucket(d)} bucket · ${fmtInt(d.n)} markets`,
      `Average price ${fmtPct(d.mean_price)}`,
      `Resolved Yes ${fmtPct(d.freq)}`,
      `95% CI ${fmtPct(d.ci_low)} to ${fmtPct(d.ci_high)}`,
      `Gap ${fmtPp(d.gap)}`,
    ].join("\n");

  let chart;
  if (!isGap) {
    const size = Math.min(w, 560);
    chart = Plot.plot({
      width: size,
      height: size,
      marginLeft: 48,
      marginBottom: 42,
      style: baseStyle(t),
      x: { domain: [0, 1], label: "Market price →", tickFormat: (d) => `${Math.round(d * 100)}¢`, ticks: 5 },
      y: { domain: [0, 1], label: "↑ Resolved Yes", tickFormat: (d) => `${Math.round(d * 100)}%`, ticks: 5, grid: true },
      marks: [
        Plot.gridX({ stroke: t.grid, strokeOpacity: 1, ticks: 5 }),
        Plot.line([[0, 0], [1, 1]], { stroke: t.muted, strokeDasharray: "4,4" }),
        Plot.ruleX(rows, { x: "mean_price", y1: "ci_low", y2: "ci_high", stroke: color, strokeWidth: 2, strokeOpacity: 0.55 }),
        Plot.dot(rows, { x: "mean_price", y: "freq", fill: color, r: 4.5, stroke: t.surface, strokeWidth: 1.5 }),
        Plot.tip(rows, Plot.pointer({ x: "mean_price", y: "freq", title: tipTitle, fill: t.raised, stroke: t.axis })),
      ],
    });
  } else {
    const extent = Math.max(0.05, ...rows.map((r) => Math.max(Math.abs(r.ci_low - r.mean_price), Math.abs(r.ci_high - r.mean_price))));
    const lim = Math.min(0.2, Math.ceil(extent * 50) / 50);
    chart = Plot.plot({
      width: w,
      height: Math.min(420, Math.max(300, w * 0.5)),
      marginLeft: 56,
      marginBottom: 42,
      style: baseStyle(t),
      x: { domain: [0, 1], label: "Market price →", tickFormat: (d) => `${Math.round(d * 100)}¢`, ticks: 5 },
      y: { domain: [-lim, lim], label: "↑ More often than priced", tickFormat: (d) => (Math.abs(d) < 1e-9 ? "0" : fmtPp(d).replace(".0", "")), grid: true, ticks: 5 },
      marks: [
        Plot.ruleY([0], { stroke: t.muted }),
        Plot.areaY(rows, {
          x: "mean_price",
          y1: (d) => d.ci_low - d.mean_price,
          y2: (d) => d.ci_high - d.mean_price,
          fill: color,
          fillOpacity: 0.16,
          curve: "monotone-x",
        }),
        Plot.line(rows, { x: "mean_price", y: "gap", stroke: color, strokeWidth: 2, curve: "monotone-x" }),
        Plot.dot(rows, { x: "mean_price", y: "gap", fill: color, r: 4, stroke: t.surface, strokeWidth: 1.5 }),
        Plot.tip(rows, Plot.pointer({ x: "mean_price", y: "gap", title: tipTitle, fill: t.raised, stroke: t.axis })),
      ],
    });
  }
  el.replaceChildren(chart);

  document.getElementById("calibration-table").innerHTML = table(
    ["Price bucket", "Markets", "Avg price", "Resolved Yes", "95% CI", "Gap"],
    rows.map((r) => [bucket(r), fmtInt(r.n), fmtPct(r.mean_price), fmtPct(r.freq), `${fmtPct(r.ci_low)} – ${fmtPct(r.ci_high)}`, fmtPp(r.gap)])
  );
}

// ---------- 2. slope by horizon ----------
function renderSlopes(t) {
  const el = document.getElementById("slope-chart");
  const horizons = data.horizons;
  const offsets = { "Kalshi|all": -0.27, "Kalshi|mid": -0.09, "Polymarket|all": 0.09, "Polymarket|mid": 0.27 };
  const points = [];
  for (const r of data.summary) {
    const i = horizons.indexOf(r.horizon);
    points.push({ ...r, sample: "all", x: i + offsets[`${r.platform}|all`], b: r.b, lo: r.b_low, hi: r.b_high });
    if (r.b_mid != null)
      points.push({ ...r, sample: "mid", x: i + offsets[`${r.platform}|mid`], b: r.b_mid, lo: r.b_mid_low, hi: r.b_mid_high });
  }
  const lo = Math.min(0.85, ...points.map((p) => p.lo));
  const hi = Math.max(1.15, ...points.map((p) => p.hi));
  const w = width(el);

  const chart = Plot.plot({
    width: w,
    height: 340,
    marginLeft: 48,
    marginBottom: 40,
    style: baseStyle(t),
    x: {
      domain: [-0.6, horizons.length - 0.4],
      ticks: horizons.map((_, i) => i),
      tickFormat: (i) => `${HORIZON_LABELS[horizons[i]]} before`,
      label: null,
    },
    y: { domain: [lo - 0.02, hi + 0.02], label: "↑ Calibration slope b", grid: true, ticks: 6 },
    marks: [
      Plot.ruleY([1], { stroke: t.muted, strokeDasharray: "4,4" }),
      Plot.text([1], { x: horizons.length - 0.45, y: 1, text: () => "perfect = 1", textAnchor: "end", dy: -8, fill: t.muted, fontSize: 11.5 }),
      Plot.ruleX(points, { x: "x", y1: "lo", y2: "hi", stroke: (d) => t[d.platform], strokeWidth: 2 }),
      Plot.dot(points, {
        x: "x",
        y: "b",
        r: 5,
        stroke: (d) => t[d.platform],
        strokeWidth: 2,
        fill: (d) => (d.sample === "all" ? t[d.platform] : t.surface),
      }),
      Plot.tip(
        points,
        Plot.pointer({
          x: "x",
          y: "b",
          fill: t.raised,
          stroke: t.axis,
          title: (d) =>
            [
              `${d.platform}, ${HORIZON_LABELS[d.horizon]} before close`,
              d.sample === "all" ? "All prices" : "Prices 5–95¢ only",
              `Slope ${fmtB(d.b)} (95% CI ${fmtB(d.lo)}–${fmtB(d.hi)})`,
            ].join("\n"),
        })
      ),
    ],
  });
  el.replaceChildren(chart);

  document.getElementById("slope-legend").innerHTML = ["Kalshi", "Polymarket"]
    .map((p) => `<span class="legend-item"><span class="swatch" style="background:${t[p]}"></span>${p}, all prices</span>
      <span class="legend-item" style="color:${t[p]}"><span class="swatch hollow"></span><span style="color:var(--ink-2)">${p}, 5–95¢ only</span></span>`)
    .join("");

  document.getElementById("slope-table").innerHTML = table(
    ["Platform", "Before close", "Slope (all prices)", "95% CI", "Slope (5–95¢)", "95% CI"],
    data.summary.map((r) => [
      r.platform,
      HORIZON_LABELS[r.horizon],
      fmtB(r.b),
      `${fmtB(r.b_low)}–${fmtB(r.b_high)}`,
      fmtB(r.b_mid),
      `${fmtB(r.b_mid_low)}–${fmtB(r.b_mid_high)}`,
    ])
  );
}

// ---------- 3. forest by volume and category ----------
function renderForest(t) {
  const el = document.getElementById("forest-chart");
  const rows = data.slopes
    .filter((r) => r.platform === state.forest && r.group !== "Horizon")
    .map((r) => ({ ...r, label: r.group === "Volume" ? `${r.level} volume` : r.level }));
  const order = rows.map((r) => r.label);
  const color = t[state.forest];
  const lo = Math.min(0.75, ...rows.map((r) => r.b_low));
  const hi = Math.max(1.25, ...rows.map((r) => r.b_high));
  const w = width(el);
  const narrow = w < 520;

  document.getElementById("forest-caption").textContent =
    `${state.forest}: calibration slope by volume and category, 1 day before close`;

  const chart = Plot.plot({
    width: w,
    height: 34 * rows.length + 60,
    marginLeft: narrow ? 128 : 170,
    marginRight: 16,
    marginBottom: 40,
    style: baseStyle(t),
    x: { domain: [lo - 0.02, hi + 0.02], label: "Calibration slope b →", grid: true, ticks: narrow ? 4 : 7 },
    y: { domain: order, label: null, tickSize: 0 },
    marks: [
      Plot.ruleX([1], { stroke: t.muted, strokeDasharray: "4,4" }),
      Plot.ruleY(rows, { y: "label", x1: "b_low", x2: "b_high", stroke: color, strokeWidth: 2 }),
      Plot.dot(rows, { y: "label", x: "b", r: 5, fill: color, stroke: t.surface, strokeWidth: 1.5 }),
      Plot.tip(
        rows,
        Plot.pointer({
          y: "label",
          x: "b",
          fill: t.raised,
          stroke: t.axis,
          title: (d) =>
            [`${d.label} · ${fmtInt(d.events)} events`, `Slope ${fmtB(d.b)} (95% CI ${fmtB(d.b_low)}–${fmtB(d.b_high)})`, `p(b = 1) ${fmtP(d.p_b1)}`].join("\n"),
        })
      ),
    ],
  });
  el.replaceChildren(chart);

  document.getElementById("forest-table").innerHTML = table(
    ["Group", "Events", "Slope", "95% CI", "p (b = 1)"],
    rows.map((r) => [r.label, fmtInt(r.events), fmtB(r.b), `${fmtB(r.b_low)}–${fmtB(r.b_high)}`, fmtP(r.p_b1)])
  );
}

// ---------- 4. returns ----------
function renderReturns(t) {
  const el = document.getElementById("returns-chart");
  const sides = [
    { key: "yes", label: "Buy Yes", color: t.yes },
    { key: "no", label: "Buy No", color: t.no },
  ];
  const rows = data.returns.flatMap((r) =>
    sides.map((s) => ({
      bucket: bucket(r),
      side: s.label,
      ret: r[`${s.key}_return`],
      lo: r[`${s.key}_ci_low`],
      hi: r[`${s.key}_ci_high`],
      n: r.n,
    }))
  );
  const order = data.returns.map(bucket);
  const w = width(el);
  const narrow = w < 620;
  // Phones: vertical labels without the cent sign, which moves to the axis title.
  const tickLabel = (b) => (narrow ? b.replace("¢", "") : b);

  const chart = Plot.plot({
    width: w,
    height: 380,
    marginLeft: 52,
    marginBottom: narrow ? 78 : 48,
    style: baseStyle(t),
    fx: {
      domain: order,
      label: narrow ? "Yes price, ¢ →" : "Yes price (midpoint) →",
      tickRotate: narrow ? -90 : 0,
      labelOffset: narrow ? 72 : 40,
      tickFormat: tickLabel,
      padding: 0.18,
    },
    x: { domain: sides.map((s) => s.label), axis: null, padding: 0.08 },
    y: { label: "↑ Return per $1", tickFormat: (d) => `${d > 0 ? "+" : ""}${Math.round(d * 100)}%`, grid: true, ticks: 6 },
    color: { domain: sides.map((s) => s.label), range: sides.map((s) => s.color) },
    marks: [
      Plot.ruleY([0], { stroke: t.muted }),
      Plot.barY(rows, { fx: "bucket", x: "side", y: "ret", fill: "side", rx: 2 }),
      Plot.ruleX(rows, { fx: "bucket", x: "side", y1: "lo", y2: "hi", stroke: t.ink2, strokeWidth: 1.2 }),
      Plot.tip(
        rows,
        Plot.pointerX({
          fx: "bucket",
          x: "side",
          y: "ret",
          fill: t.raised,
          stroke: t.axis,
          title: (d) =>
            [`${d.side} when Yes is ${d.bucket} · ${fmtInt(d.n)} markets`, `Average return ${fmtSigned(d.ret)}`, `95% CI ${fmtSigned(d.lo)} to ${fmtSigned(d.hi)}`].join("\n"),
        })
      ),
    ],
  });
  el.replaceChildren(chart);

  document.getElementById("returns-legend").innerHTML = sides
    .map((s) => `<span class="legend-item"><span class="swatch bar" style="background:${s.color}"></span>${s.label}</span>`)
    .join("") + `<span class="legend-item muted">Lines: 95% CI</span>`;

  document.getElementById("returns-table").innerHTML = table(
    ["Yes price", "Markets", "Buy Yes", "95% CI", "Buy No", "95% CI"],
    data.returns.map((r) => [
      bucket(r),
      fmtInt(r.n),
      fmtSigned(r.yes_return),
      `${fmtSigned(r.yes_ci_low)} to ${fmtSigned(r.yes_ci_high)}`,
      fmtSigned(r.no_return),
      `${fmtSigned(r.no_ci_low)} to ${fmtSigned(r.no_ci_high)}`,
    ])
  );
}

// ---------- wiring ----------
function renderCharts() {
  if (!data || typeof Plot === "undefined") return;
  const t = theme();
  renderCalibration(t);
  renderSlopes(t);
  renderForest(t);
  renderReturns(t);
}

function wireControls() {
  document.querySelectorAll(".segmented").forEach((group) => {
    const key = group.dataset.control;
    group.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      state[key] = button.dataset.value;
      group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      const t = theme();
      if (key === "forest") renderForest(t);
      else renderCalibration(t);
    });
  });

  document.querySelector(".theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try {
      localStorage.setItem("theme", root.dataset.theme);
    } catch (e) {}
    renderCharts();
  });

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderCharts);

  let resizeTimer;
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderCharts, 150);
  });
}

async function main() {
  wireControls();
  try {
    const response = await fetch("data/results.json", { cache: "no-cache" });
    data = await response.json();
  } catch (error) {
    document.querySelectorAll(".chart").forEach((el) => {
      el.textContent = "Couldn't load the results. They're also in the README on GitHub.";
    });
    return;
  }
  fillText();
  renderCharts();
}

main();
