"""Numeric-aware sample ordering for dataset read models.

SpaceNet sample stems are integers (``0``, ``1``, ``10``, ``100`` ...). Ordering
them as plain text produces the surprising ``0, 1, 10, 100, 1000`` sequence, so
the dataset sample list orders integer-looking names by numeric value. Names
that are not pure integers keep lexicographic order (and sort after integers).

SQLite is the only supported V1 database, so the numeric test uses ``GLOB``.
This affects display order only; the frozen recording manifest
(``app.benchmarks.manifest``) keeps its own canonical ordering and hash.
"""
from __future__ import annotations

from sqlalchemy import Integer, and_, case, cast

__all__ = ["numeric_aware_order"]


def _integer_looking(column):
    # GLOB: starts with a digit AND contains no non-digit (the empty string fails).
    return and_(
        column.op("GLOB")("[0-9]*"),
        column.op("NOT GLOB")("*[^0-9]*"),
    )


def numeric_aware_order(*columns):
    """Return ORDER BY terms for numeric-first, numeric-aware ordering.

    Integer-looking values sort ascending by numeric value; everything else
    keeps lexicographic order after them. The raw column is kept as a tiebreak.
    """
    terms = []
    for column in columns:
        integer_looking = _integer_looking(column)
        terms.append(case((integer_looking, 0), else_=1))
        terms.append(case((integer_looking, cast(column, Integer)), else_=0))
        terms.append(column)
    return terms
