"""Filesystem layout. Everything takes a Paths object so tests can point data/docs at a temp dir."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    docs: Path
    crosswalks: Path
    checks: Path
    sql: Path

    @classmethod
    def from_root(cls, root: Path | str, data: Path | None = None, docs: Path | None = None) -> "Paths":
        root = Path(root)
        return cls(
            root=root,
            data=Path(data) if data else root / "data",
            docs=Path(docs) if docs else root / "docs",
            crosswalks=root / "crosswalks",
            checks=root / "checks",
            sql=root / "sql",
        )

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def facts_csv(self) -> Path:
        return self.data / "facts.csv"

    @property
    def revisions_csv(self) -> Path:
        return self.data / "revisions.csv"

    @property
    def health_json(self) -> Path:
        return self.data / "health.json"

    @property
    def series_csv(self) -> Path:
        return self.crosswalks / "series.csv"

    @property
    def golden_yaml(self) -> Path:
        return self.checks / "golden.yaml"

    @property
    def ack_yaml(self) -> Path:
        return self.checks / "restatement_ack.yaml"

    @property
    def views_sql(self) -> Path:
        return self.sql / "views.sql"

    @property
    def tccp_facts_sql(self) -> Path:
        return self.sql / "tccp_facts.sql"

    @property
    def tccp_products_csv(self) -> Path:
        """Sub-grain raw table for the TCCP source: one row per card product per half-year, written by its fetcher."""
        return self.raw / "tccp" / "tccp_products.csv"
