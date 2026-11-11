"""
Ontology
========
One connected picture of what the bot knows: companies, the securities they
issue, sectors and industries, themes, macro theses and market events, with
the relations between them. Before this they were separate lists of strings,
so a thesis about "grid equipment" could not find the theme "AI power" or the
stocks in it.

Nodes (id: type:key)
  security:NVDA            a listed security (canonical ticker; stable id from identity.py when known)
  company:1045810          a company (SEC CIK; company:<TICKER> when the CIK is unknown)
  sector:technology        industry:semiconductors
  theme:ai_compute         thesis:TH-2026-0003
  event:CRWV@2026-11-11    a 20%+ day (big_movers.py), with its catalyst

Edges (from -rel-> to)
  security  ISSUED_BY   company
  company   IN_SECTOR   sector          company IN_INDUSTRY industry
  security  IN_THEME    theme           (themes.json members)
  thesis    BENEFITS    sector          (its beneficiary_sectors)
  thesis    ABOUT       theme           (2+ of the theme's keywords in the thesis text, and at least 40% as many
                                         as its best-matching theme; the words are kept as evidence)
  thesis    MENTIONS    theme           (a weaker keyword match: shown, never used to find stocks)
  thesis    EXCLUDES    security        (its consensus names)
  event     MOVED       security

Writes docs/data/graph.json every evening (pit.yml). Read it with Graph:

    g = ontology.Graph.load()
    g.stocks_for_thesis("TH-2026-0003")   # [{symbol, via: ["theme:ai_power", ...]}]
    g.themes_of("VRT"); g.theses_for("VRT"); g.peers("CRWV")

The graph only describes; nothing in it changes a trade. The screener's macro
multiplier still works by sector, as before.
"""

import argparse
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import contracts
import identity

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
GRAPH_FILE = DOCS_DATA / "graph.json"
EVENT_DAYS = 120
ABOUT_MIN_HITS = 2          # distinct theme keywords a thesis must use to be ABOUT the theme
ABOUT_SHARE_OF_BEST = 0.4   # ... and at least this share of its best-matching theme's hits


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def keyword_hits(text: str, keywords: Iterable[str]) -> List[str]:
    """Keywords found in text, matched at a word start (so 'refin' finds 'refiners')."""
    t = str(text or "").lower()
    return [k for k in keywords if re.search(r"(?<![a-z0-9])" + re.escape(k.lower()), t)]


def build(universe: Dict, themes: Dict, theses: Dict, companies: Dict, movers: Dict,
          today: Optional[date] = None) -> Dict:
    """The graph from the shared files' contents (pure)."""
    today = today or date.today()
    nodes: Dict[str, Dict] = {}
    edges: List[Dict] = []

    def node(nid: str, **attrs) -> str:
        nodes.setdefault(nid, {"type": nid.split(":", 1)[0]}).update({k: v for k, v in attrs.items() if v is not None})
        return nid

    def edge(a: str, rel: str, b: str, **attrs):
        edges.append(dict({"from": a, "rel": rel, "to": b}, **attrs))

    def security(sym: str) -> str:
        sym = identity.canonical(sym)
        sid = node(f"security:{sym}", label=sym, security_id=identity.security_id(sym))
        if "issued_by" not in nodes[sid]:
            cik = identity.cik(sym)
            comp = node(f"company:{cik}" if cik else f"company:{sym}", cik=cik,
                        label=(companies.get(sym) or {}).get("name") or (identity.security(sym) or {}).get("name") or sym)
            nodes[sid]["issued_by"] = comp
            edge(sid, "ISSUED_BY", comp)
        return sid

    # Companies, sectors, industries: the universe, enriched by companies.json
    for sym, sector in (universe.get("tickers") or {}).items():
        sid = security(sym)
        comp = nodes[sid]["issued_by"]
        if sector:
            edge(comp, "IN_SECTOR", node(f"sector:{_slug(sector)}", label=sector))
    for sym, info in (companies or {}).items():
        if not identity.is_ticker(sym):
            continue
        comp = nodes[security(sym)]["issued_by"]
        if info.get("industry"):
            edge(comp, "IN_INDUSTRY", node(f"industry:{_slug(info['industry'])}", label=info["industry"]))
        if info.get("sector") and not any(e["from"] == comp and e["rel"] == "IN_SECTOR" for e in edges):
            edge(comp, "IN_SECTOR", node(f"sector:{_slug(info['sector'])}", label=info["sector"]))

    # Themes
    theme_kw: Dict[str, List[str]] = {}
    for slug, t in (themes.get("themes") or {}).items():
        tid = node(f"theme:{slug}", label=t.get("description") or slug)
        theme_kw[tid] = list(t.get("keywords") or [])
        for m in t.get("members") or []:
            if identity.is_ticker(m):
                edge(security(m), "IN_THEME", tid)

    # Theses
    for th in theses.get("theses") or []:
        if th.get("status") not in (None, "live", "active"):
            continue
        hid = node(f"thesis:{th['id']}", label=th.get("theme"), horizon=th.get("horizon"),
                   conviction=th.get("conviction"), last_validated=th.get("last_validated"))
        for s in th.get("beneficiary_sectors") or []:
            edge(hid, "BENEFITS", node(f"sector:{_slug(s)}", label=s))
        text = " ".join(str(th.get(k) or "") for k in ("theme", "causal_chain", "second_order"))
        hits = {tid: keyword_hits(text, kws) for tid, kws in theme_kw.items()}
        best = max((len(h) for h in hits.values()), default=0)
        for tid, h in hits.items():
            if not h:
                continue
            strong = len(h) >= ABOUT_MIN_HITS and len(h) >= ABOUT_SHARE_OF_BEST * best
            edge(hid, "ABOUT" if strong else "MENTIONS", tid, evidence=h)
        for x in th.get("consensus_names_excluded") or []:
            if identity.is_ticker(x):
                edge(hid, "EXCLUDES", security(x))

    # Big-move events (last EVENT_DAYS)
    cutoff = (today - timedelta(days=EVENT_DAYS)).isoformat()
    for ev in (movers.get("events") or []):
        if ev.get("date", "") < cutoff or not identity.is_ticker(ev.get("symbol")):
            continue
        eid = node(f"event:{identity.canonical(ev['symbol'])}@{ev['date']}", label=f"{ev['symbol']} {ev.get('ret', 0):+.0%}",
                   date=ev["date"], catalyst=ev.get("catalyst"), ret=ev.get("ret"))
        edge(eid, "MOVED", security(ev["symbol"]))

    counts: Dict[str, int] = {}
    for n in nodes.values():
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counts": counts, "nodes": nodes, "edges": edges}


class Graph:
    def __init__(self, data: Dict):
        self.nodes: Dict[str, Dict] = data.get("nodes") or {}
        self.edges: List[Dict] = data.get("edges") or []
        self._out: Dict[str, List[Dict]] = {}
        self._in: Dict[str, List[Dict]] = {}
        for e in self.edges:
            self._out.setdefault(e["from"], []).append(e)
            self._in.setdefault(e["to"], []).append(e)

    @classmethod
    def load(cls, path: Path = GRAPH_FILE) -> "Graph":
        return cls(contracts.read(path, default={}))

    def out(self, nid: str, rel: Optional[str] = None) -> List[Dict]:
        return [e for e in self._out.get(nid, []) if rel is None or e["rel"] == rel]

    def into(self, nid: str, rel: Optional[str] = None) -> List[Dict]:
        return [e for e in self._in.get(nid, []) if rel is None or e["rel"] == rel]

    def themes_of(self, symbol: str) -> List[str]:
        return sorted(e["to"].split(":", 1)[1] for e in self.out(f"security:{identity.canonical(symbol)}", "IN_THEME"))

    def sector_of(self, symbol: str) -> Optional[str]:
        sec = self.nodes.get(f"security:{identity.canonical(symbol)}") or {}
        es = self.out(sec.get("issued_by", ""), "IN_SECTOR")
        return es[0]["to"].split(":", 1)[1] if es else None

    def stocks_for_thesis(self, thesis_id: str, by_sector: bool = False) -> List[Dict]:
        """
        Stocks a thesis points at, with the path that links them: members of the
        themes it is ABOUT (and, if by_sector, every company in a sector it
        BENEFITS). Consensus names it EXCLUDES are left out.
        """
        hid = f"thesis:{thesis_id}"
        excluded = {e["to"] for e in self.out(hid, "EXCLUDES")}
        found: Dict[str, List[str]] = {}
        for e in self.out(hid, "ABOUT"):
            for m in self.into(e["to"], "IN_THEME"):
                found.setdefault(m["from"], []).append(e["to"])
        if by_sector:
            for e in self.out(hid, "BENEFITS"):
                for c in self.into(e["to"], "IN_SECTOR"):
                    for s in self.into(c["from"], "ISSUED_BY"):
                        found.setdefault(s["from"], []).append(e["to"])
        return sorted(({"symbol": s.split(":", 1)[1], "via": sorted(set(v))}
                       for s, v in found.items() if s not in excluded), key=lambda x: x["symbol"])

    def theses_for(self, symbol: str) -> List[Dict]:
        """Theses that reach a stock through its themes or its sector."""
        sid = f"security:{identity.canonical(symbol)}"
        out = []
        for hid, h in self.nodes.items():
            if h["type"] != "thesis":
                continue
            hits = self.stocks_for_thesis(hid.split(":", 1)[1], by_sector=True)
            row = next((x for x in hits if f"security:{x['symbol']}" == sid), None)
            if row:
                out.append({"thesis": hid.split(":", 1)[1], "label": h.get("label"), "via": row["via"]})
        return out

    def peers(self, symbol: str) -> List[str]:
        """Other stocks sharing a theme with `symbol`."""
        sid = f"security:{identity.canonical(symbol)}"
        out = set()
        for e in self.out(sid, "IN_THEME"):
            out |= {m["from"].split(":", 1)[1] for m in self.into(e["to"], "IN_THEME") if m["from"] != sid}
        return sorted(out)


def run(path: Path = GRAPH_FILE) -> Dict:
    read = lambda name, default: contracts.read(DOCS_DATA / name, default=default)
    graph = build(read("universe.json", {}), read("themes.json", {}), read("theses.json", {}),
                  read("companies.json", {}), read("big_movers_log.json", {}))
    contracts.write(path, graph, compact=True)
    logger.info(f"Graph: {graph['counts']}, {len(graph['edges'])} edges")
    return graph


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the knowledge graph (docs/data/graph.json)")
    ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
