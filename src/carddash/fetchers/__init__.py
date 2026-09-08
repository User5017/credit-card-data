"""Fetcher registry.

Contract for every fetcher module:
    SOURCE: str
    fetch(meta: DataFrame, raw_dir: Path, session, pulled_at: str) -> DataFrame in the facts schema

Rules: idempotent; pulls full history; writes the raw download under raw_dir/latest/ before parsing;
never touches facts.csv or DuckDB (the loader does); parses by header text, never by column position.
"""

from . import bea, census, fdic, fred, nyfed_hhdc, nyfed_sce, nyfed_sce_monthly, phillyfed, tccp

FETCHERS = {
    fred.SOURCE: fred,
    tccp.SOURCE: tccp,
    phillyfed.SOURCE: phillyfed,
    nyfed_hhdc.SOURCE: nyfed_hhdc,
    fdic.SOURCE: fdic,
    nyfed_sce.SOURCE: nyfed_sce,
    nyfed_sce_monthly.SOURCE: nyfed_sce_monthly,
    bea.SOURCE: bea,
    census.SOURCE: census,
}
