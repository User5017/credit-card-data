from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from carddash.fetchers import fred, phillyfed, tccp
from carddash.paths import Paths
from carddash.schema import coerce_facts
from carddash.series import load_series

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PULLED_AT = "2026-09-07T00:00:00Z"


@pytest.fixture(scope="session")
def meta() -> pd.DataFrame:
    return load_series(REPO / "crosswalks" / "series.csv")


def facts_from_fred_fixtures(meta: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for path in sorted((FIXTURES / "fred").glob("*.csv")):
        row = meta[(meta["source"] == "fred") & (meta["source_id"] == path.stem)]
        assert len(row) == 1, f"fixture {path.name} has no series.csv row"
        obs = fred.parse_fredgraph(path.read_text(encoding="utf-8"))
        frames.append(fred.to_facts(obs, row.iloc[0], PULLED_AT))
    return coerce_facts(pd.concat(frames, ignore_index=True))


@pytest.fixture(scope="session")
def tccp_products() -> pd.DataFrame:
    """Every checked-in TCCP workbook (H1 2023 and H2 2025), parsed once per session."""
    return tccp.parse_all(FIXTURES / "tccp")


def facts_from_tccp_fixtures(products: pd.DataFrame) -> pd.DataFrame:
    return coerce_facts(tccp.products_to_facts(products, PULLED_AT))


PHILLYFED_FIXTURE_QUARTER = (2026, 1)  # the release the checked-in 26Q1-*.csv files come from


@pytest.fixture(scope="session")
def phillyfed_facts() -> pd.DataFrame:
    """Facts from the checked-in 2026 Q1 Philly Fed release (both CSVs), parsed once per session."""
    paths = {f: FIXTURES / "phillyfed" / f"26Q1-{f}.csv" for f in phillyfed.FILES}
    return coerce_facts(phillyfed.parse_release(paths, PULLED_AT, expected_quarter=PHILLYFED_FIXTURE_QUARTER))


@pytest.fixture(scope="session")
def fixture_facts(meta, tccp_products, phillyfed_facts) -> pd.DataFrame:
    """Facts from every checked-in raw file, all sources, the way the loader would see them."""
    return coerce_facts(
        pd.concat(
            [facts_from_fred_fixtures(meta), facts_from_tccp_fixtures(tccp_products), phillyfed_facts],
            ignore_index=True,
        )
    )


@pytest.fixture
def tmp_paths(tmp_path) -> Paths:
    return Paths.from_root(REPO, data=tmp_path / "data", docs=tmp_path / "docs")
