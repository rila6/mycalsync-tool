# earnings-ics

Generates subscribable `.ics` calendar feeds of upcoming company earnings dates, with a before-market-open (BMO) or after-market-close (AMC) tag in each event title.

- Pure Python 3.11+ standard library, no dependencies.
- Runs on a schedule with GitHub Actions and publishes the feeds with GitHub Pages.
- Data comes from the Finnhub free API (requires your own API key).
- Watchlists are read from the `WATCHLISTS_TOML` environment variable (set as a repository secret) or from `watchlists.toml`. The file in this repo is an example only.

## Usage

```
export FINNHUB_API_KEY=...
python3 earnings_ics.py --out output
python3 test_earnings_ics.py
```

## Notes

- Event times are approximate session windows, not release times.
- Earnings dates can change until companies confirm them.
- For planning only; not financial advice.

Licensed under the MIT License (see `LICENSE`).
