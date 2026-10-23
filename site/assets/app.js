// Renders the project page from data/results.json, which `pmcal analyze` writes.
// Every number on the page comes from that file; nothing below is hard-coded.

const HORIZONS = ["30d", "7d", "1d", "1h"];
const HORIZON_LABEL = { "30d": "30 days", "7d": "7 days", "1d": "1 day", "1h": "1 hour" };
const PLATFORMS = ["Kalshi", "Polymarket"];
const state = { h: 2, view: "curve", timer: null };
let data = null;

// ---------- formatting ----------
const fmtInt = (v) => Math.round(v).toLocaleString("en-US");
const fmtPct = (v, d = 1) => `${(v * 100).toFixed(d)}%`;
const fmtSigned = (v, d = 1) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(d)}%`;
const fmtPts = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts`;
const fmtB = (v) => v.toFixed(2);
const fmtP = (v) => (v == null ? "–" : v < 0.001 ? "<0.001" : v.toFixed(3));
const fmtCents = (v) => `${+(v * 100).toFixed(1)}¢`;
const bucket = (r) => `${+(r.bin_low * 100).toFixed(0)}–${+(r.bin_high * 100).toFixed(0)}¢`;
const monthYear = (iso, long = false) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: long ? "long" : "short", year: "numeric", timeZone: "UTC" });

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const colors = () => ({
  Kalshi: css("--kalshi"),
  Polymarket: css("--poly"),
  text: css("--text"),
  heading: css("--heading"),
  muted: css("--muted"),
  faint: css("--faint"),
  grid: css("--grid"),
  axis: css("--axis"),
  bg: css("--bg"),
  gold: css("--gold"),
  neutral: "#57534d",
});
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const duration = () => (reduceMotion() ? 0 : 650);

function setField(name, text) {
  document.querySelectorAll(`[data-field="${name}"]`).forEach((el) => (el.textContent = text));
}

function table(headers, rows) {
  const head = `<tr>${headers.map((h) => `<th scope="col">${h}</th>`).join("")}</tr>`;
  const body = rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("");
  return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

// ---------- tooltip ----------
const tip = document.getElementById("tooltip");
function showTip(event, title, lines) {
  tip.innerHTML = `<b>${title}</b>${lines.join("<br>")}`;
  tip.hidden = false;
  const pad = 14;
  const { innerWidth: vw, innerHeight: vh } = window;
  const rect = tip.getBoundingClientRect();
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + rect.width > vw - 8) x = event.clientX - rect.width - pad;
  if (y + rect.height > vh - 8) y = event.clientY - rect.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
function hideTip() {
  tip.hidden = true;
}

function axisStyle(g, c) {
  g.selectAll(".domain").remove();
  g.selectAll(".tick line").attr("stroke", c.grid);
  g.selectAll(".tick text").attr("fill", c.faint).attr("font-size", 11);
}

// ---------- static text ----------
function fillText() {
  const k = data.headline.kalshi;
  const cheap = data.headline.cheapest_bucket;
  setField("total-markets", fmtInt(data.totals.markets));
  setField("longshot-return", fmtSigned(cheap.yes_return, 0));
  setField("longshot-bucket", `under ${+(cheap.bin_high * 100).toFixed(0)}¢`);
  setField("kalshi-mid", fmtB(k.b_mid));
  setField("kalshi-mid-ci", `(95% CI ${fmtB(k.b_mid_low)}–${fmtB(k.b_mid_high)})`);
  setField("early-share", fmtPct(data.early_close_share, 0));
  const onTime = data.polymarket_on_time.find((r) => r.horizon === data.headline_horizon);
  if (onTime) setField("ontime-slope", fmtB(onTime.b));
  setField("carry-30", `${(data.carry_distortion["30"] * 100).toFixed(2)}¢`);
  setField("generated", data.generated);
  setField("generated-month", monthYear(data.generated, true).toLowerCase());
  setField("date-range", `${monthYear(data.totals.first_close)} – ${monthYear(data.totals.last_close)}`);

  // A concrete example in the prose: the cheapest and dearest Kalshi buckets a day out.
  const rows = data.reliability.filter((r) => r.platform === "Kalshi" && r.horizon === data.headline_horizon);
  const low = rows[0];
  const high = rows[rows.length - 1];
  document.getElementById("example-line").innerHTML =
    `On Kalshi a day before close, contracts priced at <span class="num">${fmtCents(low.mean_price)}</span> on average ` +
    `paid out <span class="num">${fmtPct(low.freq)}</span> of the time, and those at ` +
    `<span class="num">${fmtCents(high.mean_price)}</span> paid out <span class="num">${fmtPct(high.freq)}</span>.`;

  document.getElementById("data-table").innerHTML = table(
    ["Platform", "Markets", "Events", "Prices", "Markets closing"],
    data.platforms.map((r) => [
      r.platform,
      fmtInt(r.markets),
      fmtInt(r.events),
      fmtInt(r.observations),
      `${monthYear(r.first_close)} – ${monthYear(r.last_close)}`,
    ])
  );
}

// ---------- fig. 1: calibration, animated across horizons ----------
const hero = {};

function heroRows(platform, h) {
  return data.reliability.filter((r) => r.platform === platform && r.horizon === HORIZONS[h]);
}

function heroGapLimit() {
  const m = d3.max(data.reliability, (r) => Math.max(Math.abs(r.ci_low - r.mean_price), Math.abs(r.ci_high - r.mean_price)));
  return Math.min(0.16, Math.ceil(m * 50) / 50);
}

function buildHero() {
  const c = colors();
  const el = document.getElementById("hero-chart");
  const w = Math.max(300, el.clientWidth);
  const h = Math.min(w, 580);
  const m = { top: 14, right: 14, bottom: 46, left: 50 };
  el.replaceChildren();

  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h).attr("viewBox", `0 0 ${w} ${h}`);
  const x = d3.scaleLinear([0, 1], [m.left, w - m.right]);
  const yCurve = d3.scaleLinear([0, 1], [h - m.bottom, m.top]);
  const lim = heroGapLimit();
  const yGap = d3.scaleLinear([-lim, lim], [h - m.bottom, m.top]);

  const gx = svg.append("g").attr("transform", `translate(0,${h - m.bottom})`);
  gx.call(d3.axisBottom(x).ticks(5).tickFormat((d) => `${Math.round(d * 100)}¢`).tickSize(-(h - m.top - m.bottom)).tickPadding(10));
  axisStyle(gx, c);
  svg.append("text").attr("x", w - m.right).attr("y", h - 6).attr("text-anchor", "end").attr("fill", c.faint).attr("font-size", 11).text("market price →");

  const gy = svg.append("g").attr("transform", `translate(${m.left},0)`);
  const yLabel = svg.append("text").attr("x", m.left).attr("y", m.top - 2).attr("fill", c.faint).attr("font-size", 11).attr("dy", "-0.4em");

  const ref = svg.append("line").attr("stroke", c.muted).attr("stroke-dasharray", "4 5").attr("stroke-width", 1.2);
  const refLabel = svg.append("text").attr("fill", c.muted).attr("font-size", 11);
  const upLabel = svg.append("text").attr("x", m.left + 10).attr("y", m.top + 16).attr("fill", c.faint).attr("font-size", 11);
  const downLabel = svg.append("text").attr("x", m.left + 10).attr("y", h - m.bottom - 10).attr("fill", c.faint).attr("font-size", 11);

  const series = {};
  for (const p of PLATFORMS) {
    const g = svg.append("g");
    series[p] = { whiskers: g.append("g"), dots: g.append("g") };
  }
  const halo = svg.append("circle").attr("r", 9).attr("fill", "none").attr("stroke-width", 2).attr("opacity", 0);

  Object.assign(hero, { svg, x, yCurve, yGap, gy, yLabel, ref, refLabel, upLabel, downLabel, series, halo, m, w, h, c });

  // Nearest-dot hover across both platforms.
  svg.append("rect")
    .attr("x", m.left).attr("y", m.top).attr("width", w - m.left - m.right).attr("height", h - m.top - m.bottom)
    .attr("fill", "transparent")
    .on("pointermove", (event) => {
      const [px, py] = d3.pointer(event);
      let best = null;
      for (const p of PLATFORMS) {
        for (const r of heroRows(p, state.h)) {
          const cy = heroY()(heroValue(r));
          const d = Math.hypot(x(r.mean_price) + DODGE[p] - px, cy - py);
          if (!best || d < best.d) best = { d, r, p, cy };
        }
      }
      if (!best || best.d > 40) {
        hideTip();
        halo.attr("opacity", 0);
        return;
      }
      const r = best.r;
      halo.attr("cx", x(r.mean_price) + DODGE[best.p]).attr("cy", best.cy).attr("stroke", c[best.p]).attr("opacity", 1);
      showTip(event, `${best.p} · ${bucket(r)}`, [
        `${fmtInt(r.n)} markets, ${HORIZON_LABEL[HORIZONS[state.h]]} before close`,
        `average price&nbsp;&nbsp;${fmtPct(r.mean_price)}`,
        `resolved yes&nbsp;&nbsp;&nbsp;${fmtPct(r.freq)}`,
        `95% CI&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;${fmtPct(r.ci_low)}–${fmtPct(r.ci_high)}`,
        `gap&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;${fmtPts(r.gap)}`,
      ]);
    })
    .on("pointerleave", () => {
      hideTip();
      halo.attr("opacity", 0);
    });

  updateHero(true);
}

const heroY = () => (state.view === "curve" ? hero.yCurve : hero.yGap);
const heroValue = (r) => (state.view === "curve" ? r.freq : r.gap);
const heroLo = (r) => (state.view === "curve" ? r.ci_low : r.ci_low - r.mean_price);
const heroHi = (r) => (state.view === "curve" ? r.ci_high : r.ci_high - r.mean_price);

// Nudges the two platforms apart so dots in the same bucket don't sit on top of each other.
const DODGE = { Kalshi: -3.5, Polymarket: 3.5 };

function updateHero(instant = false) {
  const { x, gy, yLabel, ref, refLabel, upLabel, downLabel, series, m, w, h, c } = hero;
  const y = heroY();
  const t = d3.transition().duration(instant ? 0 : duration()).ease(d3.easeCubicInOut);
  const curve = state.view === "curve";

  gy.transition(t).call(
    d3.axisLeft(y).ticks(curve ? 5 : 6).tickSize(-(w - m.left - m.right)).tickPadding(10)
      .tickFormat((d) => (curve ? `${Math.round(d * 100)}%` : Math.abs(d) < 1e-9 ? "0" : `${d > 0 ? "+" : "−"}${Math.round(Math.abs(d) * 100)}`))
  );
  gy.call((g) => axisStyle(g, c));
  yLabel.text(curve ? "↑ resolved yes" : "");

  if (curve) {
    ref.transition(t).attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1));
    // The diagonal is crowded with dots, so its label sits in the empty lower-right corner.
    refLabel.transition(t).attr("x", x(1) - 8).attr("y", y(0) - 12).attr("text-anchor", "end").attr("dy", 0);
    refLabel.text("- - -  perfectly calibrated");
    upLabel.text("");
    downLabel.text("");
  } else {
    ref.transition(t).attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(0));
    refLabel.text("");
    upLabel.text("↑ happened more often than priced, in points");
    downLabel.text("↓ happened less often than priced");
  }

  for (const p of PLATFORMS) {
    const rows = heroRows(p, state.h);
    const key = (r) => r.bin_low;
    series[p].whiskers.selectAll("line").data(rows, key).join(
      (enter) => enter.append("line").attr("stroke", c[p]).attr("stroke-width", 2).attr("stroke-linecap", "round").attr("opacity", 0.5)
        .attr("x1", (r) => x(r.mean_price) + DODGE[p]).attr("x2", (r) => x(r.mean_price) + DODGE[p]).attr("y1", (r) => y(heroValue(r))).attr("y2", (r) => y(heroValue(r))),
      (update) => update,
      (exit) => exit.transition(t).attr("opacity", 0).remove()
    ).transition(t)
      .attr("x1", (r) => x(r.mean_price) + DODGE[p]).attr("x2", (r) => x(r.mean_price) + DODGE[p])
      .attr("y1", (r) => y(heroLo(r))).attr("y2", (r) => y(heroHi(r)));

    series[p].dots.selectAll("circle").data(rows, key).join(
      (enter) => enter.append("circle").attr("r", 5).attr("fill", c[p]).attr("stroke", c.bg).attr("stroke-width", 1.5)
        .attr("cx", (r) => x(r.mean_price) + DODGE[p]).attr("cy", (r) => y(heroValue(r))),
      (update) => update,
      (exit) => exit.transition(t).attr("r", 0).remove()
    ).transition(t)
      .attr("cx", (r) => x(r.mean_price) + DODGE[p]).attr("cy", (r) => y(heroValue(r)));
  }

  updateReadout();
}

function updateReadout() {
  const c = colors();
  const horizon = HORIZONS[state.h];
  document.getElementById("hero-horizon").innerHTML = `${HORIZON_LABEL[horizon]}<small>before close</small>`;
  document.getElementById("horizon-range").value = state.h;
  document.getElementById("hero-readout").innerHTML = PLATFORMS.map((p) => {
    const s = data.summary.find((r) => r.platform === p && r.horizon === horizon);
    return `<div class="readout__platform">
      <dt style="color:${c[p]}">${p.toLowerCase()}</dt>
      <dd>
        <span class="stat">${(s.ece * 100).toFixed(1)}<span class="stat-unit"> pts</span></span>
        <span class="stat">${fmtB(s.b)}</span>
        <span class="stat-label">average distance from the dashed line</span>
        <span class="stat-label">calibration slope (1.00 is perfect)</span>
      </dd>
    </div>`;
  }).join("");

  const rows = PLATFORMS.flatMap((p) => heroRows(p, state.h).map((r) => ({ p, ...r })));
  document.getElementById("hero-table").innerHTML = table(
    ["Platform", "Price bucket", "Markets", "Avg price", "Resolved yes", "95% CI", "Gap"],
    rows.map((r) => [r.p, bucket(r), fmtInt(r.n), fmtPct(r.mean_price), fmtPct(r.freq), `${fmtPct(r.ci_low)}–${fmtPct(r.ci_high)}`, fmtPts(r.gap)])
  );
}

function setHorizon(h) {
  state.h = h;
  updateHero();
}

function togglePlay() {
  const button = document.querySelector(".play");
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
    button.textContent = "play ▸";
    return;
  }
  button.textContent = "pause ❚❚";
  setHorizon(0);
  state.timer = setInterval(() => {
    if (state.h >= HORIZONS.length - 1) {
      togglePlay();
      return;
    }
    setHorizon(state.h + 1);
  }, 1500);
}

// ---------- fig. 2: slope by horizon ----------
function drawSlopes() {
  const c = colors();
  const el = document.getElementById("slope-chart");
  const w = Math.max(300, el.clientWidth);
  const narrow = w < 560;
  const h = narrow ? 340 : 380;
  const m = { top: 24, right: narrow ? 92 : 128, bottom: 40, left: 46 };
  el.replaceChildren();

  const points = [];
  for (const r of data.summary) {
    points.push({ ...r, sample: "all", v: r.b, lo: r.b_low, hi: r.b_high });
    if (r.b_mid != null) points.push({ ...r, sample: "mid", v: r.b_mid, lo: r.b_mid_low, hi: r.b_mid_high });
  }
  const x = d3.scalePoint(HORIZONS, [m.left, w - m.right]).padding(0.45);
  const y = d3.scaleLinear([Math.min(0.85, d3.min(points, (p) => p.lo)) - 0.02, Math.max(1.15, d3.max(points, (p) => p.hi)) + 0.02], [h - m.bottom, m.top]);
  const offset = { "Kalshi|all": -15, "Kalshi|mid": -5, "Polymarket|all": 5, "Polymarket|mid": 15 };
  const px = (d) => x(d.horizon) + offset[`${d.platform}|${d.sample}`] * (narrow ? 0.8 : 1);

  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h).attr("viewBox", `0 0 ${w} ${h}`);
  const gy = svg.append("g").attr("transform", `translate(${m.left},0)`)
    .call(d3.axisLeft(y).ticks(6).tickSize(-(w - m.left - m.right)).tickPadding(10).tickFormat(d3.format(".1f")));
  axisStyle(gy, c);
  const gx = svg.append("g").attr("transform", `translate(0,${h - m.bottom})`)
    .call(d3.axisBottom(x).tickSize(0).tickPadding(14).tickFormat((d) => (narrow ? HORIZON_LABEL[d] : `${HORIZON_LABEL[d]} before`)));
  axisStyle(gx, c);

  svg.append("line").attr("x1", m.left).attr("x2", w - m.right).attr("y1", y(1)).attr("y2", y(1))
    .attr("stroke", c.muted).attr("stroke-dasharray", "4 5");
  // Between the 7-day and 1-day columns, where no markers sit.
  svg.append("text").attr("x", (x("7d") + x("1d")) / 2).attr("y", y(1)).attr("dy", "1.3em").attr("text-anchor", "middle")
    .attr("fill", c.muted).attr("font-size", 11).text("perfectly calibrated");
  svg.append("text").attr("x", m.left + 8).attr("y", m.top).attr("dy", "0.4em").attr("fill", c.faint).attr("font-size", 11).text("↑ longshots overpriced");
  svg.append("text").attr("x", m.left + 8).attr("y", h - m.bottom - 10).attr("fill", c.faint).attr("font-size", 11).text("↓ prices overconfident");

  for (const p of PLATFORMS) {
    const all = points.filter((d) => d.platform === p && d.sample === "all");
    svg.append("path").datum(all).attr("fill", "none").attr("stroke", c[p]).attr("stroke-width", 1.5).attr("opacity", 0.55)
      .attr("d", d3.line().x(px).y((d) => y(d.v)));
    const last = all[all.length - 1];
    const label = svg.append("text").attr("x", w - m.right + 12).attr("y", y(last.v)).attr("fill", c[p])
      .attr("font-size", 12).attr("font-weight", 500);
    if (narrow) {
      // Two lines so the label fits the narrow right margin.
      label.attr("dy", "-0.2em");
      label.append("tspan").text(p.toLowerCase());
      label.append("tspan").attr("x", w - m.right + 12).attr("dy", "1.2em").text(fmtB(last.v));
    } else {
      label.attr("dy", "0.35em").text(`${p.toLowerCase()} ${fmtB(last.v)}`);
    }
  }

  const g = svg.append("g").selectAll("g").data(points).join("g");
  g.append("line").attr("x1", px).attr("x2", px).attr("y1", (d) => y(d.lo)).attr("y2", (d) => y(d.hi))
    .attr("stroke", (d) => c[d.platform]).attr("stroke-width", 2).attr("stroke-linecap", "round").attr("opacity", (d) => (d.sample === "all" ? 0.75 : 0.45));
  g.append("circle").attr("cx", px).attr("cy", (d) => y(d.v)).attr("r", 4.5)
    .attr("fill", (d) => (d.sample === "all" ? c[d.platform] : c.bg)).attr("stroke", (d) => c[d.platform]).attr("stroke-width", 2);
  g.append("circle").attr("cx", px).attr("cy", (d) => y(d.v)).attr("r", 12).attr("fill", "transparent")
    .on("pointermove", (event, d) => showTip(event, `${d.platform} · ${HORIZON_LABEL[d.horizon]} before close`, [
      d.sample === "all" ? "every price" : "prices 5–95¢ only",
      `slope ${fmtB(d.v)} (95% CI ${fmtB(d.lo)}–${fmtB(d.hi)})`,
    ]))
    .on("pointerleave", hideTip);

  document.getElementById("slope-table").innerHTML = table(
    ["Platform", "Before close", "Slope, all prices", "95% CI", "Slope, 5–95¢", "95% CI"],
    data.summary.map((r) => [r.platform, HORIZON_LABEL[r.horizon], fmtB(r.b), `${fmtB(r.b_low)}–${fmtB(r.b_high)}`, fmtB(r.b_mid), `${fmtB(r.b_mid_low)}–${fmtB(r.b_mid_high)}`])
  );
}

// ---------- fig. 3: slope by volume and category ----------
function forestRows(platform) {
  const rows = data.slopes.filter((r) => r.platform === platform);
  const volume = ["Low", "Mid", "High"].map((l) => rows.find((r) => r.group === "Volume" && r.level === l)).filter(Boolean);
  const category = rows.filter((r) => r.group === "Category").sort((a, b) => b.b - a.b);
  return [
    { header: "by volume" },
    ...volume.map((r) => ({ ...r, label: `${r.level.toLowerCase()} volume` })),
    { header: "by category" },
    ...category.map((r) => ({ ...r, label: r.level.toLowerCase().replace(" and ", " & ") })),
  ];
}

function drawForest() {
  const c = colors();
  const all = data.slopes.filter((r) => r.group !== "Horizon");
  const domain = [Math.min(0.75, d3.min(all, (r) => r.b_low)) - 0.02, Math.max(1.25, d3.max(all, (r) => r.b_high)) + 0.02];
  for (const [platform, id] of [["Kalshi", "forest-kalshi"], ["Polymarket", "forest-poly"]]) {
    const el = document.getElementById(id);
    const w = Math.max(280, el.clientWidth);
    const rows = forestRows(platform);
    const rowH = 27;
    const m = { top: 6, right: 12, bottom: 36, left: w < 420 ? 128 : 150 };
    const h = m.top + rows.length * rowH + m.bottom;
    el.replaceChildren();

    const svg = d3.select(el).append("svg").attr("width", w).attr("height", h).attr("viewBox", `0 0 ${w} ${h}`);
    const x = d3.scaleLinear(domain, [m.left, w - m.right]);
    const yOf = (i) => m.top + i * rowH + rowH / 2;
    const gx = svg.append("g").attr("transform", `translate(0,${h - m.bottom + 6})`)
      .call(d3.axisBottom(x).ticks(5).tickSize(-(h - m.bottom - m.top + 6)).tickPadding(10).tickFormat(d3.format(".1f")));
    axisStyle(gx, c);
    svg.append("line").attr("x1", x(1)).attr("x2", x(1)).attr("y1", m.top).attr("y2", h - m.bottom + 6).attr("stroke", c.muted).attr("stroke-dasharray", "4 5");

    rows.forEach((r, i) => {
      const y = yOf(i);
      if (r.header) {
        svg.append("text").attr("x", 0).attr("y", y + 4).attr("fill", c.faint).attr("font-size", 10.5).attr("letter-spacing", "0.04em").text(r.header);
        return;
      }
      const significant = r.b_low > 1 || r.b_high < 1;
      svg.append("text").attr("x", m.left - 12).attr("y", y).attr("dy", "0.35em").attr("text-anchor", "end")
        .attr("fill", significant ? c.text : c.muted).attr("font-size", 12).text(r.label);
      svg.append("line").attr("x1", x(r.b_low)).attr("x2", x(r.b_high)).attr("y1", y).attr("y2", y)
        .attr("stroke", c[platform]).attr("stroke-width", 2).attr("stroke-linecap", "round").attr("opacity", significant ? 0.9 : 0.4);
      svg.append("circle").attr("cx", x(r.b)).attr("cy", y).attr("r", 4.5)
        .attr("fill", significant ? c[platform] : c.bg).attr("stroke", c[platform]).attr("stroke-width", 2);
      svg.append("rect").attr("x", 0).attr("y", y - rowH / 2).attr("width", w).attr("height", rowH).attr("fill", "transparent")
        .on("pointermove", (event) => showTip(event, `${platform} · ${r.label}`, [
          `${fmtInt(r.events)} events`,
          `slope ${fmtB(r.b)} (95% CI ${fmtB(r.b_low)}–${fmtB(r.b_high)})`,
          `p (b = 1) ${fmtP(r.p_b1)}`,
        ]))
        .on("pointerleave", hideTip);
    });
  }

  document.getElementById("forest-table").innerHTML = table(
    ["Platform", "Group", "Events", "Slope", "95% CI", "p (b = 1)"],
    all.map((r) => [r.platform, `${r.group}: ${r.level}`, fmtInt(r.events), fmtB(r.b), `${fmtB(r.b_low)}–${fmtB(r.b_high)}`, fmtP(r.p_b1)])
  );
}

// ---------- fig. 4: returns ----------
function drawReturns() {
  const c = colors();
  const rows = data.returns;
  // Headroom below -100% for the value label under the worst bar.
  const lo = Math.min(-1, d3.min(rows, (r) => Math.min(r.yes_ci_low, r.no_ci_low))) - 0.14;
  const hi = Math.max(0.2, d3.max(rows, (r) => Math.max(r.yes_ci_high, r.no_ci_high)));
  for (const [side, id] of [["yes", "returns-yes"], ["no", "returns-no"]]) {
    const el = document.getElementById(id);
    const w = Math.max(280, el.clientWidth);
    const h = 300;
    const m = { top: 14, right: 6, bottom: 58, left: 46 };
    el.replaceChildren();

    const svg = d3.select(el).append("svg").attr("width", w).attr("height", h).attr("viewBox", `0 0 ${w} ${h}`);
    const x = d3.scaleBand(rows.map(bucket), [m.left, w - m.right]).padding(0.28);
    const y = d3.scaleLinear([lo, hi], [h - m.bottom, m.top]);
    const gy = svg.append("g").attr("transform", `translate(${m.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickSize(-(w - m.left - m.right)).tickPadding(8).tickFormat((d) => `${d > 0 ? "+" : d < 0 ? "−" : ""}${Math.abs(Math.round(d * 100))}%`));
    axisStyle(gy, c);
    const gx = svg.append("g").attr("transform", `translate(0,${h - m.bottom})`)
      .call(d3.axisBottom(x).tickSize(0).tickPadding(8).tickFormat((b) => b.replace("¢", "")));
    axisStyle(gx, c);
    gx.selectAll("text").attr("transform", "rotate(-90)").attr("text-anchor", "end").attr("dx", "-0.6em").attr("dy", "-0.55em").attr("font-size", 10.5);
    svg.append("text").attr("x", w - m.right).attr("y", h - 4).attr("text-anchor", "end").attr("fill", c.faint).attr("font-size", 11).text("yes price, ¢ →");
    svg.append("line").attr("x1", m.left).attr("x2", w - m.right).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", c.muted);

    const val = (r) => r[`${side}_return`];
    const ciLo = (r) => r[`${side}_ci_low`];
    const ciHi = (r) => r[`${side}_ci_high`];
    const sig = (r) => ciHi(r) < 0;
    svg.append("g").selectAll("rect").data(rows).join("rect")
      .attr("x", (r) => x(bucket(r))).attr("width", x.bandwidth())
      .attr("y", (r) => y(Math.max(0, val(r)))).attr("height", (r) => Math.max(1, Math.abs(y(val(r)) - y(0))))
      .attr("rx", 2).attr("fill", (r) => (sig(r) ? c.Kalshi : c.neutral));
    svg.append("g").selectAll("line").data(rows).join("line")
      .attr("x1", (r) => x(bucket(r)) + x.bandwidth() / 2).attr("x2", (r) => x(bucket(r)) + x.bandwidth() / 2)
      .attr("y1", (r) => y(ciLo(r))).attr("y2", (r) => y(ciHi(r))).attr("stroke", c.muted).attr("stroke-width", 1);

    const worst = rows.reduce((a, b) => (val(b) < val(a) ? b : a));
    const left = x(bucket(worst)) < w / 2;
    svg.append("text").attr("x", left ? x(bucket(worst)) : x(bucket(worst)) + x.bandwidth())
      .attr("y", y(Math.min(val(worst), ciLo(worst))) + 15)
      .attr("text-anchor", left ? "start" : "end").attr("fill", c.gold).attr("font-size", 12).attr("font-weight", 500)
      .text(fmtSigned(val(worst), 0));

    svg.append("g").selectAll("rect").data(rows).join("rect")
      .attr("x", (r) => x(bucket(r)) - (x.step() - x.bandwidth()) / 2).attr("width", x.step())
      .attr("y", m.top).attr("height", h - m.top - m.bottom).attr("fill", "transparent")
      .on("pointermove", (event, r) => showTip(event, `Buy ${side} when yes is ${bucket(r)}`, [
        `${fmtInt(r.n)} markets`,
        `average return ${fmtSigned(val(r))}`,
        `95% CI ${fmtSigned(ciLo(r))} to ${fmtSigned(ciHi(r))}`,
      ]))
      .on("pointerleave", hideTip);
  }

  document.getElementById("returns-table").innerHTML = table(
    ["Yes price", "Markets", "Buy yes", "95% CI", "Buy no", "95% CI"],
    rows.map((r) => [bucket(r), fmtInt(r.n), fmtSigned(r.yes_return), `${fmtSigned(r.yes_ci_low)} to ${fmtSigned(r.yes_ci_high)}`, fmtSigned(r.no_return), `${fmtSigned(r.no_ci_low)} to ${fmtSigned(r.no_ci_high)}`])
  );
}

// ---------- wiring ----------
function drawAll() {
  buildHero();
  drawSlopes();
  drawForest();
  drawReturns();
}

function wire() {
  document.getElementById("horizon-range").addEventListener("input", (e) => {
    if (state.timer) togglePlay();
    setHorizon(+e.target.value);
  });
  document.querySelector(".play").addEventListener("click", togglePlay);
  document.querySelectorAll(".toggle button").forEach((b) =>
    b.addEventListener("click", () => {
      state.view = b.dataset.view;
      document.querySelectorAll(".toggle button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
      updateHero();
    })
  );
  let timer;
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    clearTimeout(timer);
    timer = setTimeout(drawAll, 150);
  });
  window.addEventListener("scroll", hideTip, { passive: true });
}

async function main() {
  try {
    data = await (await fetch("data/results.json", { cache: "no-cache" })).json();
  } catch (error) {
    document.querySelectorAll(".chart").forEach((el) => (el.textContent = "Couldn't load the results. They're also in the README on GitHub."));
    return;
  }
  fillText();
  wire();
  drawAll();
}

main();
