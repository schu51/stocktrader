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

    def boom(context):
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
    monkeypatch.setattr(mra, "_generate", lambda context: [])
    assert mra.main() == 0
