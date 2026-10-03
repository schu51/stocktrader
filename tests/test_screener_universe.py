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
