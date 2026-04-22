"""Factor rating — migrated from ``research.analysis.compute_rating``.

The task description mentions an S/A/B/C/D letter scale; the legacy numeric
system (3=strong / 2=usable / 1=weak / 0=invalid) is what production relies on
and what AC-13.2 requires regression parity with, so ``compute_rating``
stays numeric.  A thin ``rating_letter()`` helper maps the numeric rating
onto the S/A/B/C/D scale for display purposes (S=3, A=2, B=1, C/D=0 split by
``ic_positive_pct``).
"""
from __future__ import annotations


def compute_rating(summary: dict) -> int:
    """Rate factor: 3 = strong, 2 = usable, 1 = weak, 0 = invalid.

    Thresholds match ``research.analysis.compute_rating`` exactly.  The IR
    check uses ``abs(ir)`` so negative-but-large ICs still rate as strong.
    """
    ir = abs(summary.get("ir", 0))
    pct = summary.get("ic_positive_pct", 0)
    if ir > 1.0 and pct > 0.60:
        return 3
    if ir > 0.5 and pct > 0.55:
        return 2
    if ir > 0.2:
        return 1
    return 0


def rating_letter(rating: int, summary: dict | None = None) -> str:
    """Map numeric rating → S/A/B/C/D letter grade for display.

    Mapping:
      * ``3`` → ``"S"``
      * ``2`` → ``"A"``
      * ``1`` → ``"B"``
      * ``0`` + ``ic_positive_pct >= 0.45`` → ``"C"``
      * ``0`` + ``ic_positive_pct < 0.45`` → ``"D"``
    """
    if rating >= 3:
        return "S"
    if rating == 2:
        return "A"
    if rating == 1:
        return "B"
    pct = (summary or {}).get("ic_positive_pct", 0.0)
    return "C" if pct >= 0.45 else "D"


__all__ = ["compute_rating", "rating_letter"]
