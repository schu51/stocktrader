from datetime import date, datetime, timezone

import finnhub_feed as fh


def test_no_key_returns_empty():
    assert fh.earnings_calendar(date(2026, 11, 13), date(2026, 11, 19), key="") == {}
    assert fh.company_news("AAPL", date(2026, 11, 5), date(2026, 11, 12), key="") == []


def test_parse_calendar_window_and_earliest():
    payload = {"earningsCalendar": [
        {"symbol": "abc", "date": "2026-11-18", "hour": "amc", "epsEstimate": 0.12},
        {"symbol": "ABC", "date": "2026-11-14", "hour": "bmo", "epsEstimate": 0.10},
        {"symbol": "OUT", "date": "2026-12-30"},
        {"symbol": "BAD", "date": "not a date"},
        {"symbol": "", "date": "2026-11-15"},
    ]}
    cal = fh.parse_calendar(payload, date(2026, 11, 13), date(2026, 11, 19))
    assert cal == {"ABC": {"date": "2026-11-14", "hour": "bmo", "eps_estimate": 0.10}}
    assert fh.parse_calendar(None, date(2026, 11, 13), date(2026, 11, 19)) == {}


def test_parse_news_filters_and_sorts():
    now = datetime(2026, 11, 12, 18, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 11, 12, tzinfo=timezone.utc).timestamp()
    t2 = datetime(2026, 11, 12, 12, tzinfo=timezone.utc).timestamp()
    future = datetime(2026, 11, 13, 12, tzinfo=timezone.utc).timestamp()
    payload = [
        {"headline": "Older story", "source": "Reuters", "datetime": t1},
        {"headline": "Newer story", "source": "CNBC", "datetime": t2},
        {"headline": "From the future", "source": "X", "datetime": future},
        {"headline": "", "datetime": t2},
        {"headline": "No time"},
    ]
    out = fh.parse_news(payload, now)
    assert [h["title"] for h in out] == ["Newer story", "Older story"]
    assert out[0]["publisher"] == "CNBC" and out[0]["source"] == "finnhub"
    assert fh.parse_news({"error": "x"}, now) == []


def test_merge_headlines_dedupes_titles():
    primary = [{"title": "Acme wins contract", "published": "2026-11-12T10:00:00+00:00"}]
    extra = [{"title": "acme  wins contract", "published": "2026-11-12T11:00:00+00:00"},
             {"title": "Acme beats estimates", "published": "2026-11-12T12:00:00+00:00"}]
    merged = fh.merge_headlines(primary, extra)
    assert [h["title"] for h in merged] == ["Acme beats estimates", "Acme wins contract"]
