# Point-in-time history

Every version of every shared data file (`docs/data` on `main`), with when the bot knew it.
Written by `pit.py` on `main` (nightly by `pit.yml`). Do not edit by hand.

    import pit
    store = pit.Store("<checkout of this branch>")
    store.as_of("screener.json", "2026-10-03T09:15:00-04:00")
