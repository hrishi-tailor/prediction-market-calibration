# Methodology

This note explains the design choices behind the analysis, and the traps each
one avoids.

## 1. Which price?

The obvious approach, bucketing markets by their final price, is useless. By
the time a market closes the outcome is usually known, so final prices sit near
0 or 1 and every market looks "calibrated".

Instead each market's price is read at four fixed horizons before it closed:
**30 days, 7 days, 1 day and 1 hour**. A market contributes one observation per
horizon at which it was already open. Calibration should hold at every horizon
if prices are probabilities, so comparing horizons also shows how calibration
changes as information arrives.

- **Kalshi:** the price is the midpoint of the best bid and ask from hourly
  candles. A candle's quote stays in force until the next candle, so the quote
  at time *t* is the last candle that ended at or before *t*. Quotes with a
  spread wider than 10¢ are dropped, because the midpoint of a wide market says
  little.
- **Polymarket:** the price is the CLOB price history for the Yes token. Over a
  market's whole life the API serves only 12-hourly or daily points, but it
  serves hourly points for a window of up to two weeks, so each market has both:
  hourly prices for the 14 days before close, and coarse prices before that. A
  point older than the horizon's tolerance (2h at 1h, 6h at 1d, 1 day at 7d,
  3 days at 30d) is dropped. Polymarket's "close" is the earlier of its
  scheduled end date and the time it actually closed.

No price observed after the horizon is ever used, so nothing leaks from the
future.

### The stopping-time trap

There is a subtler leak. Many markets ("Will X happen by D?") close as soon as
X happens. For those, the close time depends on the outcome: a Yes market
closes at the moment of the event, a No market at the deadline. Prices are
unbiased forecasts at any *fixed* time, but not at a time chosen by the
outcome, and "1 day before close" is exactly such a time. This biases the
sample toward Yes being underpriced.

Anchoring on the scheduled end date instead doesn't fix it: sports markets on
Polymarket list an end date days after the game, so their "1 hour before end"
price would come after the result was known. So the analysis keeps the actual
close as the anchor, flags markets that closed more than a day before their
scheduled end (`closed_early`), and reports Polymarket calibration with and
without them. The on-schedule subset is itself selected on outcome (it drops
markets whose event happened early), so the two views bracket the effect.
Kalshi doesn't expose a separate scheduled close time, so this check is
Polymarket-only.

## 2. Which markets?

| | Kalshi | Polymarket |
|---|---|---|
| Source | Trade API v2: `/series`, `/markets`, `/historical/markets`, candlesticks | Gamma `/events`, CLOB `/prices-history` |
| Included | Binary markets settled Yes or No | Order-book Yes/No markets that resolved cleanly to 1 or 0 via UMA |
| Excluded | Combo (multivariate) markets, voided markets | 50/50 splits, disputed or unresolved markets, AMM-era markets with no order book (no price history) |
| Liquidity floor | 1,000 contracts lifetime volume | $10,000 lifetime volume |
| Discovery | Series with ≥10k lifetime volume; latest 1,000 markets per series from each of the live and historical endpoints | Every closed event, week by week |
| Sampling | ≤10 per series, then ≤25,000 total | ≤10 per event, then ≤30,000 total |

Sampling caps matter. Kalshi runs hourly temperature, index and crypto series
that settle thousands of near-identical markets; without a per-series cap they
would be most of the sample and the result would describe weather markets, not
prediction markets. All sampling is random with a fixed seed.

## 3. Dependence between markets

Markets in the same event resolve together: exactly one bracket of a CPI print
resolves Yes, and a candidate's state-by-state markets move as one. Treating
them as independent overstates the sample size and makes confidence intervals
far too narrow (a unit test demonstrates this). Every interval and standard
error here is **clustered by event**: reliability-table intervals use a cluster
bootstrap that resamples whole events, and the regressions use cluster-robust
standard errors.

## 4. Measuring calibration

- **Reliability table:** realized Yes frequency per price bucket, with finer
  buckets in the tails (0–2¢, 2–5¢, 5–10¢, …, 95–98¢, 98–100¢), where the
  favourite–longshot bias lives.
- **Brier score,** split into *reliability* (calibration error, lower is
  better), *resolution* (how informative prices are, higher is better) and
  *uncertainty* (fixed by the base rate).
- **Expected calibration error:** count-weighted mean |frequency − price|.

## 5. Testing the favourite–longshot bias

A logistic calibration regression,

    P(Yes) = sigmoid(a + b · logit(price)),

summarises calibration in two numbers. Perfect calibration is a = 0, b = 1.

- **b > 1:** true probabilities are more extreme than prices. Longshots are
  overpriced and favourites underpriced; this is the favourite–longshot bias.
- **b < 1:** prices are too extreme (overconfident).

The slope does not depend on whether a market is quoted from its Yes or No
side, which makes it a clean test. Kalshi's arbitrary choice of which side is
"Yes" cannot create or hide the effect.

To see what drives the bias, the slope is estimated within subgroups (horizon,
volume tercile, category) and in one joint model with `logit(price)`
interacted with all three.

### The tick-size trap

Kalshi quotes in whole cents. A near-certain outcome cannot be priced above
about 98.5¢ (the midpoint of a 98/99 market), and a near-impossible one below
about 0.5¢. That floor alone compresses extreme prices toward 50¢, which raises
the slope even when traders are perfectly rational. Every slope is therefore
also reported refitted on prices between 5¢ and 95¢ only (`b_5_95`); only what
survives that is evidence of a behavioural bias.

## 6. Does it make money?

Calibration errors only matter to a trader if they survive costs. The returns
analysis trades **at the quote**, not the midpoint: buying Yes pays the ask, and
buying No pays 1 − bid. Both pay Kalshi's taker fee, `0.07 · P · (1 − P)` per
contract (the large-order rate; Kalshi rounds up to the cent per order).
Showing both sides answers the natural follow-up: if longshots are overpriced,
does fading them pay? Polymarket has no historical order book in this data, so
its returns would be optimistic and are not reported.

## 7. Could the cost of capital explain it?

A contract that pays $1 in 30 days is worth slightly less than $1 today. If
both sides are priced at their discounted value, the quoted midpoint is shrunk
toward 50¢:

    midpoint = 0.5 + e^(−rT) · (q − 0.5)

which looks exactly like a favourite–longshot bias. At 5% a year the largest
possible distortion is 0.21¢ at 30 days and 0.01¢ at 1 day, so any bias much
larger than that cannot be a cost-of-capital effect.

## 8. Known limitations

- Polymarket's 30-day horizon relies on 12-hourly or daily points, and its
  "price" is the last traded or mid price without a spread.
- Lifetime volume is measured at the end, so the volume split is "markets that
  ended up liquid", not liquidity at the time of the price.
- Kalshi's per-series listing reads the most recent 1,000 markets per series
  per endpoint, so very long-running daily series are sampled from their recent
  history.
- Categories come from each platform's own labels, which are not comparable
  one-to-one across platforms.
