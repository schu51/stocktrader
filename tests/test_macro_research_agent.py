import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_parse_theses_plain_array():
    from macro_research_agent import _parse_theses
    assert _parse_theses('[{"theme": "grid buildout"}]') == [{"theme": "grid buildout"}]


def test_parse_theses_skips_citation_brackets_and_prose():
    from macro_research_agent import _parse_theses
    text = 'Per the Fed minutes [1] and BLS data [2], here are the theses:\n[{"theme": "a"}, {"theme": "b"}]\nDone.'
    assert [t["theme"] for t in _parse_theses(text)] == ["a", "b"]


def test_parse_theses_code_fence():
    from macro_research_agent import _parse_theses
    assert _parse_theses('```json\n[{"theme": "a"}]\n```') == [{"theme": "a"}]


def test_parse_theses_nothing_usable():
    from macro_research_agent import _parse_theses
    assert _parse_theses("I could not find reliable sources [1].") == []
    assert _parse_theses("") == []
    assert _parse_theses("[]") == []


def test_main_fails_visibly_without_api_key(tmp_path, monkeypatch):
    import macro_research_agent as mra
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(mra, "BRIEF_FILE", tmp_path / "macro_brief.json")
    assert mra.main() == 1
    assert json.loads((tmp_path / "macro_brief.json").read_text())["error"] == "missing ANTHROPIC_API_KEY"


def test_main_fails_visibly_when_generation_errors(tmp_path, monkeypatch):
    import macro_research_agent as mra
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(mra, "BRIEF_FILE", tmp_path / "macro_brief.json")
    monkeypatch.setattr(mra, "THESES_FILE", tmp_path / "theses.json")
    monkeypatch.setattr(mra, "_gather_context", lambda: "")

    def boom(context, existing=None):
        raise RuntimeError("api down")
    monkeypatch.setattr(mra, "_generate", boom)
    assert mra.main() == 1
    assert not (tmp_path / "theses.json").exists()   # register left untouched


def test_main_succeeds_when_generation_returns_nothing(tmp_path, monkeypatch):
    import macro_research_agent as mra
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(mra, "BRIEF_FILE", tmp_path / "macro_brief.json")
    monkeypatch.setattr(mra, "THESES_FILE", tmp_path / "theses.json")
    monkeypatch.setattr(mra, "DOCS_DATA", tmp_path)
    monkeypatch.setattr(mra, "_gather_context", lambda: "")
    monkeypatch.setattr(mra, "_generate", lambda context, existing=None: [])
    assert mra.main() == 0


# ── weekly re-validation and duplicate control ──────────────────────────────

def _thesis(**over):
    th = {
        "id": "TH-2026-0001", "theme": "Oil services over majors", "status": "active",
        "beneficiary_sectors": ["energy", "industrials"], "second_order": "services",
        "consensus_names_excluded": ["XOM", "CVX"],
        "conviction_breakdown": {"source_corroboration": 0.6, "causal_directness": 0.6,
                                 "non_consensus": 0.6, "invalidation_clarity": 0.6},
        "conviction": 0.6, "invalidation_condition": "Brent falls 15%", "horizon": "2099-12-31",
        "sources": ["https://reuters.com/x"], "created_at": "2026-09-01", "last_validated": "2026-09-01",
    }
    th.update(over)
    return th


def _setup(mra, tmp_path, monkeypatch, theses, verdicts=None, candidates=None, revalidate_error=None):
    import json as _json
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(mra, "DOCS_DATA", tmp_path)
    monkeypatch.setattr(mra, "BRIEF_FILE", tmp_path / "macro_brief.json")
    monkeypatch.setattr(mra, "THESES_FILE", tmp_path / "theses.json")
    (tmp_path / "theses.json").write_text(_json.dumps({"generated_at": None, "theses": theses}))
    monkeypatch.setattr(mra, "_gather_context", lambda: "")
    monkeypatch.setattr(mra, "_generate", lambda context, existing=None: list(candidates or []))

    def fake_revalidate(active):
        if revalidate_error:
            raise RuntimeError(revalidate_error)
        return verdicts or {}
    monkeypatch.setattr(mra, "_revalidate", fake_revalidate)
    return lambda: _json.loads((tmp_path / "theses.json").read_text())["theses"]


def test_revalidation_refreshes_theses_that_still_hold(tmp_path, monkeypatch):
    from datetime import date
    import macro_research_agent as mra
    read = _setup(mra, tmp_path, monkeypatch, [_thesis()],
                  verdicts={"TH-2026-0001": {"invalidated": False, "evidence": "Brent steady"}})
    assert mra.main() == 0
    th = read()[0]
    assert th["status"] == "active"
    assert th["last_validated"] == date.today().isoformat()


def test_revalidation_retires_an_invalidated_thesis(tmp_path, monkeypatch):
    import macro_research_agent as mra
    from macro_thesis import load_live_theses
    read = _setup(mra, tmp_path, monkeypatch, [_thesis()],
                  verdicts={"TH-2026-0001": {"invalidated": True, "evidence": "Brent fell 20%"}})
    assert mra.main() == 0
    th = read()[0]
    assert th["status"] == "invalidated"
    assert th["invalidation_evidence"] == "Brent fell 20%"
    assert load_live_theses(tmp_path / "theses.json") == []


def test_thesis_without_a_verdict_is_left_untouched(tmp_path, monkeypatch):
    import macro_research_agent as mra
    read = _setup(mra, tmp_path, monkeypatch, [_thesis()], verdicts={})
    mra.main()
    th = read()[0]
    assert th["status"] == "active" and th["last_validated"] == "2026-09-01"


def test_failed_revalidation_changes_nothing_and_fails_visibly(tmp_path, monkeypatch):
    import json as _json
    import macro_research_agent as mra
    read = _setup(mra, tmp_path, monkeypatch, [_thesis()], revalidate_error="api down")
    assert mra.main() == 1
    th = read()[0]
    assert th["status"] == "active" and th["last_validated"] == "2026-09-01"
    assert "api down" in _json.loads((tmp_path / "macro_brief.json").read_text())["revalidation_error"]


def test_duplicate_candidate_is_rejected(tmp_path, monkeypatch):
    import json as _json
    import macro_research_agent as mra
    dup = _thesis(theme="Oilfield services beat integrated majors", consensus_names_excluded=["XOM", "CVX", "COP"])
    dup.pop("id")
    new = _thesis(theme="Regional banks", beneficiary_sectors=["financials"], consensus_names_excluded=["JPM"])
    new.pop("id")
    read = _setup(mra, tmp_path, monkeypatch, [_thesis()],
                  verdicts={"TH-2026-0001": {"invalidated": False, "evidence": ""}}, candidates=[dup, new])
    assert mra.main() == 0
    themes = [t["theme"] for t in read()]
    assert themes == ["Oil services over majors", "Regional banks"]
    brief = _json.loads((tmp_path / "macro_brief.json").read_text())
    assert "duplicate of TH-2026-0001" in brief["rejected"][0]["reason"]


def test_parse_verdicts_requires_explicit_boolean():
    from macro_research_agent import _parse_verdicts
    text = '[{"id": "TH-1", "invalidated": true, "evidence": "x"}, {"id": "TH-2", "invalidated": "maybe"}, {"id": "TH-3", "invalidated": false}]'
    v = _parse_verdicts(text)
    assert v["TH-1"]["invalidated"] is True and v["TH-3"]["invalidated"] is False
    assert "TH-2" not in v
