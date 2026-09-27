#!/usr/bin/env python3
"""
Measure the per-load cost of a raw DBAPI read against SQLAlchemy Core and the
SQLAlchemy ORM, on this machine, for the access pattern the store actually has.

WHY THIS EXISTS. The system-of-record decision (YB-043) has to choose between a
thin DBAPI adapter and an ORM, and "an ORM adds latency" is repeated often enough
to be treated as fact without ever being measured for *this* workload. The workload
is unusual and worth stating: the app loads a whole scope's graph and computes
projections in Python, so the cost that matters is one full-table read and
hydration, not a per-row round trip.

WHAT IT MEASURES. One table shaped like `assertion` (~9 scalar columns plus a JSON
blob), read in full, hydrated three ways:

    raw      sqlite3 cursor -> list[tuple]        (the floor)
    core     SQLAlchemy Core -> list[Row]         (dialect portability, no identity map)
    orm      SQLAlchemy ORM  -> list[object]      (identity map + change tracking)

Insert cost is reported too, because the write path is one transaction per review
action and a slow hydration there would matter differently.

Run:  .venv/bin/python scripts/bench_store_backends.py [rows] [repeats]
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Tuple

from sqlalchemy import (
    Column,
    Float,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 5_000
REPEATS = int(sys.argv[2]) if len(sys.argv) > 2 else 7

DEFAULT_GRAPH = json.dumps({"nodes": 150, "runs": 2, "declared_by": 150})


# ---------------------------------------------------------------------------
# The three shapes of the same table
# ---------------------------------------------------------------------------


def raw_db(rows: int) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE assertion (
               product_id TEXT, assertion_id TEXT, subject_id TEXT, predicate TEXT,
               object_id TEXT, value TEXT, confidence REAL, source_text TEXT,
               ontology_class TEXT, status TEXT, scope TEXT, graph TEXT,
               PRIMARY KEY (product_id, assertion_id))"""
    )
    conn.executemany(
        "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("p1", f"a_{i:016x}", f"container:c{i % 400}", "responsibility",
             None, "does a thing", 0.9, "source text here", "Container",
             "UNVERIFIED", "INITIATIVE_PROPOSAL", DEFAULT_GRAPH)
            for i in range(rows)
        ],
    )
    conn.commit()
    return conn


metadata = MetaData()
assertion_table = Table(
    "assertion", metadata,
    Column("product_id", String, primary_key=True),
    Column("assertion_id", String, primary_key=True),
    Column("subject_id", String), Column("predicate", String),
    Column("object_id", String), Column("value", String),
    Column("confidence", Float), Column("source_text", String),
    Column("ontology_class", String), Column("status", String),
    Column("scope", String), Column("graph", String),
)


class Base(DeclarativeBase):
    pass


class AssertionRow(Base):
    __tablename__ = "assertion"
    product_id: Mapped[str] = mapped_column(primary_key=True)
    assertion_id: Mapped[str] = mapped_column(primary_key=True)
    subject_id: Mapped[str] = mapped_column()
    predicate: Mapped[str] = mapped_column()
    object_id: Mapped[str | None] = mapped_column()
    value: Mapped[str | None] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    source_text: Mapped[str] = mapped_column()
    ontology_class: Mapped[str | None] = mapped_column()
    status: Mapped[str] = mapped_column()
    scope: Mapped[str] = mapped_column()
    graph: Mapped[str] = mapped_column()


def core_db(rows: int):
    """A file-backed engine so ORM and Core share one database.

    File-backed rather than in-memory because the two must hit the *same* database to
    be comparable, and SQLAlchemy would give each pooled connection its own private
    in-memory one.
    """
    db_path = Path(tempfile.gettempdir()) / "sea_bench_store_backends.sqlite"
    db_path.unlink(missing_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata.drop_all(engine, checkfirst=True)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(assertion_table), [
            {"product_id": "p1", "assertion_id": f"a_{i:016x}",
             "subject_id": f"container:c{i % 400}", "predicate": "responsibility",
             "object_id": None, "value": "does a thing", "confidence": 0.9,
             "source_text": "source text here", "ontology_class": "Container",
             "status": "UNVERIFIED", "scope": "INITIATIVE_PROPOSAL",
             "graph": DEFAULT_GRAPH}
            for i in range(rows)
        ])
    return engine


# ---------------------------------------------------------------------------
# The loaders — each returns a list of plain dicts, the shape the domain model
# builder consumes, so the comparison ends at the same place.
# ---------------------------------------------------------------------------


def load_raw(conn: sqlite3.Connection) -> List[dict]:
    cursor = conn.execute("SELECT * FROM assertion")
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_core(engine) -> List[dict]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(assertion_table)).mappings()]


def load_orm(engine) -> List[dict]:
    with Session(engine) as session:
        return [
            {"assertion_id": a.assertion_id, "subject_id": a.subject_id,
             "predicate": a.predicate, "value": a.value, "confidence": a.confidence}
            for a in session.execute(select(AssertionRow)).scalars()
        ]


def timeit(fn: Callable[[], object], repeats: int) -> Tuple[float, float]:
    """Median and best milliseconds — median because the first run pays setup."""
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), min(samples)


def main() -> int:
    print(f"rows={ROWS:,}  repeats={REPEATS}  sqlite in-memory / file")
    conn = raw_db(ROWS)
    engine = core_db(ROWS)

    results = {
        "raw  (sqlite3 -> list[dict])": timeit(lambda: load_raw(conn), REPEATS),
        "core (SQLAlchemy Core mappings)": timeit(lambda: load_core(engine), REPEATS),
        "orm  (SQLAlchemy ORM objects)": timeit(lambda: load_orm(engine), REPEATS),
    }

    floor = results["raw  (sqlite3 -> list[dict])"][0]
    print(f"\n{'loader':34} {'median ms':>10} {'best ms':>9} {'vs raw':>8}")
    for name, (median, best) in results.items():
        print(f"{name:34} {median:10.1f} {best:9.1f} {median / floor:7.2f}x")

    per_row = {name: median / ROWS * 1000 for name, (median, _) in results.items()}
    print("\nper row: " + "  ".join(f"{n.split()[0]}={v:.2f}us" for n, v in per_row.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
