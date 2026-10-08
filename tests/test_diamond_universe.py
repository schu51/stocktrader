# tests/test_diamond_universe.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from diamond_universe import excluded_sic, is_common_share, listed_companies, size_and_volume, size_failures


def test_only_common_shares_on_the_two_main_exchanges():
    rows = [[1, "Alpha Inc", "ALPH", "Nasdaq"], [2, "Beta Corp", "BETA", "NYSE"],
            [3, "Gamma Ltd", "GAMM", "OTC"], [4, "Delta Acquisition Corp Warrant", "DLTAW", "Nasdaq"],
            [5, "Epsilon Units", "EPS-UN", "NYSE"], [6, "Zeta Inc 5% Preferred", "ZETA-PA", "NYSE"],
            [7, "Eta Inc", None, "NYSE"]]
    assert sorted(listed_companies(rows)) == [1, 2]


def test_two_share_classes_give_one_company_with_its_first_ticker():
    rows = [[9, "Dual Class Inc", "DUAL", "Nasdaq"], [9, "Dual Class Inc", "DUALB", "Nasdaq"]]
    assert listed_companies(rows) == {9: {"ticker": "DUAL", "name": "Dual Class Inc", "exchange": "Nasdaq"}}


def test_share_class_suffixes():
    assert is_common_share("BRK-B", "Berkshire Hathaway Inc")          # a class of common stock
    assert not is_common_share("ABCDW", "Abcd Corp Warrants")
    assert not is_common_share("ABCD-WT", "Abcd Corp")
    assert not is_common_share("ABCDU", "Abcd Acquisition Corp Unit")
    assert not is_common_share("ABCDR", "Abcd Corp Rights")
    assert is_common_share("UBER", "Uber Technologies Inc")             # ends in R, is not a right


def test_size_and_volume():
    closes, volumes = [20.0] * 60, [400_000.0] * 60
    s = size_and_volume(100e6, closes, volumes)
    assert s == {"price": 20.0, "market_value": 2e9, "dollar_volume": 8e6}
    assert size_failures(s) == []
    assert size_failures(size_and_volume(10e6, closes, volumes)) == ["too_small"]       # $200M
    assert size_failures(size_and_volume(600e6, closes, volumes)) == ["too_big"]        # $12B
    assert size_failures(size_and_volume(100e6, closes, [100_000.0] * 60)) == ["illiquid"]
    assert size_failures(size_and_volume(1e9, [4.0] * 60, [5e6] * 60)) == ["penny"]


def test_unknown_price_or_shares_is_a_failure_not_a_crash():
    assert size_and_volume(None, [20.0] * 60, [1e6] * 60) is None
    assert size_and_volume(100e6, [], []) is None
    assert size_and_volume(100e6, [float("nan")] * 60, [1e6] * 60) is None
    assert size_and_volume(100e6, [20.0] * 10, [1e6] * 10) is None      # too little history to judge volume
    assert size_failures(None) == ["price_unknown"]


def test_financial_industries_are_excluded_and_unknown_is_kept():
    assert excluded_sic(6021) and excluded_sic("6798") and excluded_sic(6000) and excluded_sic(6799)
    assert not excluded_sic(7372) and not excluded_sic(5999) and not excluded_sic(6800)
    assert not excluded_sic(None) and not excluded_sic("")
