# earnings-ics

Up to 3 watchlists (1–30 tickers each) → one live `.ics` URL each, with `[BMO]` / `[AMC]` in the title and your own reminders. Free: Finnhub free key + GitHub Actions + GitHub Pages. Stdlib-only Python 3.11+.

## Is it really free?
Checked against GitHub's docs (Oct 2026):
- **Pages:** available on GitHub Free for **public** repos only. Private repos need Pro/Team/Enterprise.
- **Actions:** standard GitHub-hosted runners are free in public repos.
- **Finnhub:** free tier, personal-use licence, 60 calls/min (this project makes at most 30 per run).

## Set up (personal GitHub account, public repo)
1. Get a free key at finnhub.io.
2. Create a **public** repo with a neutral name and push these files. Turn on two-factor authentication.
3. Settings → Pages → Source: **GitHub Actions**.
4. Settings → Secrets and variables → Actions → New repository secret:
   - `FINNHUB_API_KEY`: your key.
   - `WATCHLISTS_TOML`: paste your real watchlists (same format as `watchlists.toml`). It overrides the file, so your tickers never appear in the public repo. Each `file` must be unguessable, e.g. `python3 -c "import secrets;print(secrets.token_urlsafe(9))"` → `tech-Xk3_9aBq.ics`.
5. Actions → "Update earnings calendars" → Run workflow. Your URL is `https://<user>.github.io/<repo>/<file>`.
6. The committed `watchlists.toml` is only an example. Do not put real tickers or file names in it.

Anyone who has a calendar URL can read that watchlist; the random filename is obscurity, not security.

## Thunderbird
Calendar → New Calendar → On the Network → iCalendar (ICS) → paste URL. Set "Refresh Calendar" to e.g. every 30 min (the feed itself rebuilds twice a day). Google Calendar: Other calendars → From URL.

## Notes
- `mode = "block"`: wide US Eastern placeholder windows (BMO 07:00–09:30, AMC 16:00–18:00, DMH 11:00–13:00). Not the release minute. Unknown session stays all-day. Override with `bmo_start = "06:30"` etc. and `block_minutes`. `mode = "allday"`: all-day events. Reminders count back from the event start, so for all-day events `1d` fires at midnight the day before.
- Finnhub often leaves the session blank (43% of events in a Dow 30 sample feed were TBD). The script then reuses that company's previous session and tags it `[AMC?]`.
- Fail-safe: if any ticker fails to fetch (after retries) nothing is written and the job fails, so the last good calendars stay online. A ticker with no data prints a warning.
- Keep-alive: GitHub disables scheduled workflows in a public repo after 60 days without repo activity. `keepalive.yml` runs weekly and adds an empty commit when the last commit is 30+ days old. If calendars ever stop updating, check the Actions tab for a "disabled" banner and re-enable.
- Dates move; events keep a stable UID (ticker + fiscal quarter) so Thunderbird updates them in place.
- Test without network: `python3 earnings_ics.py --fixture rows.json --out temp/out`; unit tests: `python3 test_earnings_ics.py`.
