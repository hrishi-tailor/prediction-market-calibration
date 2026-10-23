// Renders the project page from data/results.json, which `pmcal analyze` writes.
// Every number on the page comes from that file; nothing below is hard-coded except the example contract.

const HORIZONS = ["30d", "7d", "1d", "1h"];
const HORIZON_LABEL = { "30d": "30 days", "7d": "7 days", "1d": "1 day", "1h": "1 hour" };
const PLATFORMS = ["Kalshi", "Polymarket"];
const state = { h: 2, view: "curve", timer: null, simSide: "yes", simIdx: 0 };
let data = null;

const GLOSSARY = {
  kalshi: ["Kalshi", "A US exchange, regulated by the CFTC, where people trade yes/no contracts on real-world events."],
  polymarket: ["Polymarket", "A crypto-based prediction market where people trade yes/no contracts on real-world events."],
  contract: ["Contract", "A bet that pays $1 if the event happens and $0 if it doesn't. Its price, between 0¢ and 100¢, reads as a probability."],
  calibrated: ["Calibrated", "A forecaster is calibrated when things it calls 30% likely happen about 30% of the time, and so on at every level."],
  longshot: ["Long shot", "An outcome the market thinks is unlikely, so its contract is cheap, say under 10¢."],
  favourite: ["Favourite", "An outcome the market thinks is likely, so its contract is expensive, say over 90¢."],
  ci: ["Confidence interval", "The range the true value plausibly falls in, given how many markets went into each dot. Wider means less certain."],
  slope: ["Calibration slope", "One number summarising how honest the odds are, from a logistic regression of outcomes on prices. 1 is perfect; above 1, long shots are overpriced; below 1, prices are overconfident."],
};

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
  ink: css("--ink"),
  ink2: css("--ink-2"),
  muted: css("--muted"),
  faint: css("--faint"),
  grid: css("--grid"),
  axis: css("--axis"),
  paper: css("--paper"),
  card: css("--card"),
  accent: css("--accent"),
  yes: css("--yes"),
  no: css("--no"),
  neutral: "#cdc3b2",
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

// ---------- tooltip and glossary ----------
const tip = document.getElementById("tooltip");
function placeTip(x, y) {
  const pad = 14;
  const rect = tip.getBoundingClientRect();
  let left = x + pad;
  let top = y + pad;
  if (left + rect.width > window.innerWidth - 8) left = x - rect.width - pad;
  if (top + rect.height > window.innerHeight - 8) top = y - rect.height - pad;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}
function showTip(event, title, lines, term = false) {
  tip.innerHTML = `<b>${title}</b>${lines.join("<br>")}`;
  tip.classList.toggle("tooltip--term", term);
  tip.hidden = false;
  placeTip(event.clientX, event.clientY);
}
function hideTip() {
  tip.hidden = true;
}

function wireGlossary() {
  document.querySelectorAll(".term").forEach((el) => {
    const [title, text] = GLOSSARY[el.dataset.term] || [];
    if (!title) return;
    el.setAttribute("aria-label", `${el.textContent}: ${text}`);
    const show = (event) => {
      const r = el.getBoundingClientRect();
      showTip({ clientX: event?.clientX ?? r.left, clientY: event?.clientY ?? r.bottom }, title, [text], true);
      tip.dataset.owner = el.dataset.term;
    };
    const isOpen = () => !tip.hidden && tip.dataset.owner === el.dataset.term;
    // Mouse users get hover; touch and pen users tap to toggle, since a tap also fires pointerenter.
    el.addEventListener("pointerenter", (e) => e.pointerType === "mouse" && show(e));
    el.addEventListener("pointerleave", (e) => e.pointerType === "mouse" && hideTip());
    // Keyboard focus only; a tap also focuses the term and would otherwise open-then-close it.
    el.addEventListener("focus", () => el.matches(":focus-visible") && show());
    el.addEventListener("blur", hideTip);
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      if (e.pointerType === "mouse") return show(e);
      isOpen() ? hideTip() : show(e);
    });
  });
  document.addEventListener("click", hideTip);
}

function axisStyle(g, c) {
  g.selectAll(".domain").remove();
  g.selectAll(".tick line").attr("stroke", c.grid);
  g.selectAll(".tick text").attr("fill", c.muted).attr("font-size", 11);
}

// ---------- text ----------
function fillText() {
  const k = data.headline.kalshi;
  setField("kalshi-mid", fmtB(k.b_mid));
  setField("kalshi-mid-ci", `(95% CI ${fmtB(k.b_mid_low)}–${fmtB(k.b_mid_high)})`);
  setField("early-share", fmtPct(data.early_close_share, 0));
  const onTime = data.polymarket_on_time.find((r) => r.horizon === data.headline_horizon);
  if (onTime) setField("ontime-slope", fmtB(onTime.b));
  setField("carry-30", `${(data.carry_distortion["30"] * 100).toFixed(2)}¢`);
  setField("generated", data.generated);
  setField("generated-month", monthYear(data.generated, true).toLowerCase());
  setField("date-range", `${monthYear(data.totals.first_close)} – ${monthYear(data.totals.last_close)}`);
  setField("year-span", String(new Date(data.totals.last_close).getUTCFullYear() - new Date(data.totals.first_close).getUTCFullYear()));
  document.querySelectorAll('[data-count="total-markets"]').forEach((el) => (el.dataset.target = data.totals.markets));
  document.querySelectorAll('[data-count="total-observations"]').forEach((el) => (el.dataset.target = data.totals.observations));

  // Time trend, Polymarket mix and the forecast test.
  const kPeriods = data.by_period.filter((r) => r.platform === "Kalshi");
  if (kPeriods.length) setField("kalshi-early-b", fmtB(kPeriods[0].b));
  const pPeriods = data.by_period.filter((r) => r.platform === "Polymarket");
  if (pPeriods.length) {
    const latest = pPeriods[pPeriods.length - 1].period.slice(0, 4);
    const share = (rows) => d3.sum(rows, (r) => r.weather_share * r.n) / d3.sum(rows, (r) => r.n);
    setField("latest-year", latest);
    setField("weather-share", fmtPct(share(pPeriods.filter((r) => r.period.startsWith(latest))), 0));
    setField("weather-share-before", fmtPct(share(pPeriods.filter((r) => !r.period.startsWith(latest))), 0));
  }
  const day = data.forecast.filter((r) => r.horizon === data.headline_horizon);
  if (day.length) {
    setField("oos-test-n", fmtInt(d3.sum(day, (r) => r.test_n)));
    setField("oos-split", new Date(`${day[0].split}T00:00:00Z`).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" }));
  }

  const rows = kalshiDay();
  const low = rows[0];
  const high = rows[rows.length - 1];
  document.getElementById("example-line").innerHTML =
    `On Kalshi a day before close, near-certain contracts priced at <b>${fmtCents(high.mean_price)}</b> paid out ` +
    `<b>${fmtPct(high.freq)}</b> of the time, and long shots at <b>${fmtCents(low.mean_price)}</b> paid out <b>${fmtPct(low.freq)}</b>.`;

  document.getElementById("data-table").innerHTML = table(
    ["Platform", "Markets", "Events", "Prices", "Markets closing"],
    data.platforms.map((r) => [r.platform, fmtInt(r.markets), fmtInt(r.events), fmtInt(r.observations), `${monthYear(r.first_close)} – ${monthYear(r.last_close)}`])
  );
}

const kalshiDay = () => data.reliability.filter((r) => r.platform === "Kalshi" && r.horizon === data.headline_horizon);

// ---------- count-up numbers and scroll reveals ----------
function countUp(el) {
  const target = +el.dataset.target;
  if (!target || el.dataset.done) return;
  el.dataset.done = "1";
  if (reduceMotion()) {
    el.textContent = fmtInt(target);
    return;
  }
  const start = performance.now();
  const dur = 1400;
  const step = (now) => {
    const t = Math.min(1, (now - start) / dur);
    el.textContent = fmtInt(target * (1 - Math.pow(1 - t, 3)));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function wireReveal() {
  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("is-visible");
        entry.target.querySelectorAll(".count").forEach(countUp);
        if (entry.target.classList.contains("count")) countUp(entry.target);
        io.unobserve(entry.target);
      }
    },
    { rootMargin: "0px 0px -8% 0px" }
  );
  document.querySelectorAll(".reveal, .hero .count").forEach((el) => io.observe(el));

  const bar = document.getElementById("progress-bar");
  const onScroll = () => {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    bar.style.width = `${max > 0 ? (window.scrollY / max) * 100 : 0}%`;
    hideTip();
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}

// ---------- hero illustration ----------
function drawHeroArt() {
  const c = colors();
  const svg = d3.select("#hero-art");
  svg.selectAll("*").remove();
  const s = 400;
  const m = 34;
  const x = d3.scaleLinear([0, 1], [m, s - m]);
  const y = d3.scaleLinear([0, 1], [s - m, m]);
  svg.append("rect").attr("x", 6).attr("y", 6).attr("width", s - 12).attr("height", s - 12).attr("rx", 22).attr("fill", c.card).attr("stroke", c.grid);
  for (const v of [0.25, 0.5, 0.75]) {
    svg.append("line").attr("x1", x(v)).attr("x2", x(v)).attr("y1", y(0)).attr("y2", y(1)).attr("stroke", c.grid);
    svg.append("line").attr("x1", x(0)).attr("x2", x(1)).attr("y1", y(v)).attr("y2", y(v)).attr("stroke", c.grid);
  }
  svg.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", c.muted).attr("stroke-dasharray", "5 6");
  svg.append("text").attr("x", x(1)).attr("y", y(0) + 22).attr("text-anchor", "end").attr("fill", c.muted).attr("font-size", 12).attr("font-family", "JetBrains Mono, monospace").text("what the market said →");
  svg.append("text").attr("transform", `translate(${x(0) - 14},${y(1)}) rotate(-90)`).attr("text-anchor", "end").attr("fill", c.muted).attr("font-size", 12).attr("font-family", "JetBrains Mono, monospace").text("what happened →");

  PLATFORMS.forEach((p, i) => {
    svg.append("circle").attr("cx", x(0) + 8).attr("cy", y(1) + 10 + i * 20).attr("r", 5).attr("fill", c[p]);
    svg.append("text").attr("x", x(0) + 20).attr("y", y(1) + 10 + i * 20).attr("dy", "0.35em").attr("fill", c.ink2)
      .attr("font-size", 12).attr("font-family", "JetBrains Mono, monospace").text(p.toLowerCase());
  });
  const pts = PLATFORMS.flatMap((p) => data.reliability.filter((r) => r.platform === p && r.horizon === "1d").map((r) => ({ p, ...r })));
  const dots = svg.append("g").selectAll("circle").data(pts).join("circle")
    .attr("cx", (d) => x(d.mean_price) + (d.p === "Kalshi" ? -3 : 3))
    .attr("cy", y(0.5) + 0)
    .attr("r", 0)
    .attr("fill", (d) => c[d.p])
    .attr("stroke", c.card)
    .attr("stroke-width", 2);
  dots.transition().delay((d, i) => (reduceMotion() ? 0 : 300 + i * 45)).duration(reduceMotion() ? 0 : 900).ease(d3.easeBackOut.overshoot(1.4))
    .attr("r", 7).attr("cy", (d) => y(d.freq));
}

// ---------- the guessing game ----------
const game = { rounds: [], i: 0, wins: 0, maxMarketErr: 0 };

function buildGame() {
  const rows = kalshiDay();
  // Three buckets that tell the story: one spot on, one long shot, one favourite.
  const nearest = (p) => rows.reduce((a, b) => (Math.abs(b.mean_price - p) < Math.abs(a.mean_price - p) ? b : a));
  game.rounds = [nearest(0.25), nearest(0.15), nearest(0.85)];
  showRound();
  const slider = document.getElementById("game-guess");
  slider.addEventListener("input", () => (document.getElementById("game-guess-value").textContent = `${slider.value}%`));
  document.getElementById("game-submit").addEventListener("click", submitGuess);
}

function showRound() {
  const r = game.rounds[game.i];
  const slider = document.getElementById("game-guess");
  slider.disabled = false;
  slider.value = 50;
  document.getElementById("game-guess-value").textContent = "50%";
  document.getElementById("game-round").textContent = `round ${game.i + 1} of ${game.rounds.length}`;
  document.getElementById("game-score").textContent = game.i ? `you ${game.wins} · market ${game.i - game.wins}` : "";
  document.getElementById("game-question").innerHTML = `The market priced these at <b>${fmtCents(r.mean_price)}</b>, a ${fmtPct(r.mean_price, 0)} chance.`;
  document.getElementById("game-sub").textContent = `${fmtInt(r.n)} real markets, a day before they closed. How often did they actually happen?`;
  document.getElementById("game-scale").innerHTML = "";
  document.getElementById("game-submit").hidden = false;
  const result = document.getElementById("game-result");
  result.hidden = true;
  result.innerHTML = "";
}

function submitGuess() {
  const r = game.rounds[game.i];
  const slider = document.getElementById("game-guess");
  const guess = +slider.value / 100;
  slider.disabled = true;
  document.getElementById("game-submit").hidden = true;

  const scale = document.getElementById("game-scale");
  scale.innerHTML =
    `<span class="marker marker--market" style="left:${r.mean_price * 100}%">market ${fmtPct(r.mean_price, 0)}</span>` +
    `<span class="marker marker--truth" style="left:${guess * 100}%">actual ${fmtPct(r.freq)}</span>`;
  // Slide the "actual" marker from the guess to the true value.
  requestAnimationFrame(() => requestAnimationFrame(() => (scale.querySelector(".marker--truth").style.left = `${r.freq * 100}%`)));

  const youErr = Math.abs(guess - r.freq);
  const marketErr = Math.abs(r.mean_price - r.freq);
  game.maxMarketErr = Math.max(game.maxMarketErr, marketErr);
  const won = youErr < marketErr;
  if (won) game.wins += 1;
  const diff = r.freq - r.mean_price;
  const insight =
    Math.abs(diff) < 0.01
      ? "Almost exactly what the market said."
      : diff < 0
        ? `These happened <b>less</b> often than the market said, by ${(marketErr * 100).toFixed(1)} points. Remember that.`
        : `These happened <b>more</b> often than the market said, by ${(marketErr * 100).toFixed(1)} points.`;
  const last = game.i === game.rounds.length - 1;
  const result = document.getElementById("game-result");
  result.innerHTML =
    `<h3>${won ? "You beat the market." : "The market wins this round."}</h3>` +
    `<p>You were ${(youErr * 100).toFixed(1)} points away; the market was ${(marketErr * 100).toFixed(1)}. ${insight}</p>` +
    (last
      ? `<p class="game__final">Final score: you ${game.wins}, the market ${game.rounds.length - game.wins}. Across these rounds the market was never more than ${(game.maxMarketErr * 100).toFixed(1)} points off. It's good, but the misses aren't random.</p>` +
        `<a class="btn btn--primary" href="#c1">see where it goes wrong ↓</a> <button type="button" class="btn" id="game-again">play again</button>`
      : `<button type="button" class="btn btn--primary" id="game-next">next round →</button>`);
  result.hidden = false;
  document.getElementById("game-score").textContent = `you ${game.wins} · market ${game.i + 1 - game.wins}`;
  if (last) {
    document.getElementById("game-again").addEventListener("click", () => {
      Object.assign(game, { i: 0, wins: 0, maxMarketErr: 0 });
      showRound();
    });
  } else {
    document.getElementById("game-next").addEventListener("click", () => {
      game.i += 1;
      showRound();
    });
  }
}

// ---------- example contract ----------
function wireContract() {
  const explain = {
    yes: "You pay <b>30¢</b>. If it rains you get $1 back, a 70¢ profit; if it stays dry you lose the 30¢. It's only worth it if you think the chance of rain is <b>above 30%</b>.",
    no: "You pay <b>70¢</b>. If it stays dry you get $1 back, a 30¢ profit; if it rains you lose the 70¢. It's only worth it if you think the chance of dry weather is <b>above 70%</b>.",
  };
  document.querySelectorAll(".side").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".side").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
      document.getElementById("contract-explain").innerHTML = explain[b.dataset.side];
    })
  );
}

// ---------- fig. 1: calibration, animated across horizons ----------
const hero = {};
const DODGE = { Kalshi: -3.5, Polymarket: 3.5 };

function heroRows(platform, h) {
  return data.reliability.filter((r) => r.platform === platform && r.horizon === HORIZONS[h]);
}

function heroGapLimit() {
  const m = d3.max(data.reliability, (r) => Math.max(Math.abs(r.ci_low - r.mean_price), Math.abs(r.ci_high - r.mean_price)));
  return Math.min(0.16, Math.ceil(m * 50) / 50);
}

const heroY = () => (state.view === "curve" ? hero.yCurve : hero.yGap);
const heroValue = (r) => (state.view === "curve" ? r.freq : r.gap);
const heroLo = (r) => (state.view === "curve" ? r.ci_low : r.ci_low - r.mean_price);
const heroHi = (r) => (state.view === "curve" ? r.ci_high : r.ci_high - r.mean_price);

function buildHero() {
  const c = colors();
  const el = document.getElementById("hero-chart");
  const w = Math.max(280, el.clientWidth);
  const h = Math.min(w, 560);
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
  svg.append("text").attr("x", w - m.right).attr("y", h - 6).attr("text-anchor", "end").attr("fill", c.muted).attr("font-size", 11).text("what the market said →");

  const gy = svg.append("g").attr("transform", `translate(${m.left},0)`);
  const yLabel = svg.append("text").attr("x", m.left).attr("y", m.top - 2).attr("fill", c.muted).attr("font-size", 11).attr("dy", "-0.4em");
  const ref = svg.append("line").attr("stroke", c.ink2).attr("stroke-dasharray", "4 5").attr("stroke-width", 1.2);
  const refLabel = svg.append("text").attr("fill", c.ink2).attr("font-size", 11);
  const upLabel = svg.append("text").attr("x", m.left + 10).attr("y", m.top + 16).attr("fill", c.muted).attr("font-size", 11);
  const downLabel = svg.append("text").attr("x", m.left + 10).attr("y", h - m.bottom - 10).attr("fill", c.muted).attr("font-size", 11);

  const series = {};
  for (const p of PLATFORMS) {
    const g = svg.append("g");
    series[p] = { whiskers: g.append("g"), dots: g.append("g") };
  }
  const halo = svg.append("circle").attr("r", 10).attr("fill", "none").attr("stroke-width", 2).attr("opacity", 0);
  Object.assign(hero, { svg, x, yCurve, yGap, gy, yLabel, ref, refLabel, upLabel, downLabel, series, halo, m, w, h, c });

  svg.append("rect").attr("x", m.left).attr("y", m.top).attr("width", w - m.left - m.right).attr("height", h - m.top - m.bottom)
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
      showTip(event, `${best.p}: priced ${bucket(r)}`, [
        `${fmtInt(r.n)} markets, ${HORIZON_LABEL[HORIZONS[state.h]]} before close`,
        `market said ${fmtPct(r.mean_price)} on average`,
        `actually happened ${fmtPct(r.freq)}`,
        `plausible range ${fmtPct(r.ci_low)} to ${fmtPct(r.ci_high)}`,
      ]);
    })
    .on("pointerleave", () => {
      hideTip();
      halo.attr("opacity", 0);
    });

  updateHero(true);
}

function updateHero(instant = false) {
  const { x, gy, yLabel, ref, refLabel, upLabel, downLabel, series, m, w, c } = hero;
  const y = heroY();
  const t = d3.transition().duration(instant ? 0 : duration()).ease(d3.easeCubicInOut);
  const curve = state.view === "curve";

  gy.transition(t).call(
    d3.axisLeft(y).ticks(curve ? 5 : 6).tickSize(-(w - m.left - m.right)).tickPadding(10)
      .tickFormat((d) => (curve ? `${Math.round(d * 100)}%` : Math.abs(d) < 1e-9 ? "0" : `${d > 0 ? "+" : "−"}${Math.round(Math.abs(d) * 100)}`))
  );
  gy.call((g) => axisStyle(g, c));
  yLabel.text(curve ? "↑ how often it happened" : "");

  if (curve) {
    ref.transition(t).attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1));
    refLabel.transition(t).attr("x", x(1) - 8).attr("y", y(0) - 12).attr("text-anchor", "end");
    refLabel.text("- - -  honest odds");
    upLabel.text("");
    downLabel.text("");
  } else {
    ref.transition(t).attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(0));
    refLabel.text("");
    upLabel.text("↑ happened more often than the market said (points)");
    downLabel.text("↓ happened less often than the market said");
  }

  for (const p of PLATFORMS) {
    const rows = heroRows(p, state.h);
    const key = (r) => r.bin_low;
    series[p].whiskers.selectAll("line").data(rows, key).join(
      (enter) => enter.append("line").attr("stroke", c[p]).attr("stroke-width", 2).attr("stroke-linecap", "round").attr("opacity", 0.45)
        .attr("x1", (r) => x(r.mean_price) + DODGE[p]).attr("x2", (r) => x(r.mean_price) + DODGE[p])
        .attr("y1", (r) => y(heroValue(r))).attr("y2", (r) => y(heroValue(r))),
      (update) => update,
      (exit) => exit.transition(t).attr("opacity", 0).remove()
    ).transition(t)
      .attr("x1", (r) => x(r.mean_price) + DODGE[p]).attr("x2", (r) => x(r.mean_price) + DODGE[p])
      .attr("y1", (r) => y(heroLo(r))).attr("y2", (r) => y(heroHi(r)));

    series[p].dots.selectAll("circle").data(rows, key).join(
      (enter) => enter.append("circle").attr("r", 5.5).attr("fill", c[p]).attr("stroke", c.paper).attr("stroke-width", 1.5)
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
      <dd><b>${(s.ece * 100).toFixed(1)}</b> points off on average, slope <b>${fmtB(s.b)}</b></dd>
    </div>`;
  }).join("");

  const rows = PLATFORMS.flatMap((p) => heroRows(p, state.h).map((r) => ({ p, ...r })));
  document.getElementById("hero-table").innerHTML = table(
    ["Platform", "Price bucket", "Markets", "Avg price", "Happened", "95% CI", "Gap"],
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
    button.textContent = "▶ play";
    return;
  }
  button.textContent = "❚❚ pause";
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
  const w = Math.max(280, el.clientWidth);
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
  // Shade the "long shots overpriced" half so the reading is obvious at a glance.
  svg.append("rect").attr("x", m.left).attr("width", w - m.left - m.right).attr("y", m.top).attr("height", y(1) - m.top)
    .attr("fill", css("--accent-soft")).attr("opacity", 0.45);
  const gy = svg.append("g").attr("transform", `translate(${m.left},0)`)
    .call(d3.axisLeft(y).ticks(6).tickSize(-(w - m.left - m.right)).tickPadding(10).tickFormat(d3.format(".1f")));
  axisStyle(gy, c);
  const gx = svg.append("g").attr("transform", `translate(0,${h - m.bottom})`)
    .call(d3.axisBottom(x).tickSize(0).tickPadding(14).tickFormat((d) => (narrow ? HORIZON_LABEL[d] : `${HORIZON_LABEL[d]} before`)));
  axisStyle(gx, c);

  svg.append("line").attr("x1", m.left).attr("x2", w - m.right).attr("y1", y(1)).attr("y2", y(1)).attr("stroke", c.ink2).attr("stroke-dasharray", "4 5");
  svg.append("text").attr("x", (x("7d") + x("1d")) / 2).attr("y", y(1)).attr("dy", "-0.6em").attr("text-anchor", "middle").attr("fill", c.ink2).attr("font-size", 11).text("honest odds");
  svg.append("text").attr("x", m.left + 8).attr("y", m.top).attr("dy", "1.1em").attr("fill", c.accent).attr("font-size", 11).text("↑ long shots overpriced");
  svg.append("text").attr("x", m.left + 8).attr("y", h - m.bottom - 10).attr("fill", c.muted).attr("font-size", 11).text("↓ prices overconfident");

  for (const p of PLATFORMS) {
    const all = points.filter((d) => d.platform === p && d.sample === "all");
    svg.append("path").datum(all).attr("fill", "none").attr("stroke", c[p]).attr("stroke-width", 1.6).attr("opacity", 0.6)
      .attr("d", d3.line().x(px).y((d) => y(d.v)));
    const last = all[all.length - 1];
    const label = svg.append("text").attr("x", w - m.right + 12).attr("y", y(last.v)).attr("fill", c[p]).attr("font-size", 12).attr("font-weight", 500);
    if (narrow) {
      label.attr("dy", "-0.2em");
      label.append("tspan").text(p.toLowerCase());
      label.append("tspan").attr("x", w - m.right + 12).attr("dy", "1.2em").text(fmtB(last.v));
    } else {
      label.attr("dy", "0.35em").text(`${p.toLowerCase()} ${fmtB(last.v)}`);
    }
  }

  const g = svg.append("g").selectAll("g").data(points).join("g");
  g.append("line").attr("x1", px).attr("x2", px).attr("y1", (d) => y(d.lo)).attr("y2", (d) => y(d.hi))
    .attr("stroke", (d) => c[d.platform]).attr("stroke-width", 2).attr("stroke-linecap", "round").attr("opacity", (d) => (d.sample === "all" ? 0.8 : 0.45));
  g.append("circle").attr("cx", px).attr("cy", (d) => y(d.v)).attr("r", 4.5)
    .attr("fill", (d) => (d.sample === "all" ? c[d.platform] : c.paper)).attr("stroke", (d) => c[d.platform]).attr("stroke-width", 2);
  g.append("circle").attr("cx", px).attr("cy", (d) => y(d.v)).attr("r", 12).attr("fill", "transparent")
    .on("pointermove", (event, d) => showTip(event, `${d.platform}, ${HORIZON_LABEL[d.horizon]} before close`, [
      d.sample === "all" ? "every price" : "without the most extreme prices",
      `slope ${fmtB(d.v)} (95% CI ${fmtB(d.lo)}–${fmtB(d.hi)})`,
    ]))
    .on("pointerleave", hideTip);

  document.getElementById("slope-table").innerHTML = table(
    ["Platform", "Before close", "Slope, all prices", "95% CI", "Slope, 5–95¢", "95% CI"],
    data.summary.map((r) => [r.platform, HORIZON_LABEL[r.horizon], fmtB(r.b), `${fmtB(r.b_low)}–${fmtB(r.b_high)}`, fmtB(r.b_mid), `${fmtB(r.b_mid_low)}–${fmtB(r.b_mid_high)}`])
  );
}

// ---------- calibration over time ----------
function drawPeriods() {
  const c = colors();
  const el = document.getElementById("period-chart");
  const rows = data.by_period;
  if (!rows.length) return;
  const w = Math.max(280, el.clientWidth);
  const narrow = w < 560;
  const h = narrow ? 320 : 360;
  const m = { top: 24, right: narrow ? 92 : 120, bottom: 40, left: 46 };
  el.replaceChildren();

  const periods = [...new Set(rows.map((r) => r.period))];
  const x = d3.scalePoint(periods, [m.left, w - m.right]).padding(0.4);
  const y = d3.scaleLinear([Math.min(0.85, d3.min(rows, (r) => r.b_low)) - 0.02, Math.max(1.15, d3.max(rows, (r) => r.b_high)) + 0.02], [h - m.bottom, m.top]);
  const dx = { Kalshi: -6, Polymarket: 6 };
  const px = (r) => x(r.period) + dx[r.platform];

  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h).attr("viewBox", `0 0 ${w} ${h}`);
  svg.append("rect").attr("x", m.left).attr("width", w - m.left - m.right).attr("y", m.top).attr("height", y(1) - m.top)
    .attr("fill", css("--accent-soft")).attr("opacity", 0.45);
  const gy = svg.append("g").attr("transform", `translate(${m.left},0)`)
    .call(d3.axisLeft(y).ticks(6).tickSize(-(w - m.left - m.right)).tickPadding(10).tickFormat(d3.format(".1f")));
  axisStyle(gy, c);
  const gx = svg.append("g").attr("transform", `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).tickSize(0).tickPadding(14));
  axisStyle(gx, c);
  svg.append("line").attr("x1", m.left).attr("x2", w - m.right).attr("y1", y(1)).attr("y2", y(1)).attr("stroke", c.ink2).attr("stroke-dasharray", "4 5");
  // Top-right: the early-period whisker fills the top-left corner.
  svg.append("text").attr("x", w - m.right - 8).attr("y", m.top).attr("dy", "1.1em").attr("text-anchor", "end").attr("fill", c.accent).attr("font-size", 11).text("long shots overpriced ↑");
  svg.append("text").attr("x", m.left + 8).attr("y", h - m.bottom - 10).attr("fill", c.muted).attr("font-size", 11).text("↓ prices overconfident");

  for (const p of PLATFORMS) {
    const sub = rows.filter((r) => r.platform === p);
    if (!sub.length) continue;
    svg.append("path").datum(sub).attr("fill", "none").attr("stroke", c[p]).attr("stroke-width", 1.6).attr("opacity", 0.6)
      .attr("d", d3.line().x(px).y((r) => y(r.b)));
    const last = sub[sub.length - 1];
    const label = svg.append("text").attr("x", w - m.right + 12).attr("y", y(last.b)).attr("fill", c[p]).attr("font-size", 12).attr("font-weight", 500);
    if (narrow) {
      label.attr("dy", "-0.2em");
      label.append("tspan").text(p.toLowerCase());
      label.append("tspan").attr("x", w - m.right + 12).attr("dy", "1.2em").text(fmtB(last.b));
    } else {
      label.attr("dy", "0.35em").text(`${p.toLowerCase()} ${fmtB(last.b)}`);
    }
  }
  const g = svg.append("g").selectAll("g").data(rows).join("g");
  g.append("line").attr("x1", px).attr("x2", px).attr("y1", (r) => y(r.b_low)).attr("y2", (r) => y(r.b_high))
    .attr("stroke", (r) => c[r.platform]).attr("stroke-width", 2).attr("stroke-linecap", "round").attr("opacity", 0.75);
  g.append("circle").attr("cx", px).attr("cy", (r) => y(r.b)).attr("r", 5).attr("fill", (r) => c[r.platform]).attr("stroke", c.paper).attr("stroke-width", 1.5);
  g.append("circle").attr("cx", px).attr("cy", (r) => y(r.b)).attr("r", 13).attr("fill", "transparent")
    .on("pointermove", (event, r) => showTip(event, `${r.platform}, markets closing ${r.period}`, [
      `${fmtInt(r.n)} prices from ${fmtInt(r.events)} events`,
      `slope ${fmtB(r.b)} (95% CI ${fmtB(r.b_low)}–${fmtB(r.b_high)})`,
      `weather markets ${fmtPct(r.weather_share, 0)} of the mix`,
    ]))
    .on("pointerleave", hideTip);

  const comp = (sample, latest) => data.composition.find((r) => r.sample === sample && (latest ? !r.period.startsWith("before") : r.period.startsWith("before")));
  const all = comp("all markets", true);
  const clean = comp("without weather", true);
  const year = all ? all.period : "";
  const share = document.querySelector('[data-field="weather-share"]')?.textContent;
  document.getElementById("mix-card").innerHTML = all && clean
    ? `<div class="mix__item"><span class="mix__n">${share}</span><span class="mix__l">of Polymarket's ${year} markets were about the weather</span></div>` +
      `<div class="mix__item"><span class="mix__n">${fmtB(all.b)}</span><span class="mix__l">Polymarket's ${year} slope, all markets: overconfident</span></div>` +
      `<div class="mix__item"><span class="mix__n">${fmtB(clean.b)}</span><span class="mix__l">the same, without weather markets: honest odds</span></div>`
    : "";

  document.getElementById("period-table").innerHTML = table(
    ["Platform", "Closing", "Prices", "Events", "Slope", "95% CI", "Weather share"],
    rows.map((r) => [r.platform, r.period, fmtInt(r.n), fmtInt(r.events), fmtB(r.b), `${fmtB(r.b_low)}–${fmtB(r.b_high)}`, fmtPct(r.weather_share, 0)])
  );
}

// ---------- out-of-sample forecast test ----------
function drawForecast() {
  const c = colors();
  const el = document.getElementById("forecast-chart");
  const rows = data.forecast.map((r) => ({ ...r, label: `${r.platform.toLowerCase()}, ${HORIZON_LABEL[r.horizon]} before` }));
  if (!rows.length) return;
  const w = Math.max(280, el.clientWidth);
  const narrow = w < 520;
  const rowH = 44;
  const m = { top: 30, right: 20, bottom: 40, left: narrow ? 104 : 170 };
  const h = m.top + rows.length * rowH + m.bottom;
  el.replaceChildren();

  const lim = Math.max(0.01, d3.max(rows, (r) => Math.max(Math.abs(r.improvement_low), Math.abs(r.improvement_high)))) * 1.15;
  const x = d3.scaleLinear([-lim, lim], [m.left, w - m.right]);
  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h).attr("viewBox", `0 0 ${w} ${h}`);
  const gx = svg.append("g").attr("transform", `translate(0,${h - m.bottom})`)
    .call(d3.axisBottom(x).ticks(narrow ? 4 : 6).tickSize(-(h - m.bottom - m.top)).tickPadding(10)
      .tickFormat((d) => (Math.abs(d) < 1e-9 ? "0" : `${d > 0 ? "+" : "−"}${(Math.abs(d) * 100).toFixed(1)}%`)));
  axisStyle(gx, c);
  svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", m.top - 6).attr("y2", h - m.bottom).attr("stroke", c.ink2).attr("stroke-dasharray", "4 5");
  svg.append("text").attr("x", x(0) + 8).attr("y", m.top - 12).attr("fill", c.yes).attr("font-size", 11).text("correction helps →");
  svg.append("text").attr("x", x(0) - 8).attr("y", m.top - 12).attr("text-anchor", "end").attr("fill", c.muted).attr("font-size", 11).text("← market's odds better");

  rows.forEach((r, i) => {
    const yy = m.top + i * rowH + rowH / 2;
    const clear = r.improvement_low > 0 || r.improvement_high < 0;
    const label = svg.append("text").attr("x", m.left - 14).attr("y", yy).attr("text-anchor", "end").attr("fill", c.ink).attr("font-size", 12);
    if (narrow) {
      // Two lines so the label fits the narrow left margin.
      label.append("tspan").attr("x", m.left - 14).attr("dy", "-0.25em").text(r.platform.toLowerCase());
      label.append("tspan").attr("x", m.left - 14).attr("dy", "1.2em").attr("fill", c.muted).text(`${HORIZON_LABEL[r.horizon]} before`);
    } else {
      label.attr("dy", "0.35em").text(r.label);
    }
    svg.append("line").attr("x1", x(r.improvement_low)).attr("x2", x(r.improvement_high)).attr("y1", yy).attr("y2", yy)
      .attr("stroke", c[r.platform]).attr("stroke-width", 2.5).attr("stroke-linecap", "round").attr("opacity", 0.8);
    svg.append("circle").attr("cx", x(r.improvement)).attr("cy", yy).attr("r", 5.5)
      .attr("fill", clear ? c[r.platform] : c.paper).attr("stroke", c[r.platform]).attr("stroke-width", 2);
    svg.append("rect").attr("x", 0).attr("y", yy - rowH / 2).attr("width", w).attr("height", rowH).attr("fill", "transparent")
      .on("pointermove", (event) => showTip(event, `${r.platform}, ${HORIZON_LABEL[r.horizon]} before close`, [
        `learned on ${fmtInt(r.train_n)} earlier prices, tested on ${fmtInt(r.test_n)}`,
        `accuracy change ${fmtSigned(r.improvement, 2)} (95% CI ${fmtSigned(r.improvement_low, 2)} to ${fmtSigned(r.improvement_high, 2)})`,
        clear ? (r.improvement > 0 ? "the correction clearly helped" : "the correction clearly hurt") : "no clear difference",
      ]))
      .on("pointerleave", hideTip);
  });

  document.getElementById("forecast-table").innerHTML = table(
    ["Platform", "Before close", "Trained on", "Tested on", "Brier, market", "Brier, corrected", "Change", "95% CI"],
    data.forecast.map((r) => [r.platform, HORIZON_LABEL[r.horizon], fmtInt(r.train_n), fmtInt(r.test_n), r.brier_raw.toFixed(4), r.brier_recal.toFixed(4), fmtSigned(r.improvement, 2), `${fmtSigned(r.improvement_low, 2)} to ${fmtSigned(r.improvement_high, 2)}`])
  );
}

// ---------- simulator ----------
function describePrice(r) {
  const p = (r.bin_low + r.bin_high) / 2;
  if (p < 0.1) return "the market says it almost certainly won't happen";
  if (p < 0.4) return "the market says it probably won't happen";
  if (p < 0.6) return "the market calls it a coin flip";
  if (p < 0.9) return "the market says it probably will happen";
  return "the market says it's a near-sure thing";
}

function updateSim() {
  const c = colors();
  const r = data.returns[state.simIdx];
  const side = state.simSide;
  const ret = r[`${side}_return`];
  const lo = r[`${side}_ci_low`];
  const hi = r[`${side}_ci_high`];
  const back = 100 * (1 + ret);
  const scale = (v) => `${Math.max(0, Math.min(100, (v / 200) * 100))}%`;

  document.getElementById("sim-bucket").textContent = bucket(r);
  const money = document.getElementById("sim-money");
  // Show cents when the result is within a dollar of the stake, so a 0.1% loss doesn't read as "$0".
  const dollars = (v) => (Math.abs(v - 100) < 1 || Math.abs(v) < 1 ? v.toFixed(2) : String(Math.round(v)));
  money.textContent = `$${dollars(back)}`;
  const clearLoss = hi < 0;
  const clearGain = lo > 0;
  const tone = clearLoss ? c.no : clearGain ? c.yes : c.ink;
  money.style.color = tone;
  const fill = document.getElementById("sim-fill");
  fill.style.width = scale(back);
  fill.style.background = clearLoss ? c.no : clearGain ? c.yes : c.neutral;
  const band = document.getElementById("sim-band");
  band.style.left = scale(100 * (1 + lo));
  band.style.width = `calc(${scale(100 * (1 + hi))} - ${scale(100 * (1 + lo))})`;

  const gap = Math.abs(back - 100);
  const diff = gap < 1 ? gap.toFixed(2) : String(Math.round(gap));
  const what = `Betting ${side} when yes costs ${bucket(r)}: ${describePrice(r)}.`;
  const verdict = clearLoss
    ? `You'd lose about <b>$${diff}</b> per $100${gap < 1 ? ", small but consistent" : ""}, a clear result across ${fmtInt(r.n)} markets.`
    : clearGain
      ? `You'd gain about <b>$${diff}</b> per $100 across ${fmtInt(r.n)} markets.`
      : `Roughly break-even: the ${back < 100 ? "loss" : "gain"} of $${diff} per $100 is within the margin of error (${fmtInt(r.n)} markets).`;
  document.getElementById("sim-verdict").innerHTML = `${what} ${verdict} Any single bet either pays out or doesn't; this is the average.`;
}

function wireSim() {
  const range = document.getElementById("sim-range");
  range.max = data.returns.length - 1;
  range.addEventListener("input", () => {
    state.simIdx = +range.value;
    updateSim();
  });
  document.querySelectorAll("[data-sim-side]").forEach((b) =>
    b.addEventListener("click", () => {
      state.simSide = b.dataset.simSide;
      document.querySelectorAll("[data-sim-side]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
      updateSim();
    })
  );
  updateSim();
}

// ---------- fig. 3: slope by volume and category ----------
function forestRows(platform) {
  const rows = data.slopes.filter((r) => r.platform === platform);
  const popularity = { Low: "least popular third", Mid: "middle third", High: "most popular third" };
  const recentLabel = { None: "no trades", Light: "light trading", Heavy: "heavy trading" };
  const volume = ["Low", "Mid", "High"].map((l) => rows.find((r) => r.group === "Volume" && r.level === l)).filter(Boolean);
  const recent = ["None", "Light", "Heavy"].map((l) => rows.find((r) => r.group === "Recent trading" && r.level === l)).filter(Boolean);
  const category = rows.filter((r) => r.group === "Category").sort((a, b) => b.b - a.b);
  return [
    { header: "by popularity" },
    ...volume.map((r) => ({ ...r, label: popularity[r.level] })),
    ...(recent.length ? [{ header: "by trading, last 24h" }, ...recent.map((r) => ({ ...r, label: recentLabel[r.level] }))] : []),
    { header: "by topic" },
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
    svg.append("rect").attr("x", x(1)).attr("width", w - m.right - x(1)).attr("y", m.top).attr("height", h - m.bottom - m.top + 6)
      .attr("fill", css("--accent-soft")).attr("opacity", 0.35);
    const gx = svg.append("g").attr("transform", `translate(0,${h - m.bottom + 6})`)
      .call(d3.axisBottom(x).ticks(5).tickSize(-(h - m.bottom - m.top + 6)).tickPadding(10).tickFormat(d3.format(".1f")));
    axisStyle(gx, c);
    svg.append("line").attr("x1", x(1)).attr("x2", x(1)).attr("y1", m.top).attr("y2", h - m.bottom + 6).attr("stroke", c.ink2).attr("stroke-dasharray", "4 5");

    rows.forEach((r, i) => {
      const y = yOf(i);
      if (r.header) {
        svg.append("text").attr("x", 0).attr("y", y + 4).attr("fill", c.faint).attr("font-size", 10.5).attr("letter-spacing", "0.04em").text(r.header);
        return;
      }
      const significant = r.b_low > 1 || r.b_high < 1;
      svg.append("text").attr("x", m.left - 12).attr("y", y).attr("dy", "0.35em").attr("text-anchor", "end")
        .attr("fill", significant ? c.ink : c.muted).attr("font-weight", significant ? 500 : 400).attr("font-size", 12).text(r.label);
      svg.append("line").attr("x1", x(r.b_low)).attr("x2", x(r.b_high)).attr("y1", y).attr("y2", y)
        .attr("stroke", c[platform]).attr("stroke-width", 2).attr("stroke-linecap", "round").attr("opacity", significant ? 0.9 : 0.4);
      svg.append("circle").attr("cx", x(r.b)).attr("cy", y).attr("r", 4.5)
        .attr("fill", significant ? c[platform] : c.paper).attr("stroke", c[platform]).attr("stroke-width", 2);
      svg.append("rect").attr("x", 0).attr("y", y - rowH / 2).attr("width", w).attr("height", rowH).attr("fill", "transparent")
        .on("pointermove", (event) => showTip(event, `${platform}: ${r.label}`, [
          `${fmtInt(r.events)} events`,
          `slope ${fmtB(r.b)} (95% CI ${fmtB(r.b_low)}–${fmtB(r.b_high)})`,
          significant ? (r.b > 1 ? "long shots clearly overpriced" : "clearly overconfident") : "consistent with honest odds",
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
    svg.append("text").attr("x", w - m.right).attr("y", h - 4).attr("text-anchor", "end").attr("fill", c.muted).attr("font-size", 11).text("yes price, ¢ →");
    svg.append("line").attr("x1", m.left).attr("x2", w - m.right).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", c.ink2);

    const val = (r) => r[`${side}_return`];
    const ciLo = (r) => r[`${side}_ci_low`];
    const ciHi = (r) => r[`${side}_ci_high`];
    const sig = (r) => ciHi(r) < 0;
    svg.append("g").selectAll("rect").data(rows).join("rect")
      .attr("x", (r) => x(bucket(r))).attr("width", x.bandwidth())
      .attr("y", (r) => y(Math.max(0, val(r)))).attr("height", (r) => Math.max(1, Math.abs(y(val(r)) - y(0))))
      .attr("rx", 2).attr("fill", (r) => (sig(r) ? c.no : c.neutral));
    svg.append("g").selectAll("line").data(rows).join("line")
      .attr("x1", (r) => x(bucket(r)) + x.bandwidth() / 2).attr("x2", (r) => x(bucket(r)) + x.bandwidth() / 2)
      .attr("y1", (r) => y(ciLo(r))).attr("y2", (r) => y(ciHi(r))).attr("stroke", c.ink2).attr("stroke-width", 1).attr("opacity", 0.6);

    const worst = rows.reduce((a, b) => (val(b) < val(a) ? b : a));
    const left = x(bucket(worst)) < w / 2;
    svg.append("text").attr("x", left ? x(bucket(worst)) : x(bucket(worst)) + x.bandwidth())
      .attr("y", y(Math.min(val(worst), ciLo(worst))) + 15)
      .attr("text-anchor", left ? "start" : "end").attr("fill", c.no).attr("font-size", 12).attr("font-weight", 500)
      .text(fmtSigned(val(worst), 0));

    svg.append("g").selectAll("rect").data(rows).join("rect")
      .attr("x", (r) => x(bucket(r)) - (x.step() - x.bandwidth()) / 2).attr("width", x.step())
      .attr("y", m.top).attr("height", h - m.top - m.bottom).attr("fill", "transparent")
      .on("pointermove", (event, r) => showTip(event, `Bet ${side} when yes costs ${bucket(r)}`, [
        `${fmtInt(r.n)} markets`,
        `$100 becomes $${Math.round(100 * (1 + val(r)))} on average`,
        `plausible range ${fmtSigned(ciLo(r))} to ${fmtSigned(ciHi(r))}`,
      ]))
      .on("pointerleave", hideTip);
  }

  document.getElementById("returns-table").innerHTML = table(
    ["Yes price", "Markets", "Buy yes", "95% CI", "Buy no", "95% CI"],
    rows.map((r) => [bucket(r), fmtInt(r.n), fmtSigned(r.yes_return), `${fmtSigned(r.yes_ci_low)} to ${fmtSigned(r.yes_ci_high)}`, fmtSigned(r.no_return), `${fmtSigned(r.no_ci_low)} to ${fmtSigned(r.no_ci_high)}`])
  );
}

// ---------- wiring ----------
function drawCharts() {
  buildHero();
  drawSlopes();
  drawPeriods();
  drawForecast();
  drawForest();
  drawReturns();
}

function wire() {
  document.getElementById("horizon-range").addEventListener("input", (e) => {
    if (state.timer) togglePlay();
    setHorizon(+e.target.value);
  });
  document.querySelector(".play").addEventListener("click", togglePlay);
  document.querySelectorAll(".hero-side .toggle button").forEach((b) =>
    b.addEventListener("click", () => {
      state.view = b.dataset.view;
      document.querySelectorAll(".hero-side .toggle button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
      updateHero();
    })
  );
  let timer;
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    clearTimeout(timer);
    timer = setTimeout(drawCharts, 150);
  });
}

async function main() {
  try {
    data = await (await fetch("data/results.json", { cache: "no-cache" })).json();
  } catch (error) {
    document.querySelectorAll(".chart").forEach((el) => (el.textContent = "Couldn't load the results. They're also in the README on GitHub."));
    document.querySelectorAll(".reveal").forEach((el) => el.classList.add("is-visible"));
    return;
  }
  fillText();
  wireGlossary();
  wireContract();
  buildGame();
  wireSim();
  wire();
  drawHeroArt();
  drawCharts();
  wireReveal();
}

main();
