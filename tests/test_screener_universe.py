import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup


def _cell(html):
    return BeautifulSoup(f"<table><tr>{html}</tr></table>", "lxml").find("td")


# Finviz ticker cell since July 2026: a logo with the first letter as placeholder text
NEW_CELL = (
    '<td align="left" data-boxover-company="Apple Inc" data-boxover-ticker="AAPL" height="10">'
    '<span class="flex items-center gap-1 pl-0.5">'
    '<a class="company-ticker" href="stock?t=AAPL&amp;ty=c&amp;p=d&amp;b=1"><img alt="AAPL logo"/>'
    '<span>A</span></a><a class="tab-link" href="stock?t=AAPL&amp;ty=c&amp;p=d&amp;b=1">AAPL</a></span></td>'
)
OLD_CELL = '<td align="left"><a class="tab-link" href="quote.ashx?t=MSFT&amp;ty=c&amp;p=d&amp;b=1">MSFT</a></td>'


def test_ticker_from_logo_cell_is_not_doubled():
    from screener import _ticker_from_cell
    assert _cell(NEW_CELL).get_text(strip=True) == "AAAPL"      # what the old scraper read
    assert _ticker_from_cell(_cell(NEW_CELL)) == "AAPL"


def test_ticker_from_plain_cell():
    from screener import _ticker_from_cell
    assert _ticker_from_cell(_cell(OLD_CELL)) == "MSFT"


def test_ticker_falls_back_to_link_when_attribute_missing():
    from screener import _ticker_from_cell
    cell = _cell('<td><a href="stock?t=BRK-B&amp;ty=c"><span>B</span></a><a href="stock?t=BRK-B&amp;ty=c">BRK-B</a></td>')
    assert _ticker_from_cell(cell) == "BRK-B"


def test_ticker_from_empty_cell():
    from screener import _ticker_from_cell
    assert _ticker_from_cell(_cell("<td></td>")) == ""


def test_data_coverage_flags_a_broken_universe():
    from screener import data_coverage
    assert data_coverage(["AAPL", "MSFT", "NVDA", "AMD"], {"AAPL": 0.1, "MSFT": 0.2, "NVDA": 0.3, "AMD": 0.1}) == 1.0
    assert data_coverage(["AAAPL", "MMSFT", "NNVDA", "AMD"], {"AMD": 0.1}) == 0.25
    assert data_coverage([], {}) == 0.0


class _Resp:
    def __init__(self, status, rows=0):
        self.status_code = status
        self.text = "<table>" + "".join(
            f'<tr class="styled-row"><td>1</td><td data-boxover-ticker="T{chr(65 + i // 26)}{chr(65 + i % 26)}">x</td><td>Co</td><td>Technology</td></tr>'
            for i in range(rows)) + "</table>"


class _Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def get(self, url, timeout=None):
        self.calls += 1
        return self.responses.pop(0)


def test_scrape_retries_a_throttled_page_instead_of_stopping(monkeypatch):
    import screener
    monkeypatch.setattr(screener.time, "sleep", lambda s: None)
    session = _Session([_Resp(429), _Resp(200, rows=3)])      # throttled once, then the (last) page
    out = screener._scrape_finviz_index(session, "idx_sp500")
    assert len(out) == 3 and session.calls == 2


def test_scrape_reports_incomplete_when_a_page_keeps_failing(monkeypatch):
    import pytest
    import screener
    monkeypatch.setattr(screener.time, "sleep", lambda s: None)
    session = _Session([_Resp(200, rows=20)] + [_Resp(429)] * screener.FINVIZ_PAGE_ATTEMPTS)
    with pytest.raises(screener.IncompleteScrape):
        screener._scrape_finviz_index(session, "idx_sp500")


def test_universe_falls_back_when_finviz_returns_too_few(monkeypatch):
    import screener
    monkeypatch.setattr(screener, "_fetch_finviz_universe", lambda: {f"AB{c}": "technology" for c in "CDEFGHIJ"} )
    universe = screener.get_universe()
    assert all(sym in universe for sym in list(screener.FALLBACK_UNIVERSE)[:5])


def test_universe_falls_back_when_scrape_is_incomplete(monkeypatch):
    import screener

    def cut_short():
        raise screener.IncompleteScrape("page 12 failed")
    monkeypatch.setattr(screener, "_fetch_finviz_universe", cut_short)
    universe = screener.get_universe()
    assert all(sym in universe for sym in list(screener.FALLBACK_UNIVERSE)[:5])
