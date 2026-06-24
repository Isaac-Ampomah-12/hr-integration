import re
import unicodedata
from datetime import date
from typing import TypedDict

import pandas as pd

from config import CONFIG, logger

class CleanReport(TypedDict):
    source:         str
    input_rows:     int
    output_rows:    int
    rows_dropped:   int
    fixes_applied:  dict[str, int]
    issues_flagged: dict[str, int]

def _normalize_name_token(token: str) -> str:

    if not token:
        return token
    lo = token.lower()
    if lo.startswith("mc") and len(token) > 2:
        return "Mc" + token[2:].capitalize()
    if lo.startswith("mac") and len(token) > 3:
        return "Mac" + token[3:].capitalize()
    return token.capitalize()


def _normalize_name(name: str) -> str:

    if not isinstance(name, str) or not name.strip():
        return name

    # NFC: canonical composition — ensures accented glyphs are single code points
    name = unicodedata.normalize("NFC", name.strip())

    # Split on spaces and hyphens, preserving the delimiters for reconstruction
    segments = re.split(r"([ \-])", name)
    result = []
    for seg in segments:
        if seg in (" ", "-", ""):
            result.append(seg)
            continue
        # Handle apostrophe within a segment (O'Brien → O + ' + Brien)
        parts = seg.split("'")
        result.append("'".join(_normalize_name_token(p) for p in parts))

    return "".join(result)


# ── 2. Employee ID namespacing ────────────────────────────────────────────

_ID_DIGIT_RE = re.compile(r"(\d+)$")


def _format_employee_id(raw_id: str, source_system: str) -> str:

    prefix = CONFIG["employee_id_prefix"].get(source_system, "XX")
    m = _ID_DIGIT_RE.search(str(raw_id))
    if not m:
        return f"{prefix}-{raw_id}"
    return f"{prefix}-{int(m.group(1)):06d}"


def _format_manager_id(raw_mgr: str) -> str:

    if not raw_mgr or str(raw_mgr).strip() in ("", "nan"):
        return ""
    s = str(raw_mgr).strip()
    m = _ID_DIGIT_RE.search(s)
    if not m:
        return s
    num = int(m.group(1))
    if s.upper().startswith("ACQ"):
        return f"AC-{num:06d}"
    return f"GT-{num:06d}"


# ── 3. Date parsing ───────────────────────────────────────────────────────

_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"]


def _parse_dates(series: pd.Series) -> pd.Series:

    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    remaining = series.notna() & (series.astype(str).str.strip() != "")

    for fmt in _DATE_FORMATS:
        if not remaining.any():
            break
        parsed = pd.to_datetime(series[remaining], format=fmt, errors="coerce")
        matched = parsed.notna()
        result[remaining & matched.reindex(series.index, fill_value=False)] = parsed[matched].values
        remaining &= ~matched.reindex(series.index, fill_value=False)

    return result


# ── 4. Salary parsing ─────────────────────────────────────────────────────

def _parse_salary(series: pd.Series) -> pd.Series:

    cleaned = (
        series.astype(str)
        .str.replace(r"[$£€]", "", regex=True)
        .str.replace(",", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


# ── 5. String whitespace strip ────────────────────────────────────────────

def _strip_string_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:

    changed = 0
    for col in df.select_dtypes(include=["object", "str"]).columns:
        stripped = df[col].str.strip()
        diff = (stripped != df[col]) & df[col].notna()
        changed += int(diff.sum())
        df[col] = stripped
    return df, changed

def clean_employees(df: pd.DataFrame) -> tuple[pd.DataFrame, CleanReport]:

    src = "clean_employees"
    df = df.copy()
    input_rows = len(df)
    fixes: dict[str, int] = {}
    issues: dict[str, int] = {}

    # ── 1. Whitespace strip ─────────────────────────────────────────────
    df, stripped = _strip_string_columns(df)
    if stripped:
        fixes["whitespace_stripped_cells"] = stripped
        logger.info("[%s] Stripped whitespace in %d cells", src, stripped)

    # ── 2. Name normalization ────────────────────────────────────────────
    df["first_name_raw"] = df["first_name"]
    df["last_name_raw"]  = df["last_name"]
    df["first_name"] = df["first_name"].apply(_normalize_name)
    df["last_name"]  = df["last_name"].apply(_normalize_name)

    name_changes = int(
        ((df["first_name"] != df["first_name_raw"]) & df["first_name_raw"].notna()).sum()
        + ((df["last_name"]  != df["last_name_raw"])  & df["last_name_raw"].notna()).sum()
    )
    if name_changes:
        fixes["names_normalized"] = name_changes
        logger.info("[%s] Normalized %d name values", src, name_changes)
    else:
        logger.info("[%s] All names already well-formed (no changes)", src)

    # ── 3. Employee ID namespacing ───────────────────────────────────────
    df["employee_id"] = df.apply(
        lambda r: _format_employee_id(r["employee_id"], r["source_system"]), axis=1
    )
    df["manager_id"] = df["manager_id"].apply(_format_manager_id)
    fixes["employee_ids_namespaced"] = len(df)
    logger.info("[%s] Namespaced %d employee_ids and manager_ids (GT-/AC- scheme)", src, len(df))

    # ── 4. Department taxonomy mapping ───────────────────────────────────
    dept_map = CONFIG["department_map"]
    original_dept = df["department"].copy()
    df["department"] = df["department"].map(dept_map)

    # Rows where map returned NaN: either was already NaN (null) or unmapped string
    was_not_null    = original_dept.notna()
    now_null        = df["department"].isna()
    unmapped_mask   = was_not_null & now_null

    if unmapped_mask.sum():
        unmapped_vals = original_dept[unmapped_mask].value_counts().to_dict()
        issues["unmapped_department"] = int(unmapped_mask.sum())
        logger.warning("[%s] %d unmapped department values: %s", src, unmapped_mask.sum(), unmapped_vals)
        # Restore original value so data is not silently lost
        df.loc[unmapped_mask, "department"] = original_dept[unmapped_mask]

    # Report nulls that came from the source (not from failed mapping)
    null_dept = df["department"].isna().sum()
    if null_dept:
        issues["null_department"] = int(null_dept)
        logger.warning(
            "[%s] %d rows have null department (sources: %s)",
            src, null_dept,
            df.loc[df["department"].isna(), "source_system"].value_counts().to_dict(),
        )

    mapped_count = int(was_not_null.sum()) - int(unmapped_mask.sum())
    fixes["departments_mapped"] = mapped_count
    logger.info("[%s] Mapped %d department values to standard taxonomy", src, mapped_count)

    # ── 5. Null country ──────────────────────────────────────────────────
    null_country = df["country"].isna().sum()
    if null_country:
        issues["null_country"] = int(null_country)
        logger.warning(
            "[%s] %d rows have null country (sources: %s)",
            src, null_country,
            df.loc[df["country"].isna(), "source_system"].value_counts().to_dict(),
        )

    # ── 6. Email validation ──────────────────────────────────────────────
    email_re = CONFIG["email_regex"]
    bad_email_mask = ~df["email"].str.match(email_re, na=False)
    bad_email_count = int(bad_email_mask.sum())
    if bad_email_count:
        issues["invalid_email"] = bad_email_count
        logger.warning("[%s] %d rows have malformed email addresses", src, bad_email_count)

    # ── 7. Employment type / status allow-lists ──────────────────────────
    bad_type = int((~df["employment_type"].isin(CONFIG["valid_employment_types"]) & df["employment_type"].notna()).sum())
    if bad_type:
        issues["invalid_employment_type"] = bad_type
        logger.warning("[%s] %d rows have unexpected employment_type", src, bad_type)

    bad_status = int((~df["employment_status"].isin(CONFIG["valid_employment_statuses"]) & df["employment_status"].notna()).sum())
    if bad_status:
        issues["invalid_employment_status"] = bad_status
        logger.warning("[%s] %d rows have unexpected employment_status", src, bad_status)

    # ── 8. hire_date standardization ────────────────────────────────────
    df["hire_date"] = _parse_dates(df["hire_date"].astype(str))
    unparsed_dates = int(df["hire_date"].isna().sum())
    if unparsed_dates:
        issues["unparseable_hire_date"] = unparsed_dates
        logger.warning("[%s] %d hire_date values could not be parsed", src, unparsed_dates)
    else:
        fixes["hire_date_parsed"] = len(df)

    date_min = pd.Timestamp(CONFIG["hire_date_min"])
    date_max = pd.Timestamp(date.today())
    implausible = df["hire_date"].notna() & (
        (df["hire_date"] < date_min) | (df["hire_date"] > date_max)
    )
    if implausible.sum():
        issues["implausible_hire_date"] = int(implausible.sum())
        logger.warning(
            "[%s] %d hire dates outside plausible range (%s – %s)",
            src, implausible.sum(), date_min.date(), date_max.date(),
        )

    report: CleanReport = {
        "source":         src,
        "input_rows":     input_rows,
        "output_rows":    len(df),
        "rows_dropped":   0,
        "fixes_applied":  fixes,
        "issues_flagged": issues,
    }
    logger.info(
        "[%s] Complete — %d rows in/out, fixes=%s, issues=%s",
        src, len(df), fixes, issues,
    )
    return df, report


# ---------------------------------------------------------------------------
# clean_payroll
# ---------------------------------------------------------------------------

def clean_payroll(df: pd.DataFrame) -> tuple[pd.DataFrame, CleanReport]:

    src = "clean_payroll"
    df = df.copy()
    input_rows = len(df)
    fixes: dict[str, int] = {}
    issues: dict[str, int] = {}

    # ── 1. Parse base_salary ─────────────────────────────────────────────
    original_salary = df["base_salary"].copy()
    df["base_salary"] = _parse_salary(df["base_salary"])

    was_formatted   = pd.to_numeric(original_salary, errors="coerce").isna()
    parsed_ok       = was_formatted & df["base_salary"].notna()
    salary_parsed   = int(parsed_ok.sum())
    if salary_parsed:
        fixes["salary_symbols_stripped"] = salary_parsed
        logger.info("[%s] Parsed %d formatted salary strings (e.g. '$70,315' → 70315.0)", src, salary_parsed)

    null_salary = int(df["base_salary"].isna().sum())
    if null_salary:
        issues["unparseable_salary"] = null_salary
        logger.warning("[%s] %d rows have unparseable base_salary after cleaning", src, null_salary)

    non_positive = int((df["base_salary"].notna() & (df["base_salary"] <= 0)).sum())
    if non_positive:
        issues["non_positive_salary"] = non_positive
        logger.warning("[%s] %d rows have salary ≤ 0", src, non_positive)

    # ── 2. Employee ID namespacing ───────────────────────────────────────
    # Payroll source column uses "GlobalTech" / "AcquiredCo"; map to system names
    _payroll_source_to_system = {
        "GlobalTech": "GlobalTech_HRIS",
        "AcquiredCo": "AcquiredCo_HRIS",
    }
    df["employee_id"] = df.apply(
        lambda r: _format_employee_id(
            r["employee_id"],
            _payroll_source_to_system.get(r["source"], r["source"]),
        ),
        axis=1,
    )
    fixes["payroll_ids_namespaced"] = len(df)
    logger.info("[%s] Namespaced %d payroll employee_ids (GT-/AC- scheme)", src, len(df))

    # ── 3. Validate currency ─────────────────────────────────────────────
    valid_ccy = set(CONFIG["valid_currencies"])
    bad_ccy   = int((~df["currency"].isin(valid_ccy) & df["currency"].notna()).sum())
    if bad_ccy:
        issues["invalid_currency"] = bad_ccy
        logger.warning("[%s] %d rows have unexpected currency: %s", src, bad_ccy,
                       df.loc[~df["currency"].isin(valid_ccy), "currency"].value_counts().to_dict())

    # ── 4. Currency → USD conversion ────────────────────────────────────
    fx = CONFIG["fx_rates_to_usd"]
    df["base_salary_usd"] = df["base_salary"] * df["currency"].map(fx)

    no_rate = int((df["base_salary"].notna() & df["base_salary_usd"].isna()).sum())
    if no_rate:
        issues["missing_fx_rate"] = no_rate
        logger.warning("[%s] %d rows could not be converted to USD (no FX rate)", src, no_rate)
    else:
        fixes["salary_converted_to_usd"] = int(df["base_salary_usd"].notna().sum())
        logger.info("[%s] Converted %d salaries to USD", src, fixes["salary_converted_to_usd"])

    # ── 5. Pay-frequency → annual normalization ──────────────────────────
    mult = CONFIG["pay_frequency_multiplier"]
    df["salary_usd_annual"] = df["base_salary_usd"] * df["pay_frequency"].map(mult)

    unmapped_freq = int((df["pay_frequency"].notna() & ~df["pay_frequency"].isin(mult)).sum())
    if unmapped_freq:
        issues["unmapped_pay_frequency"] = unmapped_freq
        logger.warning("[%s] %d rows have unmapped pay_frequency — salary_usd_annual is NaN for those",
                       src, unmapped_freq)
    else:
        fixes["salary_usd_annual_computed"] = int(df["salary_usd_annual"].notna().sum())
        logger.info("[%s] Computed salary_usd_annual for %d rows", src, fixes["salary_usd_annual_computed"])

    # ── 6. effective_date → datetime64 ───────────────────────────────────
    df["effective_date"] = _parse_dates(df["effective_date"].astype(str))
    bad_eff_date = int(df["effective_date"].isna().sum())
    if bad_eff_date:
        issues["unparseable_effective_date"] = bad_eff_date
        logger.warning("[%s] %d rows have unparseable effective_date", src, bad_eff_date)
    else:
        fixes["effective_date_parsed"] = len(df)

    # ── 7. Deduplicate (employee_id, source) — keep most-recent ──────────
    pre_dedup = len(df)
    df = (
        df.sort_values("effective_date", na_position="first")
          .drop_duplicates(subset=["employee_id", "source"], keep="last")
          .sort_index()
          .reset_index(drop=True)
    )
    rows_dropped = pre_dedup - len(df)
    if rows_dropped:
        fixes["payroll_duplicates_removed"] = rows_dropped
        logger.info("[%s] Removed %d duplicate (employee_id, source) rows", src, rows_dropped)

    report: CleanReport = {
        "source":         src,
        "input_rows":     input_rows,
        "output_rows":    len(df),
        "rows_dropped":   rows_dropped,
        "fixes_applied":  fixes,
        "issues_flagged": issues,
    }
    logger.info(
        "[%s] Complete — %d rows in, %d out (%d dropped), fixes=%s, issues=%s",
        src, input_rows, len(df), rows_dropped, fixes, issues,
    )
    return df, report


# ---------------------------------------------------------------------------
# clean_benefits
# ---------------------------------------------------------------------------

def clean_benefits(df: pd.DataFrame) -> tuple[pd.DataFrame, CleanReport]:

    src = "clean_benefits"
    df = df.copy()
    input_rows = len(df)
    fixes: dict[str, int] = {}
    issues: dict[str, int] = {}

    # ── 1. Employee ID namespacing (GlobalTech only) ──────────────────────
    df["employee_id"] = df["employee_id"].apply(
        lambda eid: _format_employee_id(eid, "GlobalTech_HRIS")
    )
    fixes["benefits_ids_namespaced"] = len(df)
    logger.info("[%s] Namespaced %d benefits employee_ids (GT- scheme)", src, len(df))

    # ── 2. enrollment_date → datetime64 ──────────────────────────────────
    df["enrollment_date"] = _parse_dates(df["enrollment_date"].astype(str))
    bad_date = int(df["enrollment_date"].isna().sum())
    if bad_date:
        issues["unparseable_enrollment_date"] = bad_date
        logger.warning("[%s] %d rows have unparseable enrollment_date", src, bad_date)
    else:
        fixes["enrollment_date_parsed"] = len(df)

    # ── 3. Premium validation ─────────────────────────────────────────────
    neg_emp = int((df["premium_employee"] < 0).sum())
    if neg_emp:
        issues["negative_premium_employee"] = neg_emp
        logger.warning("[%s] %d rows have negative premium_employee", src, neg_emp)

    neg_er = int((df["premium_employer"] < 0).sum())
    if neg_er:
        issues["negative_premium_employer"] = neg_er
        logger.warning("[%s] %d rows have negative premium_employer", src, neg_er)

    report: CleanReport = {
        "source":         src,
        "input_rows":     input_rows,
        "output_rows":    len(df),
        "rows_dropped":   0,
        "fixes_applied":  fixes,
        "issues_flagged": issues,
    }
    logger.info(
        "[%s] Complete — %d rows in/out, fixes=%s, issues=%s",
        src, len(df), fixes, issues,
    )
    return df, report


# ---------------------------------------------------------------------------
# Convenience entry-point
# ---------------------------------------------------------------------------

def clean_all(
    ingest_result: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame | list[CleanReport]]:

    emp_clean, emp_report = clean_employees(ingest_result["employees"])
    pay_clean, pay_report = clean_payroll(ingest_result["payroll"])
    ben_clean, ben_report = clean_benefits(ingest_result["benefits"])

    reports = [emp_report, pay_report, ben_report]

    logger.info("=" * 60)
    logger.info("CLEANING COMPLETE")
    logger.info("  employees : %6d rows (dropped %d)", len(emp_clean), emp_report["rows_dropped"])
    logger.info("  payroll   : %6d rows (dropped %d)", len(pay_clean), pay_report["rows_dropped"])
    logger.info("  benefits  : %6d rows (dropped %d)", len(ben_clean), ben_report["rows_dropped"])
    logger.info("=" * 60)

    return {
        "employees": emp_clean,
        "payroll":   pay_clean,
        "benefits":  ben_clean,
        "reports":   reports,
    }


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from ingest import ingest_all

    raw     = ingest_all()
    cleaned = clean_all(raw)

    emp = cleaned["employees"]
    pay = cleaned["payroll"]
    ben = cleaned["benefits"]

    print("\n--- employees sample ---")
    print(emp[["employee_id","source_system","first_name","last_name",
               "department","hire_date","manager_id"]].head(4).to_string())

    print("\n--- payroll sample (salary columns) ---")
    print(pay[["employee_id","source","base_salary","currency",
               "base_salary_usd","pay_frequency","salary_usd_annual",
               "effective_date"]].head(4).to_string())

    print("\n--- benefits sample ---")
    print(ben[["employee_id","plan_type","enrollment_date"]].head(4).to_string())

    print("\n--- clean reports ---")
    for rep in cleaned["reports"]:
        print(f"\n{rep['source']}")
        print(f"  rows  : {rep['input_rows']} → {rep['output_rows']} (dropped {rep['rows_dropped']})")
        print(f"  fixed : {rep['fixes_applied']}")
        print(f"  issues: {rep['issues_flagged']}")
