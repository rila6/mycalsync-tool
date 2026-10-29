import json
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import earnings_ics as e

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
TODAY = NOW.date()
ROWS = [
    {"symbol": "AAPL", "date": "2025-10-30", "hour": "amc", "year": 2025, "quarter": 4},
    {"symbol": "AAPL", "date": "2026-10-30", "hour": "", "year": 2026, "quarter": 4, "epsEstimate": 1.8},
    {"symbol": "JPM", "date": "2026-11-13", "hour": "bmo", "year": 2026, "quarter": 4},
    {"symbol": "XYZ", "date": "2026-11-14", "hour": "", "year": 2026, "quarter": 4},
]


def evs():
    return e.normalise([dict(r) for r in ROWS], TODAY, 7)


class T(unittest.TestCase):
    def wl(self, **kw):
        return {"name": "t", "mode": "block", "reminders": [e.parse_reminder("1d"), e.parse_reminder("30m")], **kw}

    def test_reminders(self):
        self.assertEqual(e.parse_reminder("2h"), "-PT2H")
        self.assertEqual(e.parse_reminder("1d"), "-P1D")
        with self.assertRaises(ValueError):
            e.parse_reminder("soon")

    def test_normalise_infers_session_and_drops_old(self):
        by = {x["symbol"]: x for x in evs()}
        self.assertEqual(len(evs()), 3)  # last year's AAPL row only feeds inference
        self.assertEqual((by["AAPL"]["session"], by["AAPL"]["inferred"]), ("AMC", True))
        self.assertEqual((by["JPM"]["session"], by["JPM"]["inferred"]), ("BMO", False))
        self.assertEqual(by["XYZ"]["session"], "TBD")

    def test_foreign_listing_is_date_only_under_requested_ticker(self):
        fx = [{"symbol": "TSM", "date": "2026-11-05", "hour": "bmo", "year": 2026, "quarter": 4},
              {"symbol": "AAPL", "date": "2026-11-05", "hour": "amc", "year": 2026, "quarter": 4}]
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d, "c.toml")
            cfg.write_text('[[watchlist]]\nname="a"\nfile="a-1.ics"\ntickers=["TSM","AAPL"]\n')
            raw = [dict(fx[0], symbol="2330.TW"), fx[1]]  # Finnhub answers TSM with the Taiwan listing
            with mock.patch.dict("os.environ", {"FINNHUB_API_KEY": "k"}), \
                    mock.patch("sys.argv", ["x", "--config", str(cfg), "--out", str(Path(d, "o"))]), \
                    mock.patch.object(e, "fetch_earnings",
                                      side_effect=lambda s, *a: [raw[0]] if s == "TSM" else [raw[1]]), \
                    mock.patch.object(e, "REQUEST_PAUSE", 0):
                self.assertEqual(e.main(), 0)
            ics = Path(d, "o", "a-1.ics").read_text()
        self.assertIn("SUMMARY:TSM earnings [date only]", ics)
        self.assertNotIn("2330", ics)
        self.assertIn("DTSTART;VALUE=DATE:20261105", ics)
        self.assertIn("SUMMARY:AAPL earnings [AMC]", ics)

    def test_moved_date_replaces_same_quarter(self):
        rows = [{"symbol": "A", "date": "2026-11-01", "hour": "bmo", "year": 2026, "quarter": 4},
                {"symbol": "A", "date": "2026-11-05", "hour": "bmo", "year": 2026, "quarter": 4}]
        out = e.normalise(rows, TODAY, 7)
        self.assertEqual([x["date"] for x in out], ["2026-11-05"])

    def test_block_times_and_tags(self):
        ics = e.build_calendar(self.wl(), evs(), NOW)
        self.assertIn("SUMMARY:AAPL earnings [AMC?]", ics)
        self.assertIn("SUMMARY:JPM earnings [BMO]", ics)
        self.assertIn("DTSTART:20261030T200000Z", ics)  # 16:00 EDT
        self.assertIn("DTSTART:20261113T120000Z", ics)  # 07:00 EST (after DST ends)
        self.assertIn("DTEND:20261113T143000Z", ics)  # 09:30 EST
        self.assertIn("DTSTART;VALUE=DATE:20261114", ics)  # TBD stays all-day, no invented time
        self.assertIn("[TBD]", ics)
        self.assertEqual(ics.count("BEGIN:VALARM"), 6)
        self.assertIn("TRIGGER:-P1D", ics)

    def test_allday_and_override(self):
        ics = e.build_calendar(self.wl(mode="allday"), evs(), NOW)
        self.assertNotIn("DTSTART:2", ics)
        ics = e.build_calendar(self.wl(amc_start="17:00"), evs(), NOW)
        self.assertIn("DTSTART:20261030T210000Z", ics)

    def test_stable_uid_crlf_and_fold(self):
        ics = e.build_calendar(self.wl(), evs(), NOW)
        self.assertIn("UID:AAPL-2026Q4@earnings-ics", ics)
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))
        self.assertTrue(all(len(l.encode()) <= 75 for l in ics.split("\r\n")))

    def test_retry_then_success_and_no_key_in_error(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value = mock.MagicMock(read=lambda: b'{"earningsCalendar": [{"symbol": "A"}]}')
        err = urllib.error.HTTPError("http://x?token=SECRET", 429, "slow", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]), mock.patch("time.sleep"):
            self.assertEqual(e.fetch_earnings("A", date(2026, 1, 1), date(2026, 2, 1), "SECRET"), [{"symbol": "A"}])
        with mock.patch("urllib.request.urlopen", side_effect=err), mock.patch("time.sleep"):
            with self.assertRaises(e.FetchError) as cm:
                e.fetch_earnings("A", date(2026, 1, 1), date(2026, 2, 1), "SECRET")
        self.assertNotIn("SECRET", str(cm.exception))

    def test_main_publishes_nothing_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d, "c.toml")
            cfg.write_text('[[watchlist]]\nname="a"\nfile="a-1.ics"\ntickers=["AAPL"]\n')
            out = Path(d, "out")
            argv = ["x", "--config", str(cfg), "--out", str(out)]
            with mock.patch.dict("os.environ", {"FINNHUB_API_KEY": "k"}), mock.patch("sys.argv", argv), \
                    mock.patch.object(e, "fetch_earnings", side_effect=e.FetchError("AAPL: HTTP 500")):
                self.assertEqual(e.main(), 1)
            self.assertFalse(out.exists())
            fx = Path(d, "fx.json")
            fx.write_text(json.dumps(ROWS))
            with mock.patch("sys.argv", argv + ["--fixture", str(fx)]):
                self.assertEqual(e.main(), 0)
            self.assertTrue(Path(out, "a-1.ics").exists())

    def test_config_rejects_duplicate_files(self):
        toml = '[[watchlist]]\nfile="a.ics"\ntickers=["A"]\n[[watchlist]]\nfile="a.ics"\ntickers=["B"]\n'
        with mock.patch.dict("os.environ", {"WATCHLISTS_TOML": toml}):
            with self.assertRaises(ValueError):
                e.load_config(Path("unused"))


if __name__ == "__main__":
    unittest.main()
