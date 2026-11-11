"""Knowledge graph: theses, themes, sectors and stocks connected."""
import ontology

UNIVERSE = {"tickers": {"VRT": "industrials", "ETN": "industrials", "NVDA": "technology", "XOM": "energy",
                        "BRK-B": "financials"}}
THEMES = {"themes": {
    "ai_power": {"description": "Power for data centers", "members": ["VRT", "ETN", "OKLO"],
                 "keywords": ["grid", "transformer", "switchgear", "electrification"]},
    "ai_compute": {"description": "AI compute", "members": ["NVDA", "CRWV"], "keywords": ["gpu", "datacenter", "hyperscaler"]},
    "oil_gas": {"description": "Oil", "members": ["XOM"], "keywords": ["oil", "crude"]}}}
THESES = {"theses": [{"id": "TH-1", "status": "live", "theme": "Grid equipment makers benefit",
                      "causal_chain": "Datacenter demand lifts transformer and switchgear orders; oil-driven inflation",
                      "second_order": "grid suppliers", "beneficiary_sectors": ["industrials"],
                      "consensus_names_excluded": ["ETN"], "horizon": "2027-03-31"}]}
MOVERS = {"events": [{"symbol": "CRWV", "date": "2026-11-11", "ret": 0.2, "catalyst": "earnings"}]}


def _graph():
    from datetime import date
    return ontology.Graph(ontology.build(UNIVERSE, THEMES, THESES, {}, MOVERS, today=date(2026, 11, 11)))


def test_a_thesis_finds_its_theme_and_stocks_through_keywords():
    g = _graph()
    about = [e for e in g.out("thesis:TH-1") if e["rel"] == "ABOUT"]
    assert [e["to"] for e in about] == ["theme:ai_power"]
    assert set(about[0]["evidence"]) == {"grid", "transformer", "switchgear"}
    # one weak word for another theme is only a mention
    assert {e["to"] for e in g.out("thesis:TH-1", "MENTIONS")} == {"theme:ai_compute", "theme:oil_gas"}
    # ETN is a consensus name the thesis excludes
    assert [x["symbol"] for x in g.stocks_for_thesis("TH-1")] == ["OKLO", "VRT"]


def test_sector_paths_are_optional_and_labelled():
    rows = {x["symbol"]: x["via"] for x in _graph().stocks_for_thesis("TH-1", by_sector=True)}
    assert rows["VRT"] == ["sector:industrials", "theme:ai_power"] and "ETN" not in rows


def test_lookups_by_stock():
    g = _graph()
    assert g.themes_of("VRT") == ["ai_power"] and g.sector_of("NVDA") == "technology"
    assert g.peers("NVDA") == ["CRWV"]
    assert [t["thesis"] for t in g.theses_for("VRT")] == ["TH-1"]
    assert g.nodes["event:CRWV@2026-11-11"]["catalyst"] == "earnings"
    assert any(e["rel"] == "MOVED" and e["to"] == "security:CRWV" for e in g.edges)


def test_class_shares_are_one_security():
    g = _graph()
    assert "security:BRK-B" in g.nodes and "security:BRK.B" not in g.nodes


def test_keyword_hits_match_word_starts_only():
    assert ontology.keyword_hits("refiners and oilfield services", ["refin", "oil"]) == ["refin", "oil"]
    assert ontology.keyword_hits("maintenance spending", ["ai"]) == []


def test_committed_graph_meets_its_contract():
    import contracts
    assert contracts.validate(ontology.GRAPH_FILE, contracts.read(ontology.GRAPH_FILE)) == []
