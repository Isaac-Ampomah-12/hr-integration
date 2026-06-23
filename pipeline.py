"""
pipeline.py — Full Pipeline Orchestrator
GlobalTech Corp HR Integration Pipeline

Runs all six deliverables end-to-end:
  1. Ingest    — load the four raw HR sources
  2. Clean     — normalize, standardize, and enrich
  3. Deduplicate — three-pass dedup + ghost detection + provenance
  4. Validate  — 15-check quality report + pipeline gate
  5. Visualize — 6-chart EDA report (300 DPI PNG)
  6. Export    — golden Parquet + ghost CSV + probable-match review

Usage
-----
  python pipeline.py              # run everything, gate halts on >2 check failures
  python pipeline.py --no-gate   # run everything, ignore gate result
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from config import CONFIG, logger
from ingest import ingest_all
from clean import clean_all
from deduplicate import dedup_all
from validate import validate_all
from visualize import visualize_all
from export import export_all


def run(*, respect_gate: bool = True) -> int:
    """
    Execute the full pipeline.

    Returns
    -------
    0 on success, 1 if the validation gate halted the pipeline.
    """
    logger.info("=" * 65)
    logger.info("GlobalTech HR Integration Pipeline  —  %s", date.today())
    logger.info("=" * 65)

    # ── Deliverable 1: Ingest ─────────────────────────────────────────────
    logger.info("--- Deliverable 1: Ingestion ---")
    raw = ingest_all()

    # ── Deliverable 2: Clean ──────────────────────────────────────────────
    logger.info("--- Deliverable 2: Cleaning ---")
    cleaned = clean_all(raw)

    # ── Deliverable 3: Deduplicate ────────────────────────────────────────
    logger.info("--- Deliverable 3: Deduplication ---")
    deduped = dedup_all(cleaned)

    # ── Deliverable 4: Validate ───────────────────────────────────────────
    logger.info("--- Deliverable 4: Validation ---")
    val_report, gate_passed = validate_all(deduped)

    if not gate_passed and respect_gate:
        logger.critical(
            "Pipeline halted by validation gate.  "
            "Review %s/validation_report.html before re-running.",
            CONFIG["output_dir"],
        )
        return 1

    # ── Deliverable 5: Visualize ──────────────────────────────────────────
    logger.info("--- Deliverable 5: Visualization ---")
    eda_path = visualize_all(deduped, val_report)
    logger.info("EDA report → %s", eda_path)

    # ── Deliverable 6: Export ─────────────────────────────────────────────
    logger.info("--- Deliverable 6: Export ---")
    export_paths = export_all(deduped)

    logger.info("=" * 65)
    logger.info("PIPELINE COMPLETE — outputs in %s", CONFIG["output_dir"])
    logger.info("  golden_employees/ (Parquet, partitioned)")
    logger.info("  golden_employees_schema.csv")
    logger.info("  ghost_employees.csv")
    logger.info("  probable_matches_review.csv")
    logger.info("  validation_report.csv / .html")
    logger.info("  eda_report.png")
    logger.info("=" * 65)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GlobalTech HR Integration Pipeline")
    parser.add_argument(
        "--no-gate", action="store_true",
        help="Continue even if the validation gate fails (use for debugging only)",
    )
    args = parser.parse_args()
    sys.exit(run(respect_gate=not args.no_gate))
