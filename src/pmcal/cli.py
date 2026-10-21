"""Command-line entry point: `pmcal <command>`."""

from __future__ import annotations

import argparse
import logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pmcal", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    k = sub.add_parser("fetch-kalshi", help="download settled Kalshi markets and candles")
    k.add_argument("--rate", type=float, default=4.0, help="requests per second")
    p = sub.add_parser("fetch-polymarket", help="download resolved Polymarket markets and prices")
    p.add_argument("--rate", type=float, default=8.0, help="requests per second")
    sub.add_parser("build", help="build the per-horizon observations dataset")
    sub.add_parser("analyze", help="compute results, write figures/ and reports/")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs every request at INFO, which buries progress output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "fetch-kalshi":
        from pmcal import kalshi

        kalshi.run(rate=args.rate)
    elif args.command == "fetch-polymarket":
        from pmcal import polymarket

        polymarket.run(rate=args.rate)
    elif args.command == "build":
        from pmcal import dataset

        dataset.build()
    elif args.command == "analyze":
        from pmcal import report

        report.run()


if __name__ == "__main__":
    main()
