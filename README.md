# Are prediction markets calibrated?

When a prediction market prices an outcome at 70¢, does it happen 70% of the time?

This project pulls thousands of resolved markets from Kalshi and Polymarket, measures each
market's price at fixed horizons before it closed (30 days, 7 days, 1 day, 1 hour), and tests:

1. **Calibration**: do prices match realized frequencies?
2. **Favourite–longshot bias**: are cheap contracts systematically overpriced?
3. **Drivers**: does miscalibration depend on category, volume or time to close?

Work in progress.
