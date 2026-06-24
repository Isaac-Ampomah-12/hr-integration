from __future__ import annotations

from typing import TypedDict

import pandas as pd
from rapidfuzz import fuzz

from config import logger


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

class DeduplicationReport(TypedDict):
    pass_name:        str
    input_rows:       int
    output_rows:      int
    rows_removed:     int
    rows_quarantined: int
    details:          dict[str, int]


# Threshold and window for Pass 3
_FUZZY_THRESHOLD  = 88        # token_sort_ratio minimum
_DATE_WINDOW_DAYS = 30        # hire-date block half-width


# ---------------------------------------------------------------------------
# Helper: build source_systems string
# ---------------------------------------------------------------------------

def _source_systems_series(df: pd.DataFrame, pay_ids: set, ben_ids: set) -> pd.Series:
    """Return a Series of source_systems strings aligned to df's index."""
    def _row(r: pd.Series) -> str:
        parts = {r["source_system"].lower().replace(" ", "_").replace("-", "_")}
        if r["employee_id"] in pay_ids:
            parts.add("payroll")
        if r["employee_id"] in ben_ids:
            parts.add("benefits")
        return ",".join(sorted(parts))

    return df.apply(_row, axis=1)


# ---------------------------------------------------------------------------
# Pass 1 — Exact employee ID match
# ---------------------------------------------------------------------------

def _pass1_exact_id(
    employees: pd.DataFrame,
    payroll:   pd.DataFrame,
    benefits:  pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, DeduplicationReport]:

    src = "pass1_exact_id"
    df = employees.copy()
    input_rows = len(df)
    quarantine_rows: list[pd.DataFrame] = []

    pay_ids = set(payroll["employee_id"])
    ben_ids = set(benefits["employee_id"])

    # ── 1a. Intra-HRIS duplicate employee_ids ────────────────────────────
    dup_id_mask  = df.duplicated("employee_id", keep=False)
    dup_ids      = df.loc[dup_id_mask, "employee_id"].unique()

    collision_count    = 0
    same_person_count  = 0

    if len(dup_ids):
        keep_indices    : list[int] = []
        remove_indices  : list[int] = []

        for _, group in df[df["employee_id"].isin(dup_ids)].groupby("employee_id"):
            if group["first_name"].nunique() == 1 and group["last_name"].nunique() == 1:
                # Same person — keep first (HRIS rows come before payroll/benefits rows)
                keep_indices.append(group.index[0])
                remove_indices.extend(group.index[1:].tolist())
                same_person_count += len(group) - 1
            else:
                # ID collision — different people share a canonical ID
                # Keep the non-DUP row (original AcquiredCo employee); quarantine DUP
                first_name_raw_col = "first_name_raw" if "first_name_raw" in group.columns else "first_name"
                non_dup = group[~group[first_name_raw_col].str.contains("DUP", case=False, na=False)]

                if len(non_dup) >= 1:
                    keep_indices.append(non_dup.index[0])
                    qtmp = group.loc[group.index.difference([non_dup.index[0]])].copy()
                else:
                    # No clear non-DUP row — keep first, quarantine rest
                    keep_indices.append(group.index[0])
                    qtmp = group.iloc[1:].copy()

                qtmp["quarantine_reason"] = "ID_COLLISION"
                quarantine_rows.append(qtmp)
                collision_count += len(qtmp)

        keep_mask = ~df.index.isin(remove_indices + [idx for q in quarantine_rows for idx in q.index])
        df = df[keep_mask].copy()

    if same_person_count:
        logger.info("[%s] Removed %d same-person exact-ID duplicates (HRIS priority kept)", src, same_person_count)
    if collision_count:
        logger.warning(
            "[%s] %d ID-collision rows quarantined (different people share same canonical ID — "
            "artifact of ACQ_DUP_NNNNN normalisation)",
            src, collision_count,
        )

    # ── 1b. Assign source_systems and dedup_method ────────────────────────
    df["source_systems"] = _source_systems_series(df, pay_ids, ben_ids)
    multi_source = df["source_systems"].str.contains(",")
    df["dedup_method"] = "single_source"
    df.loc[multi_source, "dedup_method"] = "exact_id"

    multi_count = int(multi_source.sum())
    logger.info(
        "[%s] source_systems annotated — %d employees appear in >1 source, %d payroll-only ghosts checked",
        src, multi_count, len(pay_ids - set(df["employee_id"])),
    )

    quarantine = pd.concat(quarantine_rows, ignore_index=True) if quarantine_rows else pd.DataFrame()

    report: DeduplicationReport = {
        "pass_name":        src,
        "input_rows":       input_rows,
        "output_rows":      len(df),
        "rows_removed":     same_person_count,
        "rows_quarantined": collision_count,
        "details": {
            "intra_hris_same_person_removed":    same_person_count,
            "intra_hris_id_collision_quarantined": collision_count,
            "multi_source_employees":            multi_count,
        },
    }

    logger.info(
        "[%s] Complete — %d in, %d out, %d quarantined",
        src, input_rows, len(df), collision_count,
    )
    return df, quarantine, report

def _pass2_email_match(
    employees: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, DeduplicationReport]:

    src = "pass2_email_match"
    df = employees.copy()
    input_rows = len(df)

    email_sources = df.groupby("email")["source_system"].nunique()
    cross_emails  = set(email_sources[email_sources > 1].index)

    quarantine = pd.DataFrame()
    rows_removed = 0

    if cross_emails:
        # Keep GlobalTech record; quarantine AcquiredCo
        acq_cross_mask = cross_mask & (df["source_system"] == "AcquiredCo_HRIS")
        quarantine = df[acq_cross_mask].copy()
        quarantine["quarantine_reason"] = "EMAIL_MATCH_CROSS_COMPANY"

        df = df[~acq_cross_mask].copy()
        rows_removed = int(acq_cross_mask.sum())

        # Update dedup_method for the GT records that were matched
        gt_matched = df["email"].isin(cross_emails)
        df.loc[gt_matched, "dedup_method"] = "email_match"

        logger.warning(
            "[%s] %d cross-company email matches found — %d AcquiredCo rows quarantined",
            src, len(cross_emails), rows_removed,
        )
    else:
        logger.info("[%s] No cross-company email matches found", src)

    report: DeduplicationReport = {
        "pass_name":        src,
        "input_rows":       input_rows,
        "output_rows":      len(df),
        "rows_removed":     rows_removed,
        "rows_quarantined": rows_removed,
        "details": {
            "cross_company_email_matches": len(cross_emails),
            "acqco_rows_removed":          rows_removed,
        },
    }
    logger.info("[%s] Complete — %d in, %d out", src, input_rows, len(df))
    return df, quarantine, report

def _pass3_fuzzy_name(
    employees: pd.DataFrame,
    *,
    threshold:       int = _FUZZY_THRESHOLD,
    date_window_days: int = _DATE_WINDOW_DAYS,
) -> tuple[pd.DataFrame, pd.DataFrame, DeduplicationReport]:

    src = "pass3_fuzzy_name"
    df = employees.copy()
    input_rows = len(df)

    gt  = df[df["source_system"] == "GlobalTech_HRIS"].copy()
    aq  = df[df["source_system"] == "AcquiredCo_HRIS"].copy()

    if gt.empty or aq.empty:
        logger.info("[%s] Skipped — no cross-company pairs to compare", src)
        report: DeduplicationReport = {
            "pass_name": src, "input_rows": input_rows, "output_rows": len(df),
            "rows_removed": 0, "rows_quarantined": 0,
            "details": {"probable_match_pairs": 0, "acqco_rows_flagged": 0},
        }
        return df, pd.DataFrame(), report

    gt["_full_name"]   = gt["first_name"].str.strip() + " " + gt["last_name"].str.strip()
    aq["_full_name"]   = aq["first_name"].str.strip() + " " + aq["last_name"].str.strip()
    gt["_hire_ts"]     = pd.to_datetime(gt["hire_date"], errors="coerce")
    aq["_hire_ts"]     = pd.to_datetime(aq["hire_date"], errors="coerce")

    window = pd.Timedelta(days=date_window_days)
    probable_pairs: list[dict] = []
    flagged_acq_ids: set[str]  = set()

    total_comparisons = 0
    gt_sorted         = gt.sort_values("_hire_ts")
    gt_dates          = gt_sorted["_hire_ts"]

    for _, acq_row in aq.iterrows():
        acq_date = acq_row["_hire_ts"]
        if pd.isna(acq_date):
            continue

        # Block: GT employees within ±window days
        lo  = acq_date - window
        hi  = acq_date + window
        block = gt_sorted.loc[(gt_dates >= lo) & (gt_dates <= hi)]

        for _, gt_row in block.iterrows():
            score = fuzz.token_sort_ratio(acq_row["_full_name"], gt_row["_full_name"])
            total_comparisons += 1
            if score >= threshold:
                probable_pairs.append({
                    "acq_employee_id":   acq_row["employee_id"],
                    "gt_employee_id":    gt_row["employee_id"],
                    "acq_full_name":     acq_row["_full_name"],
                    "gt_full_name":      gt_row["_full_name"],
                    "match_score":       score,
                    "acq_hire_date":     acq_row["hire_date"],
                    "gt_hire_date":      gt_row["hire_date"],
                    "hire_date_delta_days": abs((acq_date - gt_row["_hire_ts"]).days),
                    "acq_email":         acq_row.get("email", ""),
                    "gt_email":          gt_row.get("email", ""),
                    "acq_country":       acq_row.get("country", ""),
                    "gt_country":        gt_row.get("country", ""),
                })
                flagged_acq_ids.add(acq_row["employee_id"])

    probable_matches = pd.DataFrame(probable_pairs).sort_values(
        "match_score", ascending=False
    ).reset_index(drop=True) if probable_pairs else pd.DataFrame()

    # Flag matched ACQ rows in primary DataFrame (do NOT remove)
    if flagged_acq_ids:
        flag_mask = df["employee_id"].isin(flagged_acq_ids)
        df.loc[flag_mask, "dedup_method"] = "fuzzy_name"

    logger.info(
        "[%s] %d comparisons across %d ACQ × GT date-blocked pairs → "
        "%d probable matches (score ≥ %d), %d ACQ rows flagged",
        src, total_comparisons, len(aq), len(probable_pairs),
        threshold, len(flagged_acq_ids),
    )
    if not probable_pairs:
        logger.info("[%s] No fuzzy matches found above threshold %d", src, threshold)

    report: DeduplicationReport = {
        "pass_name":        src,
        "input_rows":       input_rows,
        "output_rows":      len(df),
        "rows_removed":     0,
        "rows_quarantined": 0,
        "details": {
            "total_comparisons":         total_comparisons,
            "probable_match_pairs":      len(probable_pairs),
            "acqco_rows_flagged":        len(flagged_acq_ids),
            "fuzzy_threshold":           threshold,
            "date_window_days":          date_window_days,
        },
    }
    return df, probable_matches, report

def _detect_ghost_employees(
    payroll:   pd.DataFrame,
    hris_ids:  set[str],
) -> tuple[pd.DataFrame, DeduplicationReport]:

    src = "ghost_employee_detection"
    ghost_mask = ~payroll["employee_id"].isin(hris_ids)
    ghosts = payroll[ghost_mask].copy()
    ghosts["ghost_employee"] = True

    count = len(ghosts)
    if count:
        logger.warning(
            "[%s] %d payroll records have no corresponding HRIS entry — "
            "flagged as ghost_employee=True (compliance/fraud risk)",
            src, count,
        )
        logger.warning(
            "[%s] Ghost IDs (first 10): %s",
            src, ghosts["employee_id"].head(10).tolist(),
        )
    else:
        logger.info("[%s] No ghost employees detected", src)

    report: DeduplicationReport = {
        "pass_name":        src,
        "input_rows":       len(payroll),
        "output_rows":      len(payroll) - count,
        "rows_removed":     0,
        "rows_quarantined": count,
        "details": {
            "ghost_employees_detected": count,
            "hris_employees_not_in_payroll": len(hris_ids - set(payroll["employee_id"])),
        },
    }
    return ghosts, report

def _dedup_benefits_current(
    benefits: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:

    sorted_ben = benefits.sort_values("enrollment_date", na_position="first")
    current    = sorted_ben.drop_duplicates(
        subset=["employee_id", "plan_type"], keep="last"
    ).reset_index(drop=True)
    history    = sorted_ben[
        sorted_ben.duplicated(subset=["employee_id", "plan_type"], keep="last")
    ].reset_index(drop=True)

    archived = len(history)
    if archived:
        logger.info(
            "[dedup_benefits] Archived %d older benefit enrollments — "
            "%d current records retained",
            archived, len(current),
        )
    return current, history

def dedup_all(
    clean_result: dict[str, pd.DataFrame],
) -> dict:
    """
    Run the full deduplication pipeline on cleaned DataFrames.

    Parameters
    ----------
    clean_result : dict
        Output of clean.clean_all() — keys "employees", "payroll", "benefits".

    Returns
    -------
    dict with keys:
        "employees"         — deduplicated employee DataFrame with provenance columns
        "payroll"           — pass-through (deduplication done in clean layer)
        "benefits"          — current (most-recent) enrollment per employee+plan
        "benefits_history"  — superseded benefit enrollments
        "probable_matches"  — fuzzy-match candidate pairs for HR review
        "ghost_employees"   — payroll rows with no HRIS counterpart
        "reports"           — list[DeduplicationReport] one per pass
    """
    employees = clean_result["employees"].copy()
    payroll   = clean_result["payroll"].copy()
    benefits  = clean_result["benefits"].copy()

    reports: list[DeduplicationReport] = []

    # ── Pass 1: Exact ID ─────────────────────────────────────────────────
    employees, p1_quarantine, p1_report = _pass1_exact_id(employees, payroll, benefits)
    reports.append(p1_report)

    # ── Pass 2: Email match ───────────────────────────────────────────────
    employees, p2_quarantine, p2_report = _pass2_email_match(employees)
    reports.append(p2_report)

    # ── Pass 3: Fuzzy name ────────────────────────────────────────────────
    employees, probable_matches, p3_report = _pass3_fuzzy_name(employees)
    reports.append(p3_report)

    # ── Ghost employee detection ──────────────────────────────────────────
    hris_ids = set(employees["employee_id"])
    ghost_employees, ghost_report = _detect_ghost_employees(payroll, hris_ids)
    reports.append(ghost_report)

    # ── Benefits current enrollment ───────────────────────────────────────
    benefits_current, benefits_history = _dedup_benefits_current(benefits)

    logger.info("=" * 60)
    logger.info("DEDUPLICATION COMPLETE")
    logger.info("  employees       : %6d rows", len(employees))
    logger.info("  p1 quarantine   : %6d rows (ID collisions)", len(p1_quarantine))
    logger.info("  p2 quarantine   : %6d rows (email matches)", len(p2_quarantine))
    logger.info("  probable matches: %6d pairs (fuzzy — for HR review)", len(probable_matches))
    logger.info("  ghost employees : %6d rows (compliance risk)", len(ghost_employees))
    logger.info("  benefits current: %6d rows (%d archived)",
                len(benefits_current), len(benefits_history))
    logger.info("=" * 60)

    return {
        "employees":        employees,
        "payroll":          payroll,
        "benefits":         benefits_current,
        "benefits_history": benefits_history,
        "p1_quarantine":    p1_quarantine,
        "p2_quarantine":    p2_quarantine,
        "probable_matches": probable_matches,
        "ghost_employees":  ghost_employees,
        "reports":          reports,
    }

if __name__ == "__main__":
    from ingest import ingest_all
    from clean import clean_all

    raw     = ingest_all()
    cleaned = clean_all(raw)
    result  = dedup_all(cleaned)

    emp = result["employees"]
    print("\n--- employees with provenance (first 4 rows) ---")
    print(emp[["employee_id","source_system","first_name","last_name",
               "source_systems","dedup_method"]].head(4).to_string())

    print("\n--- dedup_method value counts ---")
    print(emp["dedup_method"].value_counts().to_string())

    print("\n--- source_systems distribution ---")
    print(emp["source_systems"].value_counts().head(8).to_string())

    if not result["probable_matches"].empty:
        print("\n--- probable matches (top 5) ---")
        print(result["probable_matches"].head(5).to_string())
    else:
        print("\n--- probable matches: none found ---")

    if not result["ghost_employees"].empty:
        print("\n--- ghost employees (first 5) ---")
        print(result["ghost_employees"].head(5).to_string())
    else:
        print("\n--- ghost employees: none detected ---")

    print("\n--- benefits: current vs history ---")
    print(f"  current : {len(result['benefits'])}")
    print(f"  history : {len(result['benefits_history'])}")

    print("\n--- dedup reports ---")
    for rep in result["reports"]:
        print(f"\n{rep['pass_name']}")
        print(f"  rows     : {rep['input_rows']} → {rep['output_rows']} (removed {rep['rows_removed']})")
        print(f"  quarantine: {rep['rows_quarantined']}")
        print(f"  details  : {rep['details']}")
