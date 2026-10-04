import json
import sys
from datetime import date, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

TODAY = date(2026, 10, 5)


# ── headline scoring ─────────────────────────────────────────────────────────

def test_headline_scores():
    from news_sentiment import score_headline
    assert score_headline("Morgan Stanley upgrades Nvidia, raises price target") == 1.0
    assert score_headline("Company misses estimates, cuts guidance amid lawsuit") == -1.0
    assert score_headline("Shares surge after earnings beat despite weak outlook") == pytest.approx(1 / 3)
    assert score_headline("Stock jumps but analysts warn of risks") == pytest.approx(-1 / 3)
    assert score_headline("Company holds annual shareholder meeting") == 0.0


def test_negation_flips_the_word():
    from news_sentiment import score_headline
    assert score_headline("Company fails to beat estimates") == -1.0
    assert score_headline("No layoffs planned, CEO says") == 1.0


def test_news_summary_ignores_headlines_with_no_listed_words():
    from news_sentiment import news_summary
    s = news_summary(["Analyst upgrades stock", "Firm downgrades stock", "Shares surge on record profit",
                      "Company to present at conference"])
    assert s["news_n"] == 4 and s["news_scored"] == 3
    assert s["news_score"] == pytest.approx(1 / 3, abs=1e-3)
    assert news_summary([])["news_score"] is None


# ── social ───────────────────────────────────────────────────────────────────

def test_stocktwits_tags_and_ratio():
    from news_sentiment import parse_stocktwits_tags, social_summary
    payload = {"messages": [{"entities": {"sentiment": {"basic": "Bullish"}}},
                            {"entities": {"sentiment": {"basic": "Bearish"}}},
                            {"entities": {"sentiment": None}}, {"entities": {}}, {},
                            {"entities": {"sentiment": {"basic": "<script>"}}}]}
    tags = parse_stocktwits_tags(payload)
    assert tags == ["Bullish", "Bearish", None, None, None, None]
    s = social_summary(tags + ["Bullish", "Bullish"])
    assert (s["st_bullish"], s["st_bearish"], s["st_bull_ratio"]) == (3, 1, 0.75)
    assert social_summary([None, None])["st_bull_ratio"] is None
    assert parse_stocktwits_tags(None) == [] and parse_stocktwits_tags({"messages": None}) == []


# ── yes/no signals ───────────────────────────────────────────────────────────

def test_signals_need_enough_data_and_none_is_not_false():
    from news_sentiment import to_signals
    thin = to_signals({"news_score": 0.9, "news_scored": 2, "st_bull_ratio": 1.0, "st_bullish": 3, "st_bearish": 0})
    assert thin == {"news_positive": None, "news_negative": None, "social_bullish": None, "social_bearish": None}
    good = to_signals({"news_score": 0.4, "news_scored": 9, "st_bull_ratio": 0.9, "st_bullish": 9, "st_bearish": 1})
    assert good == {"news_positive": True, "news_negative": False, "social_bullish": True, "social_bearish": False}
    bad = to_signals({"news_score": -0.5, "news_scored": 9, "st_bull_ratio": 0.4, "st_bullish": 4, "st_bearish": 6})
    assert bad == {"news_positive": False, "news_negative": True, "social_bullish": False, "social_bearish": True}


# ── feed parsing ─────────────────────────────────────────────────────────────

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Nvidia upgraded by Morgan Stanley - CNBC</title><pubDate>Fri, 02 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>Old story about Nvidia - Reuters</title><pubDate>Mon, 01 Jun 2026 12:00:00 GMT</pubDate></item>
<item><title>No date here - Blog</title></item>
</channel></rss>"""


def test_rss_titles_are_recent_and_stripped_of_publisher():
    from news_sentiment import parse_rss_titles
    assert parse_rss_titles(RSS, datetime(2026, 9, 28)) == ["Nvidia upgraded by Morgan Stanley"]


def test_feed_with_a_dtd_or_entities_is_refused():
    from news_sentiment import parse_rss_titles
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss><channel></channel></rss>'
    with pytest.raises(ValueError):
        parse_rss_titles(bomb, datetime(2026, 9, 28))
    with pytest.raises(ValueError):
        parse_rss_titles(b"<rss>" + b"x" * 4_000_000 + b"</rss>", datetime(2026, 9, 28))


# ── one symbol, and the daily scan ───────────────────────────────────────────

def test_sentiment_for_survives_a_failing_source():
    from news_sentiment import sentiment_for

    def broken(symbol):
        raise RuntimeError("blocked")
    out = sentiment_for("NVDA", fetch_news=broken, fetch_social=lambda s: ["Bullish"] * 9 + ["Bearish"])
    assert out["news_score"] is None and out["news_positive"] is None
    assert out["st_bull_ratio"] == 0.9 and out["social_bullish"] is True
    assert sentiment_for("NVDA", fetch_news=broken, fetch_social=broken)["social_bullish"] is None


def test_scan_replaces_todays_rows_and_keeps_history(tmp_path):
    from news_sentiment import lookup, scan
    path = tmp_path / "sentiment_log.json"
    path.write_text(json.dumps([{"date": "2026-10-02", "symbol": "AMD", "news_score": 0.2},
                                {"date": "2025-01-01", "symbol": "OLD", "news_score": 0.1},
                                {"date": "2026-10-05", "symbol": "AMD", "news_score": -9}]))
    fake = lambda symbol: {"news_score": 0.5, "news_scored": 7, "st_bull_ratio": None, "news_positive": True}
    summary = scan(["AMD", "XOM"], TODAY, path=path, score=fake, pause=0)
    rows = json.loads(path.read_text())
    assert summary == {"symbols": 2, "with_news": 2, "with_social": 0}
    assert [(r["date"], r["symbol"]) for r in rows] == [("2026-10-02", "AMD"), ("2026-10-05", "AMD"), ("2026-10-05", "XOM")]
    assert lookup("AMD", TODAY, path)["news_score"] == 0.5          # today's stale row was replaced
    assert lookup("ZZZ", TODAY, path) is None


def test_scan_symbols_only_accepts_ticker_shaped_values(tmp_path, monkeypatch):
    import news_sentiment as ns
    monkeypatch.setattr(ns, "DOCS_DATA", tmp_path)
    (tmp_path / "screener.json").write_text(json.dumps({"candidates": [
        {"symbol": "NVDA"}, {"symbol": "../../etc/passwd"}, {"symbol": "nvda"}, {"symbol": None}, {"symbol": "BRK.B"}]}))
    (tmp_path / "latest.json").write_text(json.dumps({"positions": [
        {"symbol": "AMD"}, {"symbol": "NVDA"}, {"symbol": "AMD261218P00400000", "asset_class": "us_option"}]}))
    assert ns.scan_symbols() == ["NVDA", "BRK.B", "AMD"]
