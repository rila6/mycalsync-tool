#!/usr/bin/env python3
"""Build one live .ics earnings calendar per watchlist (stdlib only).

Data: Finnhub free /calendar/earnings (gives BMO/AMC/DMH, not the exact release minute).
Config: watchlists.toml, or the WATCHLISTS_TOML env var (keeps tickers private).
Key:    FINNHUB_API_KEY env var.

Never publishes a degraded feed: if any ticker cannot be fetched, nothing is written
and the process exits non-zero, so the previously deployed calendars stay in place.
"""
import argparse
import json
import os
import re
import sys
import time as _time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SESSIONS = {"bmo": "BMO", "amc": "AMC", "dmh": "DMH"}
SESSION_TEXT = {
    "BMO": "Before market open",
    "AMC": "After market close",
    "DMH": "During market hours",
    "TBD": "Session not announced",
    "DATE": "Date only (reported on the home-market listing; US session not shown)",
}
TAGS = {"DATE": "date only"}
# Wide US-Eastern windows (start, minutes): the width signals that the exact release
# minute is unknown. TBD has no window and stays an all-day event.
DEFAULT_BLOCKS = {"BMO": ("07:00", 150), "AMC": ("16:00", 120), "DMH": ("11:00", 120)}
DUR = re.compile(r"^(\d+)\s*([mhdw])$")
REQUEST_PAUSE = 0.5  # Finnhub free tier: 60 calls/min
MAX_RETRIES = 4
HISTORY_DAYS = 400  # look back far enough to learn each company's usual session


class FetchError(Exception):
    pass


def parse_reminder(text: str) -> str:
    """'30m' / '2h' / '1d' / '1w' -> iCalendar TRIGGER duration (before start)."""
    m = DUR.match(text.strip().lower())
    if not m:
        raise ValueError(f"bad reminder {text!r}; use e.g. 30m, 2h, 1d, 1w")
    n, unit = m.groups()
    return {"m": f"-PT{n}M", "h": f"-PT{n}H", "d": f"-P{n}D", "w": f"-P{n}W"}[unit]


def load_config(path: Path) -> dict:
    raw = os.environ.get("WATCHLISTS_TOML")
    cfg = tomllib.loads(raw) if raw else tomllib.loads(path.read_text())
    lists = cfg.get("watchlist", [])
    if not 1 <= len(lists) <= 3:
        raise ValueError("define 1-3 [[watchlist]] tables")
    files = [wl.get("file") for wl in lists]
    if len(set(files)) != len(files):
        raise ValueError("each watchlist needs a different 'file'")
    for wl in lists:
        wl["tickers"] = sorted({t.strip().upper() for t in wl.get("tickers", []) if t.strip()})
        if not 1 <= len(wl["tickers"]) <= 30:
            raise ValueError(f"{wl.get('name')}: needs 1-30 tickers, has {len(wl['tickers'])}")
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.ics", wl.get("file", "")):
            raise ValueError(f"{wl.get('name')}: 'file' must look like name-abc123.ics")
        wl["reminders"] = [parse_reminder(r) for r in wl.get("reminders", [])]
        wl.setdefault("mode", "block")
        if wl["mode"] not in ("block", "allday"):
            raise ValueError(f"{wl.get('name')}: mode must be 'block' or 'allday'")
    return cfg


def fetch_earnings(symbol: str, start: date, end: date, key: str) -> list[dict]:
    """One Finnhub call with retry/backoff. Never puts the API key in an error message."""
    qs = urllib.parse.urlencode({"from": start, "to": end, "symbol": symbol, "token": key})
    req = urllib.request.Request(
        f"https://finnhub.io/api/v1/calendar/earnings?{qs}",
        headers={"User-Agent": "earnings-ics/1.0"},
    )
    reason = "unknown"
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp).get("earningsCalendar") or []
        except urllib.error.HTTPError as e:
            reason = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                break  # 401/403/404...: retrying will not help
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            reason = type(e).__name__
        _time.sleep(2 ** attempt)
    raise FetchError(f"{symbol}: {reason}")


def normalise(rows: list[dict], today: date, days_back: int) -> list[dict]:
    """Per ticker: dedupe by fiscal quarter, infer a missing session from the company's
    previous report (they almost always keep BMO/AMC), drop events older than days_back."""
    by_symbol: dict[str, dict[tuple, dict]] = {}
    for r in rows:
        if not r.get("date") or not r.get("symbol"):
            continue
        qkey = (r.get("year"), r.get("quarter")) if r.get("year") and r.get("quarter") else ("d", r["date"])
        cur = by_symbol.setdefault(r["symbol"], {}).get(qkey)
        if cur is None or r["date"] > cur["date"]:  # a moved date replaces the old one
            by_symbol[r["symbol"]][qkey] = r
    out = []
    for sym, quarters in by_symbol.items():
        last_known = None
        for r in sorted(quarters.values(), key=lambda r: r["date"]):
            session = SESSIONS.get((r.get("hour") or "").lower())
            inferred = False
            if r.get("foreign"):  # home-listing row: the date is right, BMO/AMC is not
                session = "DATE"
            elif session:
                last_known = session
            elif last_known:
                session, inferred = last_known, True
            else:
                session = "TBD"
            if date.fromisoformat(r["date"]) < today - timedelta(days=days_back):
                continue
            q = f"{r['year']}Q{r['quarter']}" if r.get("year") and r.get("quarter") else r["date"]
            out.append({
                "symbol": sym, "date": r["date"], "session": session, "inferred": inferred,
                "uid": f"{sym}-{q}@earnings-ics",
                "eps_est": r.get("epsEstimate"), "eps_act": r.get("epsActual"),
            })
    return out


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > 74:
            out.append(cur.decode())
            cur = b" "
        cur += b
    out.append(cur.decode())
    return "\r\n".join(out)


def utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_event(ev: dict, wl: dict, now: datetime) -> list[str]:
    sym, session = ev["symbol"], ev["session"]
    d = date.fromisoformat(ev["date"])
    lines = ["BEGIN:VEVENT", f"UID:{ev['uid']}", f"DTSTAMP:{utc(now)}"]
    block = DEFAULT_BLOCKS.get(session) if wl["mode"] == "block" else None
    if block:
        hhmm, mins = block
        hh, mm = map(int, wl.get(f"{session.lower()}_start", hhmm).split(":"))
        start = datetime.combine(d, time(hh, mm), tzinfo=NY)
        lines += [f"DTSTART:{utc(start)}", f"DTEND:{utc(start + timedelta(minutes=wl.get('block_minutes', mins)))}"]
    else:  # allday mode, or session unknown: do not invent a clock time
        lines += [
            f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
            f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
        ]
    tag = TAGS.get(session, f"{session}?" if ev["inferred"] else session)
    desc = [SESSION_TEXT[session] + (" (inferred from the previous report)" if ev["inferred"] else "")]
    if ev["eps_act"] is not None:
        desc.append(f"Reported EPS: {ev['eps_act']}")
    elif ev["eps_est"] is not None:
        desc.append(f"EPS estimate: {ev['eps_est']}")
    if block:
        desc.append("Time block is an approximate US Eastern session window, not the release time.")
    desc.append("Dates may shift until the company confirms them.")
    lines += [
        f"SUMMARY:{esc(f'{sym} earnings [{tag}]')}",
        f"DESCRIPTION:{esc(chr(10).join(desc))}",
        "TRANSP:TRANSPARENT",
    ]
    for trig in wl["reminders"]:
        lines += [
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(sym + ' earnings')}",
            f"TRIGGER:{trig}", "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def build_calendar(wl: dict, events: list[dict], now: datetime) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//earnings-ics//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", f"X-WR-CALNAME:{esc(wl.get('name', 'Earnings'))}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for ev in sorted(events, key=lambda e: (e["date"], e["symbol"])):
        lines += build_event(ev, wl, now)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="watchlists.toml", type=Path)
    ap.add_argument("--out", default="docs", type=Path)
    ap.add_argument("--fixture", type=Path, help="JSON list of Finnhub rows; skips network (testing)")
    ap.add_argument("--days-back", type=int, default=7)
    ap.add_argument("--days-ahead", type=int, default=180)
    args = ap.parse_args()

    cfg = load_config(args.config)
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key and not args.fixture:
        print("FINNHUB_API_KEY is not set", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    today = now.date()
    fixture = json.loads(args.fixture.read_text()) if args.fixture else None

    symbols = sorted({s for wl in cfg["watchlist"] for s in wl["tickers"]})
    events: dict[str, list[dict]] = {}
    failures = []
    for i, sym in enumerate(symbols):
        try:
            if fixture is not None:
                rows = [r for r in fixture if r.get("symbol") == sym]
            else:
                if i:
                    _time.sleep(REQUEST_PAUSE)
                rows = fetch_earnings(sym, today - timedelta(days=HISTORY_DAYS),
                                      today + timedelta(days=args.days_ahead), key)
        except FetchError as e:
            failures.append(str(e))
            continue
        if not rows:
            print(f"warning: no earnings data at all for {sym} (typo, delisted, or unsupported ticker?)",
                  file=sys.stderr)
        for r in rows:
            # Finnhub may answer with the home listing (TSM -> 2330.TW). Show the ticker the
            # user asked for, and treat the session as unreliable (home-market hours).
            if (r.get("symbol") or sym).upper() != sym:
                r["foreign"] = True
            r["symbol"] = sym
        events[sym] = normalise(rows, today, args.days_back)

    if failures:
        print("not publishing, fetch failed for: " + "; ".join(failures), file=sys.stderr)
        return 1
    if not any(events.values()):
        print("not publishing: no events at all (check API key and tickers)", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    for wl in cfg["watchlist"]:
        evs = [e for s in wl["tickers"] for e in events.get(s, [])]
        (args.out / wl["file"]).write_text(build_calendar(wl, evs, now), newline="")
        tbd = sum(e["session"] == "TBD" for e in evs)
        print(f"{wl['file']}: {len(evs)} events, {len(wl['tickers'])} tickers, {tbd} with unknown session")
    return 0


if __name__ == "__main__":
    sys.exit(main())
