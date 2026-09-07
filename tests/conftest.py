from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from carddash.fetchers import fred
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
def fixture_facts(meta) -> pd.DataFrame:
    return facts_from_fred_fixtures(meta)


@pytest.fixture
def tmp_paths(tmp_path) -> Paths:
    return Paths.from_root(REPO, data=tmp_path / "data", docs=tmp_path / "docs")
