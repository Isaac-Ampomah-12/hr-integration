"""
ingest.py — Multi-Source HR Data Ingestion Module
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd

from config import CONFIG, logger

# ---------------------------------------------------------------------------
# Dead-letter store
# Records that could not be parsed are appended here as dicts:
#   {source: str, raw_record: Any, error: str}
# The pipeline never crashes; bad records are isolated and reported.
# ---------------------------------------------------------------------------
DEAD_LETTER: list[dict] = []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dead_letter(source: str, raw: Any, error: str) -> None:
    """Append a record to the dead-letter log without raising."""
    DEAD_LETTER.append({"source": source, "raw_record": raw, "error": error})
    logger.warning("DEAD-LETTER [%s] %s — record: %.120s", source, error, str(raw))


# ---------------------------------------------------------------------------
# Source 1: GlobalTech HRIS — Workday CSV export
# ---------------------------------------------------------------------------

def ingest_globaltech_hris(
    filepath: str | Path = CONFIG["globaltech_csv"],
    *,
    encoding: str = CONFIG["globaltech_encoding"],
) -> pd.DataFrame:

    source = "GlobalTech_HRIS"
    path = Path(filepath)

    if not path.exists():
        logger.error("[%s] File not found: %s", source, path)
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            path,
            encoding=encoding,
            dtype={"employee_id": str, "manager_id": str},
        )
    except Exception as exc:
        logger.error("[%s] Failed to read CSV: %s", source, exc)
        return pd.DataFrame()

    logger.info("[%s] Loaded %d records from %s", source, len(df), path.name)
    return df


# ---------------------------------------------------------------------------
# Source 2: AcquiredCo HRIS — BambooHR API (paginated JSON)
# ---------------------------------------------------------------------------

def _fetch_page_acquiredco(
    all_employees: list[dict],
    page: int,
    page_size: int,
) -> dict:
    """
    Simulate one BambooHR API page response.

    In production this would be:
        GET /v1/company/employees?page={page}&pageSize={page_size}
    Here we slice the in-memory list to replicate that behaviour exactly.

    Parameters
    ----------
    all_employees : list[dict]
        Full employee list loaded from the local JSON file.
    page : int
        Zero-based page index.
    page_size : int
        Number of records per page.

    Returns
    -------
    dict
        Simulated API response envelope:
        {page, page_size, total_records, has_next, employees}.
    """
    start = page * page_size
    end = start + page_size
    slice_ = all_employees[start:end]
    return {
        "page":          page,
        "page_size":     page_size,
        "total_records": len(all_employees),
        "has_next":      end < len(all_employees),
        "employees":     slice_,
    }


def ingest_acquiredco_hris(
    filepath: str | Path = CONFIG["acquiredco_json"],
    *,
    page_size: int = CONFIG["acquiredco_page_size"],
) -> pd.DataFrame:

    source = "AcquiredCo_HRIS"
    path = Path(filepath)

    if not path.exists():
        logger.error("[%s] File not found: %s", source, path)
        return pd.DataFrame()

    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("[%s] Malformed JSON: %s", source, exc)
        return pd.DataFrame()

    all_employees: list[dict] = payload.get("employees", [])
    total_reported = payload.get("total_records", len(all_employees))
    logger.info(
        "[%s] API envelope reports %d total records; paginating at page_size=%d",
        source, total_reported, page_size,
    )

    records: list[dict] = []
    page = 0

    while True:
        response = _fetch_page_acquiredco(all_employees, page, page_size)
        page_employees = response["employees"]

        for raw in page_employees:
            try:
                flat = {
                    "employee_identifier": raw["employee_identifier"],
                    "first_name":          raw["name"]["first"],
                    "last_name":           raw["name"]["last"],
                    "full_name":           raw["name"].get("full", ""),
                    "email":               raw["contact"]["email"],
                    "department":          raw["assignment"]["department"],
                    "role":                raw["assignment"]["role"],
                    "location":            raw["assignment"]["location"],
                    "hire_timestamp":      raw["assignment"]["hire_timestamp"],
                    "employment_type":     raw["employment"]["type"],
                    "employment_status":   raw["employment"]["status"],
                    "manager_employee_id": raw.get("manager_employee_id", ""),
                }
                records.append(flat)
            except (KeyError, TypeError) as exc:
                _dead_letter(source, raw, f"Missing field: {exc}")

        logger.info(
            "[%s] Page %d — %d records fetched (cumulative: %d)",
            source, page, len(page_employees), len(records),
        )

        if not response["has_next"]:
            break
        page += 1

    df = pd.DataFrame(records)
    dead_count = sum(1 for d in DEAD_LETTER if d["source"] == source)
    logger.info(
        "[%s] Ingestion complete — %d records loaded, %d dead-lettered",
        source, len(df), dead_count,
    )
    return df

def ingest_payroll(
    filepath: str | Path = CONFIG["payroll_xlsx"],
    *,
    sheet_name: str = CONFIG["payroll_sheet"],
) -> pd.DataFrame:

    source = "Payroll_ADP"
    path = Path(filepath)

    if not path.exists():
        logger.error("[%s] File not found: %s", source, path)
        return pd.DataFrame()

    try:
        df = pd.read_excel(
            path,
            sheet_name=sheet_name,
            dtype={"employee_id": str},
        )
    except Exception as exc:
        logger.error("[%s] Failed to read Excel: %s", source, exc)
        return pd.DataFrame()

    duplicate_count = df.duplicated(subset=["employee_id"]).sum()
    logger.info(
        "[%s] Loaded %d records (%d duplicate employee_id rows) from %s",
        source, len(df), duplicate_count, path.name,
    )
    return df

def ingest_benefits(filepath: str | Path = CONFIG["benefits_xml"]) -> pd.DataFrame:

    source = "Benefits_MedShield"
    path = Path(filepath)

    if not path.exists():
        logger.error("[%s] File not found: %s", source, path)
        return pd.DataFrame()

    try:
        tree = ET.parse(str(path))
    except ET.ParseError as exc:
        logger.error("[%s] XML parse error: %s", source, exc)
        return pd.DataFrame()

    root = tree.getroot()
    records: list[dict] = []

    for elem in root.findall("enrollment"):
        raw_text = ET.tostring(elem, encoding="unicode")
        try:
            emp_id = (elem.findtext("employee_id") or "").strip()
            if not emp_id:
                raise ValueError("empty employee_id")

            record = {
                "employee_id":      emp_id,
                "plan_type":        (elem.findtext("plan_type") or "").strip(),
                "coverage_level":   (elem.findtext("coverage_level") or "").strip(),
                "enrollment_date":  (elem.findtext("enrollment_date") or "").strip(),
                "premium_employee": float(elem.findtext("premium_employee") or 0),
                "premium_employer": float(elem.findtext("premium_employer") or 0),
            }
            records.append(record)
        except (ValueError, TypeError) as exc:
            _dead_letter(source, raw_text, str(exc))

    df = pd.DataFrame(records)
    dead_count = sum(1 for d in DEAD_LETTER if d["source"] == source)
    logger.info(
        "[%s] Loaded %d enrollment records (%d dead-lettered) from %s",
        source, len(df), dead_count, path.name,
    )
    return df

def align_to_standard_schema(
    globaltech_df: pd.DataFrame,
    acquiredco_df: pd.DataFrame,
) -> pd.DataFrame:

    frames: list[pd.DataFrame] = []

    # -- GlobalTech HRIS --
    if not globaltech_df.empty:
        gt = globaltech_df.copy()

        unknown_types = set(gt["employment_type"].dropna().unique()) - {"Full-Time", "Part-Time", "Contractor"}
        if unknown_types:
            logger.warning("[align] GlobalTech unknown employment_type values: %s", unknown_types)

        gt_aligned = pd.DataFrame({
            "employee_id":      gt["employee_id"].astype(str),
            "source_system":    "GlobalTech_HRIS",
            "first_name":       gt["first_name"],
            "last_name":        gt["last_name"],
            "email":            gt["email"],
            "department":       gt["department"],
            "job_title":        gt["job_title"],
            "hire_date":        gt["hire_date"].astype(str).str[:10],
            "country":          gt["country"],
            "employment_type":  gt["employment_type"],
            "employment_status": "Active",
            # manager_id may read as "123.0" due to float NaN coercion in the CSV
            "manager_id": (
                gt["manager_id"]
                .fillna("")
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            ),
        })

        logger.info("[align] GlobalTech HRIS — %d rows mapped to standard schema", len(gt_aligned))
        frames.append(gt_aligned)

    # -- AcquiredCo HRIS --
    if not acquiredco_df.empty:
        aq = acquiredco_df.copy()

        # Identify unmapped employment type codes before converting
        unmapped = aq["employment_type"][~aq["employment_type"].isin(CONFIG["acqco_emp_type_map"])].unique()
        if len(unmapped):
            logger.warning("[align] AcquiredCo unmapped employment_type codes: %s", list(unmapped))

        aq_aligned = pd.DataFrame({
            "employee_id":      aq["employee_identifier"],
            "source_system":    "AcquiredCo_HRIS",
            "first_name":       aq["first_name"],
            "last_name":        aq["last_name"],
            "email":            aq["email"],
            "department":       aq["department"],
            "job_title":        aq["role"],
            "hire_date":        aq["hire_timestamp"].astype(str).str[:10],
            "country":          aq["location"],
            "employment_type":  aq["employment_type"].map(CONFIG["acqco_emp_type_map"]).fillna(aq["employment_type"]),
            "employment_status": aq["employment_status"],
            "manager_id":       aq["manager_employee_id"].fillna("").astype(str),
        })

        logger.info("[align] AcquiredCo HRIS — %d rows mapped to standard schema", len(aq_aligned))
        frames.append(aq_aligned)

    if not frames:
        logger.warning("[align] Both source DataFrames are empty — returning empty standard schema")
        return pd.DataFrame(columns=CONFIG["standard_columns"])

    combined = pd.concat(frames, ignore_index=True)[CONFIG["standard_columns"]]
    logger.info("[align] Combined employee DataFrame — %d total rows", len(combined))
    return combined

def dead_letter_summary() -> pd.DataFrame:

    if not DEAD_LETTER:
        logger.info("Dead-letter queue is empty.")
        return pd.DataFrame(columns=["source", "error", "raw_record"])

    df = pd.DataFrame(DEAD_LETTER)
    summary = df.groupby(["source", "error"]).size().reset_index(name="count")
    logger.info("Dead-letter summary:\n%s", summary.to_string(index=False))
    return df

def ingest_all() -> dict[str, pd.DataFrame]:

    gt_raw   = ingest_globaltech_hris()
    aq_raw   = ingest_acquiredco_hris()
    payroll  = ingest_payroll()
    benefits = ingest_benefits()

    employees = align_to_standard_schema(gt_raw, aq_raw)

    logger.info("=" * 60)
    logger.info("INGESTION COMPLETE")
    logger.info("  employees   : %6d records", len(employees))
    logger.info("  payroll     : %6d records", len(payroll))
    logger.info("  benefits    : %6d records", len(benefits))
    logger.info("  dead-letter : %6d records", len(DEAD_LETTER))
    logger.info("=" * 60)

    return {
        "employees":   employees,
        "payroll":     payroll,
        "benefits":    benefits,
        "dead_letter": dead_letter_summary(),
    }

if __name__ == "__main__":
    result = ingest_all()

    print("\n--- employees (first 3 rows) ---")
    print(result["employees"].head(3).to_string())

    print("\n--- payroll (first 3 rows) ---")
    print(result["payroll"].head(3).to_string())

    print("\n--- benefits (first 3 rows) ---")
    print(result["benefits"].head(3).to_string())

    dl = result["dead_letter"]
    print(f"\n--- dead-letter ({len(dl)} records) ---")
    if not dl.empty:
        print(dl.to_string())
