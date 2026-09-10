"""NCUA call report fetcher: the checked-in extracts, a synthetic zip, quarter discovery, the summary numbers."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from carddash.fetchers import ncua
from carddash.series import series_for_source
from conftest import NCUA_FIXTURE_DIR, PULLED_AT


def test_extracts_reproduce_the_quarterly_summary(ncua_facts):
    """NCUA Quarterly Credit Union Data Summary 2026 Q1: card balances $86.0bn, delinquency 2.04%; Dec 2023 82.0."""
    ind = ncua_facts[ncua_facts["entity"] == "NCUA_FICU"].pivot(index="period_end", columns="metric", values="value")
    assert abs(ind.loc[pd.Timestamp("2026-03-31"), "ncua_card_loans"] - 86.0) < 0.06
    assert abs(ind.loc[pd.Timestamp("2023-12-31"), "ncua_card_loans"] - 82.0) < 0.06
    dq = 100 * ind.loc[pd.Timestamp("2026-03-31"), "ncua_card_delinquent_60plus"] / ind.loc[pd.Timestamp("2026-03-31"), "ncua_card_loans"]
    assert abs(dq - 2.04) < 0.01
    navy = ncua_facts[(ncua_facts["entity"] == "NCUA:NAVY_FEDERAL") & (ncua_facts["period_end"] == pd.Timestamp("2026-03-31"))]
    navy = navy.set_index("metric")["value"]
    assert abs(navy["ncua_card_loans"] - 32.686877484) < 1e-6 and navy["ncua_card_rate"] == 18.0


def test_series_shape(ncua_facts, meta):
    cols = ["metric", "entity", "tier", "period_type"]
    listed = {tuple(r) for r in series_for_source(meta, ncua.SOURCE)[cols].itertuples(index=False)}
    loaded = {tuple(r) for r in ncua_facts[cols].drop_duplicates().itertuples(index=False)}
    assert loaded == listed and len(loaded) == 24
    assert set(ncua_facts["entity_type"]) == {"aggregate", "credit_union"}
    assert sorted(ncua_facts["period_end"].dt.date.unique().astype(str)) == ["2016-03-31", "2021-12-31", "2023-12-31", "2026-03-31"]
    assert ncua_facts["pulled_at"].unique().tolist() == [PULLED_AT]


def _zip(cycle: str, cus: dict[str, dict], types: dict[str, str] | None = None) -> bytes:
    """A minimal call-report zip: FOICU plus the three schedules, one row per credit union."""
    types = types or {cu: "1" for cu in cus}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("FOICU.txt", "CU_NUMBER,CYCLE_DATE,CU_TYPE,CU_NAME\n" + "".join(f'{cu},{cycle},"{types[cu]}","X"\n' for cu in cus))
        z.writestr("fs220A.txt", "CU_NUMBER,CYCLE_DATE,ACCT_396\n" + "".join(f"{cu},{cycle},{v['396']}\n" for cu, v in cus.items()))
        z.writestr("FS220B.txt", "CU_NUMBER,CYCLE_DATE,Acct_680,Acct_681,Acct_045B\n"
                   + "".join(f"{cu},{cycle},{v['680']},{v['681']},{v['045B']}\n" for cu, v in cus.items()))
        z.writestr("FS220.txt", "CU_NUMBER,CYCLE_DATE,ACCT_521\n" + "".join(f"{cu},{cycle},{v['521']}\n" for cu, v in cus.items()))
    return buf.getvalue()


def _cus(extra=None):
    base = {cu: {"396": 100, "680": 4, "681": 1, "045B": 2, "521": 1500} for cu in ncua.CREDIT_UNIONS}
    base.update(extra or {})
    return base


def test_synthetic_zip_sums_only_insured_credit_unions_and_keeps_tracked_rows():
    cus = _cus({"999": {"396": 50, "680": 1, "681": 0, "045B": 1, "521": 900}, "998": {"396": 7, "680": 1, "681": 0, "045B": 1, "521": 900}})
    df = ncua.extract(_zip("3/31/2026 0:00:00", cus, types={**{cu: "1" for cu in cus}, "998": "3"}), 2026, 1)
    ind = df[df["entity"] == "NCUA_FICU"].set_index("metric")["value"]
    assert ind["ncua_card_loans"] == 4 * 100 + 50  # the privately insured 998 is excluded
    assert ind["ncua_card_charge_offs_ytd"] == 4 * 4 + 1 and "ncua_card_rate" not in ind
    assert len(df) == 4 * 5 + 4 and set(df["cycle_date"]) == {"2026-03-31"}
    assert df[(df["entity"] == "NCUA:PENFED") & (df["metric"] == "ncua_card_rate")]["value"].iloc[0] == 1500


def test_synthetic_zip_fails_on_wrong_cycle_missing_tracked_or_bad_number():
    with pytest.raises(ValueError, match="is dated"):
        ncua.extract(_zip("12/31/2025 0:00:00", _cus()), 2026, 1)
    cus = _cus()
    del cus["227"]
    with pytest.raises(ValueError, match="tracked credit unions missing"):
        ncua.extract(_zip("3/31/2026 0:00:00", cus), 2026, 1)
    cus = _cus()
    cus["5536"]["396"] = "abc"
    with pytest.raises(ValueError, match="not a number"):
        ncua.extract(_zip("3/31/2026 0:00:00", cus), 2026, 1)
    with pytest.raises(ValueError, match="not a zip"):
        ncua.extract(b"<!DOCTYPE html>", 2026, 1)


def test_to_facts_scales_and_requires_consecutive_quarters(meta, tmp_path):
    mine = series_for_source(meta, ncua.SOURCE)
    a = ncua.extract(_zip("3/31/2026 0:00:00", _cus()), 2026, 1)
    b = ncua.extract(_zip("6/30/2026 0:00:00", _cus()), 2026, 2)
    facts = ncua.to_facts([a, b], mine, PULLED_AT)
    loans = facts[(facts["metric"] == "ncua_card_loans") & (facts["entity"] == "NCUA_FICU")].set_index("period_end")["value"]
    assert loans.tolist() == pytest.approx([400e-9, 400e-9])  # dollars to billions
    rate = facts[(facts["metric"] == "ncua_card_rate") & (facts["entity"] == "NCUA:BECU")]["value"].iloc[0]
    assert rate == 15.0  # basis points to percent
    c = ncua.extract(_zip("12/31/2026 0:00:00", _cus()), 2026, 4)
    with pytest.raises(ValueError, match="quarters jump"):
        ncua.to_facts([a, c], mine, PULLED_AT)
    # extracts round-trip through the csv
    path = tmp_path / "2026-03.csv"
    ncua.write_extract(a, path)
    assert ncua.read_extract(path).equals(a[ncua.EXTRACT_COLUMNS].reset_index(drop=True))


class FakeSession:
    def __init__(self, zips):
        self.zips = zips
        self.calls = []

    def get(self, url):
        self.calls.append(url)

        class R:
            def __init__(self, content, status):
                self.content, self.status_code = content, status

            def raise_for_status(self):
                if self.status_code >= 500:
                    raise RuntimeError(self.status_code)

        blob = self.zips.get(url)
        return R(blob, 200) if blob is not None else R(b"<!DOCTYPE html>not found", 404)


def test_fetch_downloads_only_missing_and_newest_quarters(meta, tmp_path, monkeypatch):
    monkeypatch.setattr(ncua, "FIRST_QUARTER", (2025, 4))
    zips = {ncua.zip_url(2025, 4): _zip("12/31/2025 0:00:00", _cus()), ncua.zip_url(2026, 1): _zip("3/31/2026 0:00:00", _cus()),
            ncua.zip_url(2026, 2): _zip("6/30/2026 0:00:00", _cus())}
    session = FakeSession(zips)
    facts = ncua.fetch(meta, tmp_path, session, "2026-09-08T00:00:00Z")
    assert sorted(p.name for p in (tmp_path / "quarters").glob("*.csv")) == ["2025-12.csv", "2026-03.csv", "2026-06.csv"]
    assert facts["period_end"].max() == pd.Timestamp("2026-06-30") and len(facts) == 3 * 24
    # 2026 Q3 was tried first (walk back from September), then every quarter once
    assert session.calls[0] == ncua.zip_url(2026, 3) and len(session.calls) == 4
    # a second run re-pulls only the newest two quarters; 2025 Q4 comes from its extract
    session2 = FakeSession(zips)
    ncua.fetch(meta, tmp_path, session2, "2026-09-08T00:00:00Z")
    assert ncua.zip_url(2025, 4) not in session2.calls and ncua.zip_url(2026, 1) in session2.calls


def test_quarter_helpers():
    assert ncua.zip_url(2026, 1).endswith("call-report-data-2026-03.zip")
    assert ncua.extract_name(2025, 4) == "2025-12.csv"
    assert ncua.quarters_between((2025, 3), (2026, 1)) == [(2025, 3), (2025, 4), (2026, 1)]
