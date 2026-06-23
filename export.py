"""
export.py — Golden Dataset & Documentation Export (Deliverable 6)
GlobalTech Corp HR Integration Pipeline

Outputs
-------
Data/processed/golden_employees/            Partitioned Parquet (Snappy)
  company_origin=GlobalTech/
  company_origin=AcquiredCo/
Data/processed/golden_employees_schema.csv  Column-level data dictionary
Data/processed/ghost_employees.csv          Payroll records with no HRIS match
Data/processed/probable_matches_review.csv  Fuzzy-matched pairs for HR review
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from config import CONFIG, logger


# ---------------------------------------------------------------------------
# Schema documentation (hardcoded business definitions)
# ---------------------------------------------------------------------------

_GOLDEN_SCHEMA: list[tuple[str, str, str, str, str]] = [
    # (column_name, data_type, description, example_value, nullable)
    ("employee_id",       "string",        "Namespaced ID: GT-XXXXXX (GlobalTech) or AC-XXXXXX (AcquiredCo)",                                  "GT-000001",                         "NO"),
    ("source_system",     "string",        "Primary HR system of record for this employee",                                                     "GlobalTech_HRIS",                   "NO"),
    ("first_name",        "string",        "Normalized first name (Unicode NFC, title-case, Mc/Mac-aware)",                                     "Michael",                           "YES"),
    ("last_name",         "string",        "Normalized last name (Unicode NFC, title-case, hyphens/apostrophes handled)",                       "King",                              "YES"),
    ("full_name",         "string",        "Concatenated display name: first_name + space + last_name",                                         "Michael King",                      "YES"),
    ("email",             "string",        "Work email address; validated against RFC-style regex",                                             "michael.king@globaltech.com",       "YES"),
    ("department",        "string",        "Canonical department from 18-name taxonomy (mapped from legacy codes where needed)",                 "Engineering",                       "YES"),
    ("job_title",         "string",        "Free-text job title as provided by the source system",                                              "Senior Engineer",                   "YES"),
    ("hire_date",         "timestamp[ns]", "Date of hire; parsed from multiple formats (YYYY-MM-DD, MM/DD/YYYY, DD-Mon-YYYY)",                  "2018-03-15",                        "YES"),
    ("country",           "string",        "Country of primary employment",                                                                     "United States",                     "YES"),
    ("employment_type",   "string",        "Canonical employment type: Full-Time | Part-Time | Contractor",                                     "Full-Time",                         "YES"),
    ("employment_status", "string",        "Canonical status: Active | Inactive | On Leave",                                                    "Active",                            "YES"),
    ("manager_id",        "string",        "Manager's namespaced employee_id; empty string when no manager is recorded",                        "GT-002341",                         "YES"),
    ("first_name_raw",    "string",        "Original first name before normalization (audit trail)",                                            "MICHAEL",                           "YES"),
    ("last_name_raw",     "string",        "Original last name before normalization (audit trail)",                                             "king",                              "YES"),
    ("source_systems",    "string",        "Comma-sorted list of every pipeline source that holds a record for this employee_id",               "globaltech_hris,payroll",           "NO"),
    ("dedup_method",      "string",        "How this record's provenance was resolved: exact_id | email_match | fuzzy_name | single_source",    "exact_id",                          "NO"),
    ("company_origin",    "string",        "Partition key: originating company.  GlobalTech | AcquiredCo",                                     "GlobalTech",                        "NO"),
    ("tenure_years",      "double",        "Years of service = (report_date − hire_date) / 365.25, rounded to 2 d.p.",                         "7.42",                              "YES"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Append company_origin, full_name, and tenure_years to the employees DataFrame."""
    out = df.copy()
    out["company_origin"] = out["source_system"].map({
        "GlobalTech_HRIS": "GlobalTech",
        "AcquiredCo_HRIS": "AcquiredCo",
    }).fillna("Unknown")
    out["full_name"] = (
        out["first_name"].astype(str).str.strip()
        + " "
        + out["last_name"].astype(str).str.strip()
    )
    today = pd.Timestamp(date.today())
    hire  = pd.to_datetime(out["hire_date"], errors="coerce")
    out["tenure_years"] = ((today - hire).dt.days / 365.25).round(2)
    return out


def _recommended_action(score: float) -> str:
    """Map a fuzzy-match score to a recommended HR action."""
    if score >= 95:
        return "CONFIRM_MERGE"
    if score >= 88:
        return "MANUAL_REVIEW"
    return "REJECT"


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

def export_golden_parquet(
    employees:  pd.DataFrame,
    output_dir: Path,
) -> tuple[Path, Path]:
    """
    Write the golden employee dataset as a Snappy-compressed Parquet,
    partitioned by company_origin, plus a schema documentation CSV.

    Returns
    -------
    (parquet_dir, schema_csv_path)
    """
    out = _add_derived_columns(employees)

    parquet_dir = output_dir / "golden_employees"
    if parquet_dir.exists():
        shutil.rmtree(parquet_dir)
    parquet_dir.mkdir(parents=True)

    table = pa.Table.from_pandas(out, preserve_index=False)
    pq.write_to_dataset(
        table,
        root_path     = str(parquet_dir),
        partition_cols = ["company_origin"],
        compression   = "snappy",
    )

    for sub in sorted(parquet_dir.iterdir()):
        if sub.is_dir():
            parts  = list(sub.glob("*.parquet"))
            n_rows = sum(pq.read_metadata(str(f)).num_rows for f in parts)
            logger.info("[export] Partition %-38s → %6d rows", sub.name, n_rows)

    logger.info("[export] Golden Parquet → %s  (%d rows total)", parquet_dir, len(out))

    # Schema documentation CSV
    schema_df = pd.DataFrame(
        _GOLDEN_SCHEMA,
        columns=["column_name", "data_type", "description", "example_value", "nullable"],
    )
    schema_path = output_dir / "golden_employees_schema.csv"
    schema_df.to_csv(schema_path, index=False)
    logger.info("[export] Schema doc     → %s  (%d columns)", schema_path, len(schema_df))

    return parquet_dir, schema_path


def export_ghost_report(
    ghost_employees: pd.DataFrame,
    output_dir:      Path,
) -> Path:
    """
    Write ghost employee report CSV.

    Ghost employees are payroll records that have no corresponding HRIS entry —
    a fraud and compliance risk that must be investigated before payroll is run.

    Required output columns
    -----------------------
    payroll_employee_id  — namespaced ID as it appears in payroll
    name                 — always "Unknown — no HRIS record" (payroll contains no names)
    salary_usd_annual    — annualized USD salary from payroll
    ghost_flag_reason    — human-readable explanation
    """
    out_path = output_dir / "ghost_employees.csv"

    if ghost_employees.empty:
        ghost_df = pd.DataFrame(
            columns=["payroll_employee_id", "name", "salary_usd_annual", "ghost_flag_reason"]
        )
    else:
        g = ghost_employees.copy()
        src_col = "source" if "source" in g.columns else None
        ghost_df = pd.DataFrame({
            "payroll_employee_id": g["employee_id"],
            "name":                "Unknown — no HRIS record",
            "salary_usd_annual":   g["salary_usd_annual"].round(2),
            "ghost_flag_reason":   (
                "Payroll record (source: "
                + (g[src_col].astype(str) if src_col else "unknown")
                + ") has no matching employee_id in HRIS"
            ),
        })

    ghost_df.to_csv(out_path, index=False)
    logger.info(
        "[export] Ghost report   → %s  (%d rows — %s)",
        out_path, len(ghost_df),
        "no ghosts detected" if ghost_df.empty else "review required",
    )
    return out_path


def export_probable_matches(
    probable_matches: pd.DataFrame,
    output_dir:       Path,
) -> Path:
    """
    Write the probable-match review file for HR.

    Each row represents a candidate pair: one AcquiredCo employee (record_1)
    and one GlobalTech employee (record_2) whose names are highly similar and
    whose hire dates are within 30 days.  HR must confirm or reject each pair
    before any records are merged.

    Required output columns
    -----------------------
    record_1_id           — AcquiredCo employee_id (AC-XXXXXX)
    record_2_id           — GlobalTech employee_id (GT-XXXXXX)
    similarity_score      — rapidfuzz token_sort_ratio (0–100)
    hire_date_diff_days   — absolute difference in hire dates
    recommended_action    — CONFIRM_MERGE (≥ 95) | MANUAL_REVIEW (88–94) | REJECT (< 88)
    """
    out_path = output_dir / "probable_matches_review.csv"

    if probable_matches.empty:
        pm_df = pd.DataFrame(columns=[
            "record_1_id", "record_1_name", "record_1_hire_date", "record_1_email",
            "record_2_id", "record_2_name", "record_2_hire_date", "record_2_email",
            "similarity_score", "hire_date_diff_days", "recommended_action",
        ])
    else:
        pm = probable_matches.copy()
        pm_df = pd.DataFrame({
            "record_1_id":         pm["acq_employee_id"],
            "record_1_name":       pm["acq_full_name"].str.strip(),
            "record_1_hire_date":  pm["acq_hire_date"],
            "record_1_email":      pm["acq_email"],
            "record_2_id":         pm["gt_employee_id"],
            "record_2_name":       pm["gt_full_name"].str.strip(),
            "record_2_hire_date":  pm["gt_hire_date"],
            "record_2_email":      pm["gt_email"],
            "similarity_score":    pm["match_score"].round(2),
            "hire_date_diff_days": pm["hire_date_delta_days"],
            "recommended_action":  pm["match_score"].apply(_recommended_action),
        }).sort_values("similarity_score", ascending=False).reset_index(drop=True)

    action_counts = pm_df["recommended_action"].value_counts().to_dict() if not pm_df.empty else {}
    pm_df.to_csv(out_path, index=False)
    logger.info(
        "[export] Probable match → %s  (%d pairs: %s)",
        out_path, len(pm_df),
        ", ".join(f"{k}={v}" for k, v in action_counts.items()),
    )
    return out_path


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def export_all(
    dedup_result: dict,
    output_dir:   Path | None = None,
) -> dict[str, Path]:
    """
    Run all Deliverable 6 exports.

    Parameters
    ----------
    dedup_result : dict from deduplicate.dedup_all()
    output_dir   : write destination (defaults to CONFIG["output_dir"])

    Returns
    -------
    dict with keys: parquet_dir, schema_csv, ghost_csv, probable_matches_csv
    """
    if output_dir is None:
        output_dir = Path(CONFIG["output_dir"])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    employees        = dedup_result["employees"]
    ghost_employees  = dedup_result.get("ghost_employees",  pd.DataFrame())
    probable_matches = dedup_result.get("probable_matches", pd.DataFrame())

    parquet_dir, schema_path = export_golden_parquet(employees,        output_dir)
    ghost_path               = export_ghost_report(ghost_employees,     output_dir)
    pm_path                  = export_probable_matches(probable_matches, output_dir)

    logger.info("=" * 60)
    logger.info("EXPORT COMPLETE — Deliverable 6")
    logger.info("  Golden Parquet  : %s", parquet_dir)
    logger.info("  Schema doc      : %s", schema_path)
    logger.info("  Ghost report    : %s", ghost_path)
    logger.info("  Probable matches: %s", pm_path)
    logger.info("=" * 60)

    return {
        "parquet_dir":          parquet_dir,
        "schema_csv":           schema_path,
        "ghost_csv":            ghost_path,
        "probable_matches_csv": pm_path,
    }


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from ingest import ingest_all
    from clean import clean_all
    from deduplicate import dedup_all

    raw     = ingest_all()
    cleaned = clean_all(raw)
    deduped = dedup_all(cleaned)
    paths   = export_all(deduped)

    print("\n=== Deliverable 6 outputs ===")
    for name, path in paths.items():
        p = Path(path)
        if p.is_dir():
            files  = list(p.rglob("*.parquet"))
            n_rows = sum(pq.read_metadata(str(f)).num_rows for f in files)
            print(f"  {name:<22}: {path}  ({len(files)} files, {n_rows:,} rows)")
        else:
            size_kb = p.stat().st_size / 1024
            print(f"  {name:<22}: {path}  ({size_kb:.1f} KB)")

    # Round-trip verification
    print("\n=== Parquet round-trip check ===")
    df_rt = pq.read_table(str(paths["parquet_dir"])).to_pandas()
    print(f"  Total rows    : {len(df_rt):,}")
    print(f"  Partitions    : {df_rt['company_origin'].value_counts().to_dict()}")
    print(f"  Columns       : {len(df_rt.columns)}")
    print(f"  Sample columns: {df_rt.columns[:5].tolist()}")
