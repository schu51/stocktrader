"""
Security identity
=================
One place that says what a symbol is. Before this, "BRK-B" vs "BRK.B" was
converted by hand in eleven places and a renamed ticker looked like a new stock.

Spellings (pure, no data needed):
    canonical("brk.b")  -> "BRK-B"   how every file in docs/data writes it (and Yahoo)
    alpaca("BRK-B")     -> "BRK.B"   Alpaca orders and market data, SEC tickers, Wikidata
    yahoo("BRK.B")      -> "BRK-B"
    is_ticker(x)        -> True for a US stock ticker in either spelling

Stable ids (from docs/data/securities.json, rebuilt every Saturday):
    security_id("BRK-B") -> Alpaca asset id: one id per listed security, kept
                            when the ticker changes (FB -> META keeps its id)
    cik("BRK-B")         -> SEC CIK: one id per company (BRK-A and BRK-B share it)
    resolve("FB")        -> "META": an old ticker to the current one
    tickers_of(id)       -> every ticker the security has traded under, with dates

The registry joins Alpaca's asset list (id, ticker, exchange, name) with the
SEC's ticker file (CIK). A rebuild keeps every id it has seen: when an id's
ticker changes, the old ticker is closed with an end date and the new one
opened, so history stays attached to the security, not the string.

Usage:
    python identity.py --refresh      # rebuild docs/data/securities.json (needs Alpaca keys)
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
REGISTRY_FILE = ROOT / "docs" / "data" / "securities.json"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"}

_TICKER = re.compile(r"^[A-Z]{1,5}([.\-/][A-Z]{1,2})?$")


# ---------------------------------------------------------------------------
# Spellings (pure)
# ---------------------------------------------------------------------------

def canonical(symbol) -> str:
    """The house spelling: upper case, class shares with a hyphen (BRK-B). '' for nothing."""
    s = str(symbol or "").strip().upper()
    return re.sub(r"[./]", "-", s)


def alpaca(symbol) -> str:
    """Alpaca's spelling: class shares with a dot (BRK.B)."""
    return canonical(symbol).replace("-", ".")


def yahoo(symbol) -> str:
    """Yahoo Finance's spelling (BRK-B)."""
    return canonical(symbol)


def dotted(symbol) -> str:
    """The exchange spelling used by the SEC ticker file and Wikidata (BRK.B)."""
    return alpaca(symbol)


def is_ticker(symbol) -> bool:
    """A US stock ticker in any of the spellings above (not an option or a path)."""
    return bool(_TICKER.match(str(symbol or "").strip().upper()))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _registry() -> Dict:
    return _load(str(REGISTRY_FILE))


@lru_cache(maxsize=4)
def _load(path: str) -> Dict:
    try:
        import contracts
        data = contracts.read(Path(path))
    except FileNotFoundError:
        return {"securities": {}, "by_ticker": {}, "by_old_ticker": {}}
    except Exception as e:
        logger.warning(f"Security registry unreadable: {e}")
        return {"securities": {}, "by_ticker": {}, "by_old_ticker": {}}
    secs = data.get("securities") or {}
    by_ticker, by_old = {}, {}
    for sid, s in secs.items():
        if s.get("ticker"):
            by_ticker[canonical(s["ticker"])] = sid
        for t in s.get("tickers") or []:
            if t.get("until"):
                by_old.setdefault(canonical(t["ticker"]), sid)
    return {"securities": secs, "by_ticker": by_ticker, "by_old_ticker": by_old}


def reload() -> None:
    _load.cache_clear()


def security_id(symbol) -> Optional[str]:
    """Stable id of the security now trading as `symbol` (or formerly, if no one trades as it now)."""
    reg = _registry()
    sym = canonical(symbol)
    return reg["by_ticker"].get(sym) or reg["by_old_ticker"].get(sym)


def security(symbol) -> Optional[Dict]:
    sid = security_id(symbol)
    return dict(_registry()["securities"][sid], id=sid) if sid else None


def cik(symbol) -> Optional[int]:
    s = security(symbol)
    return int(s["cik"]) if s and s.get("cik") else None


def resolve(symbol) -> str:
    """The current ticker of whatever `symbol` names (itself if unknown or unchanged)."""
    s = security(symbol)
    return canonical(s["ticker"]) if s and s.get("ticker") else canonical(symbol)


def tickers_of(security_id_: str) -> List[Dict]:
    return list((_registry()["securities"].get(security_id_) or {}).get("tickers") or [])


# ---------------------------------------------------------------------------
# Rebuild (pure merge + fetchers)
# ---------------------------------------------------------------------------

def merge(previous: Dict[str, Dict], assets: List[Dict], sec: Dict[str, Dict], today: str) -> Dict[str, Dict]:
    """
    New registry from Alpaca `assets` [{id, symbol, exchange, name, ...}] and the
    SEC map {TICKER: {cik, name}}, keeping every id in `previous` and its ticker history.
    """
    out = {sid: dict(s, tickers=[dict(t) for t in s.get("tickers") or []]) for sid, s in (previous or {}).items()}
    seen = set()
    for a in assets:
        sid, sym = a.get("id"), canonical(a.get("symbol"))
        if not sid or not is_ticker(sym) or a.get("exchange") not in EXCHANGES:
            continue
        seen.add(sid)
        rec = out.setdefault(sid, {"tickers": []})
        hist = rec["tickers"]
        current = next((t for t in hist if not t.get("until")), None)
        if current is None or canonical(current["ticker"]) != sym:
            if current is not None:
                current["until"] = today
            hist.append({"ticker": sym, "since": today if current is not None or previous else None})
        company = sec.get(sym) or sec.get(dotted(sym)) or {}
        rec.update({"ticker": sym, "exchange": a.get("exchange"), "name": a.get("name") or company.get("name"),
                    "cik": company.get("cik") or rec.get("cik"), "active": True, "last_seen": today})
    for sid, rec in out.items():
        if sid not in seen and rec.get("active"):
            rec["active"] = False
            for t in rec.get("tickers") or []:
                if not t.get("until"):
                    t["until"] = today
    return out


def fetch_alpaca_assets(session) -> List[Dict]:
    key, secret = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    paper = os.getenv("ALPACA_PAPER", "true").strip().lower() not in ("false", "0", "no")
    base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    resp = session.get(f"{base}/v2/assets", params={"status": "active", "asset_class": "us_equity"},
                       headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_sec_tickers(session) -> Dict[str, Dict]:
    from config import SEC_EDGAR_USER_AGENT
    resp = session.get(SEC_TICKERS_URL, headers={"User-Agent": SEC_EDGAR_USER_AGENT}, timeout=60)
    resp.raise_for_status()
    return {canonical(r["ticker"]): {"cik": int(r["cik_str"]), "name": r.get("title") or ""}
            for r in resp.json().values()}


def refresh(path: Path = REGISTRY_FILE, session=None, assets_fn=fetch_alpaca_assets,
            sec_fn=fetch_sec_tickers, today: Optional[str] = None) -> Dict:
    import contracts
    if session is None:
        import requests
        session = requests.Session()
    today = today or date.today().isoformat()
    previous = contracts.read(path, default={}).get("securities") or {}
    assets = assets_fn(session)
    if len(assets) < 3000:
        raise RuntimeError(f"Alpaca returned only {len(assets)} assets; registry left unchanged")
    secs = merge(previous, assets, sec_fn(session), today)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "note": "Stable security ids (Alpaca asset id) with ticker history and SEC CIK. identity.py.",
               "securities": secs}
    contracts.write(path, payload, compact=True)
    reload()
    renamed = sum(1 for s in secs.values() for t in s["tickers"] if t.get("until") == today and s.get("active"))
    logger.info(f"Security registry: {sum(s.get('active', False) for s in secs.values())} active, "
                f"{renamed} renamed today")
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Security identity registry")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.refresh:
        refresh()
    return 0


if __name__ == "__main__":
    sys.exit(main())
