"""Fetcher registry.

Contract for every fetcher module:
    SOURCE: str
    fetch(meta: DataFrame, raw_dir: Path, session, pulled_at: str) -> DataFrame in the facts schema

Rules: idempotent; pulls full history; writes the raw download under raw_dir/latest/ before parsing;
never touches facts.csv or DuckDB (the loader does); parses by header text, never by column position.
"""

from . import fdic, fred, nyfed_hhdc, phillyfed, tccp

FETCHERS = {
    fred.SOURCE: fred,
    tccp.SOURCE: tccp,
    phillyfed.SOURCE: phillyfed,
    nyfed_hhdc.SOURCE: nyfed_hhdc,
    fdic.SOURCE: fdic,
}
