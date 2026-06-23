"""
validate.py — Data Quality Validation Module (Deliverable 4)
GlobalTech Corp HR Integration Pipeline

DataQualityValidator class running 15 checks across 7 categories:

  NOT NULL (6)              employee_id, first_name, last_name, email, department, country
  UNIQUE (2)                employee_id, email  (post-dedup)
  VALUES IN SET (2)         employment_type ∈ {Full-Time, Part-Time, Contractor}
                            currency ∈ {USD, EUR, GBP}
  REGEX (2)                 email format, employee_id format (GT-/AC- + 6 digits)
  NUMERIC RANGE (1)         salary_usd_annual  15 000 – 2 000 000
  DATE RANGE (1)            hire_date  1970-01-01 – today
  REFERENTIAL INTEGRITY (1) every non-empty manager_id must exist as an employee_id

Pipeline gate: halt if more than 2 checks fail.
Outputs written to CONFIG["output_dir"]:
  validation_report.csv
  validation_report.html
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd

from config import CONFIG, logger


# ---------------------------------------------------------------------------
# HTML report template (uses %-substitution to avoid CSS-brace escaping)
# ---------------------------------------------------------------------------

# Placeholders: __SUMMARY__ __ROWS__ __GENERATED__
# Using __NAME__ sentinels avoids CSS-brace escaping issues with % or .format()
_HTML_TEMPLATE = (
    "<!DOCTYPE html>\n"
    "<html lang='en'>\n"
    "<head>\n"
    "  <meta charset='UTF-8'>\n"
    "  <title>HR Integration — Data Quality Report</title>\n"
    "  <style>\n"
    "    body     { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;\n"
    "               margin: 2em auto; max-width: 1140px; color: #333; }\n"
    "    h1       { font-size: 1.4em; color: #1a1a2e; margin-bottom: 0.2em; }\n"
    "    h2       { font-size: 1em; font-weight: normal; color: #666; margin-top: 0; }\n"
    "    .summary { display: flex; gap: 1.5em; flex-wrap: wrap;\n"
    "               margin: 1em 0 1.5em; padding: 1em;\n"
    "               background: #f8f9fa; border-radius: 6px; border: 1px solid #dde; }\n"
    "    .stat    { text-align: center; min-width: 80px; }\n"
    "    .val     { font-size: 2em; font-weight: 700; line-height: 1.1; }\n"
    "    .lbl     { font-size: 0.75em; color: #777; margin-top: 0.2em; }\n"
    "    .gate    { margin: auto; padding: 0.4em 0.8em; border-radius: 4px;\n"
    "               font-weight: 700; font-size: 0.95em; }\n"
    "    .gate-pass { background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; }\n"
    "    .gate-fail { background: #ffebee; color: #c62828; border: 1px solid #ef9a9a; }\n"
    "    table    { border-collapse: collapse; width: 100%; font-size: 0.88em; }\n"
    "    th       { background: #1a1a2e; color: #fff; text-align: left;\n"
    "               padding: 10px 14px; white-space: nowrap; }\n"
    "    td       { padding: 8px 14px; border-bottom: 1px solid #eaecf0;\n"
    "               vertical-align: top; }\n"
    "    tr:last-child td { border-bottom: none; }\n"
    "    tr:hover td { background: #f5f7ff; }\n"
    "    .num     { text-align: right; font-variant-numeric: tabular-nums;\n"
    "               white-space: nowrap; }\n"
    "    .tag     { display: inline-block; padding: 2px 7px; border-radius: 3px;\n"
    "               font-size: 0.82em; font-weight: 600; letter-spacing: 0.03em; }\n"
    "    .pass    { background: #e8f5e9; color: #2e7d32; }\n"
    "    .fail    { background: #ffebee; color: #c62828; }\n"
    "    .cid     { font-family: 'Courier New', monospace; font-size: 0.82em; color: #555; }\n"
    "    .footer  { margin-top: 2em; font-size: 0.78em; color: #aaa; }\n"
    "  </style>\n"
    "</head>\n"
    "<body>\n"
    "  <h1>GlobalTech Corp — HR Integration Pipeline</h1>\n"
    "  <h2>Data Quality Validation Report</h2>\n"
    "  __SUMMARY__\n"
    "  <table>\n"
    "    <thead>\n"
    "      <tr>\n"
    "        <th>Check ID</th>\n"
    "        <th>Description</th>\n"
    "        <th style='text-align:right'>Total</th>\n"
    "        <th style='text-align:right'>Passed</th>\n"
    "        <th style='text-align:right'>Failed</th>\n"
    "        <th style='text-align:right'>Pass Rate</th>\n"
    "        <th>Status</th>\n"
    "      </tr>\n"
    "    </thead>\n"
    "    <tbody>\n"
    "      __ROWS__\n"
    "    </tbody>\n"
    "  </table>\n"
    "  <div class='footer'>\n"
    "    Generated __GENERATED__ &middot; GlobalTech Corp HR Integration Pipeline\n"
    "  </div>\n"
    "</body>\n"
    "</html>\n"
)


# ---------------------------------------------------------------------------
# DataQualityValidator
# ---------------------------------------------------------------------------

class DataQualityValidator:
    """
    Runs 15 data-quality checks against the post-deduplication DataFrames.

    Usage
    -----
    validator = DataQualityValidator(employees, payroll, benefits)
    report    = validator.validate()          # pd.DataFrame, 15 rows
    gate_ok   = validator.gate(report)        # True = pipeline may continue
    csv, html = validator.export(report)      # write output files
    """

    GATE_THRESHOLD = 2      # halt if strictly more than this many checks fail
    SALARY_LO      = 15_000
    SALARY_HI      = 2_000_000
    EMP_ID_PATTERN = r"^(GT|AC)-\d{6}$"

    def __init__(
        self,
        employees: pd.DataFrame,
        payroll:   pd.DataFrame,
        benefits:  pd.DataFrame,
    ) -> None:
        self._emp  = employees.copy()
        self._pay  = payroll.copy()
        self._ben  = benefits.copy()
        self._results: list[dict] = []

    # ── Private: record one check result ─────────────────────────────────

    def _record(
        self,
        check: str,
        description: str,
        total: int,
        passed: int,
    ) -> None:
        failed   = total - passed
        pass_rate = round(passed / total, 6) if total > 0 else 0.0
        status   = "PASS" if failed == 0 else "FAIL"
        self._results.append({
            "check":       check,
            "description": description,
            "total":       total,
            "passed":      passed,
            "failed":      failed,
            "pass_rate":   pass_rate,
            "status":      status,
        })
        logger.info(
            "[validate] %-45s  total=%d  passed=%d  failed=%d  %s",
            check, total, passed, failed, status,
        )

    # ── Private: normalise a string Series (treat "" as null) ────────────

    @staticmethod
    def _present(series: pd.Series) -> pd.Series:
        """Boolean mask: True where value is not NA and not blank."""
        return series.notna() & (series.astype(str).str.strip() != "")

    # ── Private: individual check implementations ─────────────────────────

    def _check_not_null(self, df: pd.DataFrame, col: str) -> None:
        total  = len(df)
        passed = int(self._present(df[col]).sum())
        self._record(
            f"not_null.{col}",
            f"{col} must not be null or blank",
            total, passed,
        )

    def _check_unique(self, df: pd.DataFrame, col: str) -> None:
        total    = len(df)
        dup_rows = int(df.duplicated(subset=[col], keep="first").sum())
        passed   = total - dup_rows
        self._record(
            f"unique.{col}",
            f"{col} must be unique across all records (post-dedup)",
            total, passed,
        )

    def _check_values_in_set(
        self,
        df:        pd.DataFrame,
        col:       str,
        valid_set: set,
        *,
        df_label:  str = "",
    ) -> None:
        non_null = df.loc[self._present(df[col]), col]
        total    = len(non_null)
        passed   = int(non_null.isin(valid_set).sum())
        self._record(
            f"values_in_set.{col}",
            f"{col} must be one of: {', '.join(sorted(valid_set))}",
            total, passed,
        )

    def _check_regex(
        self,
        df:          pd.DataFrame,
        col:         str,
        pattern:     str,
        description: str,
    ) -> None:
        present = df.loc[self._present(df[col]), col].astype(str)
        total   = len(present)
        passed  = int(present.str.match(pattern).sum())
        self._record(
            f"regex.{col}",
            description,
            total, passed,
        )

    def _check_numeric_range(
        self,
        df:  pd.DataFrame,
        col: str,
        lo:  float,
        hi:  float,
    ) -> None:
        numeric = pd.to_numeric(df[col], errors="coerce").dropna()
        total   = len(numeric)
        passed  = int(((numeric >= lo) & (numeric <= hi)).sum())
        self._record(
            f"numeric_range.{col}",
            f"{col} must be between {lo:,.0f} and {hi:,.0f}",
            total, passed,
        )

    def _check_date_range(
        self,
        df:  pd.DataFrame,
        col: str,
        lo:  str,
        hi:  str,
    ) -> None:
        dates  = pd.to_datetime(df[col], errors="coerce").dropna()
        total  = len(dates)
        lo_ts  = pd.Timestamp(lo)
        hi_ts  = pd.Timestamp(hi)
        passed = int(((dates >= lo_ts) & (dates <= hi_ts)).sum())
        self._record(
            f"date_range.{col}",
            f"{col} must be between {lo} and {hi}",
            total, passed,
        )

    def _check_referential_integrity(
        self,
        df:      pd.DataFrame,
        fk_col:  str,
        ref_set: set,
    ) -> None:
        non_null = df.loc[self._present(df[fk_col]), fk_col].astype(str)
        total    = len(non_null)
        passed   = int(non_null.isin(ref_set).sum())
        missing  = total - passed
        if missing:
            orphan_ids = non_null[~non_null.isin(ref_set)].unique()[:5]
            logger.warning(
                "[validate] referential_integrity.%s — %d orphan IDs "
                "(first 5: %s)", fk_col, missing, orphan_ids.tolist(),
            )
        self._record(
            f"referential_integrity.{fk_col}",
            f"Every non-blank {fk_col} must exist as an employee_id",
            total, passed,
        )

    # ── Public: run all checks ────────────────────────────────────────────

    def validate(self) -> pd.DataFrame:
        """
        Execute all 15 checks and return a report DataFrame with columns:
        check, description, total, passed, failed, pass_rate, status
        """
        self._results.clear()
        emp = self._emp
        pay = self._pay

        logger.info("[validate] Running data quality checks …")

        # ── 1–6  NOT NULL on employees ────────────────────────────────────
        for col in ("employee_id", "first_name", "last_name", "email", "department", "country"):
            self._check_not_null(emp, col)

        # ── 7–8  UNIQUE (post-dedup) ──────────────────────────────────────
        self._check_unique(emp, "employee_id")
        self._check_unique(emp, "email")

        # ── 9  VALUES IN SET — employment_type ────────────────────────────
        self._check_values_in_set(
            emp, "employment_type",
            set(CONFIG["valid_employment_types"]),
        )

        # ── 10  VALUES IN SET — currency (payroll) ────────────────────────
        self._check_values_in_set(
            pay, "currency",
            set(CONFIG["valid_currencies"]),
        )

        # ── 11  REGEX — email format ──────────────────────────────────────
        self._check_regex(
            emp, "email",
            CONFIG["email_regex"],
            "email must match valid RFC-style address format",
        )

        # ── 12  REGEX — employee_id format ────────────────────────────────
        self._check_regex(
            emp, "employee_id",
            self.EMP_ID_PATTERN,
            r"employee_id must match GT-\d{6} or AC-\d{6}",
        )

        # ── 13  NUMERIC RANGE — salary_usd_annual ─────────────────────────
        self._check_numeric_range(
            pay, "salary_usd_annual",
            self.SALARY_LO, self.SALARY_HI,
        )

        # ── 14  DATE RANGE — hire_date ────────────────────────────────────
        self._check_date_range(
            emp, "hire_date",
            CONFIG["hire_date_min"],
            str(date.today()),
        )

        # ── 15  REFERENTIAL INTEGRITY — manager_id → employee_id ─────────
        emp_id_set = set(emp["employee_id"].dropna().astype(str))
        self._check_referential_integrity(emp, "manager_id", emp_id_set)

        report = pd.DataFrame(self._results)
        fail_count = int((report["status"] == "FAIL").sum())
        logger.info(
            "[validate] Complete — %d checks, %d passed, %d failed",
            len(report),
            int((report["status"] == "PASS").sum()),
            fail_count,
        )
        return report

    # ── Public: pipeline gate ─────────────────────────────────────────────

    def gate(self, report: pd.DataFrame) -> bool:
        """
        Return True if the pipeline may continue, False if it should halt.
        Logs CRITICAL when fail_count > GATE_THRESHOLD.
        """
        fail_count = int((report["status"] == "FAIL").sum())
        if fail_count > self.GATE_THRESHOLD:
            logger.critical(
                "PIPELINE HALTED — %d checks failed (threshold is %d). "
                "Review Data/processed/validation_report.html before re-running.",
                fail_count, self.GATE_THRESHOLD,
            )
            return False
        if fail_count:
            logger.warning(
                "[validate] Gate: %d check(s) failed but within tolerance (≤ %d). "
                "Pipeline continues.",
                fail_count, self.GATE_THRESHOLD,
            )
        else:
            logger.info("[validate] Gate: all %d checks passed — pipeline continues.", len(report))
        return True

    # ── Public: export ────────────────────────────────────────────────────

    def export(
        self,
        report:     pd.DataFrame,
        output_dir: Path | None = None,
    ) -> tuple[Path, Path]:
        """
        Write validation_report.csv and validation_report.html.
        Returns (csv_path, html_path).
        """
        out = Path(output_dir) if output_dir else Path(CONFIG["output_dir"])
        out.mkdir(parents=True, exist_ok=True)

        csv_path  = out / "validation_report.csv"
        html_path = out / "validation_report.html"

        # CSV
        report.to_csv(csv_path, index=False)
        logger.info("[validate] CSV  → %s", csv_path)

        # HTML
        html = self._build_html(report)
        html_path.write_text(html, encoding="utf-8")
        logger.info("[validate] HTML → %s", html_path)

        return csv_path, html_path

    # ── Private: HTML builder ─────────────────────────────────────────────

    def _build_html(self, report: pd.DataFrame) -> str:
        pass_count = int((report["status"] == "PASS").sum())
        fail_count = int((report["status"] == "FAIL").sum())
        total_chk  = len(report)
        overall_pr = f"{pass_count / total_chk:.0%}" if total_chk else "—"
        gate_ok    = fail_count <= self.GATE_THRESHOLD

        gate_label = (
            f"PASSED ({fail_count} failure{'s' if fail_count != 1 else ''}, "
            f"threshold {self.GATE_THRESHOLD})"
            if gate_ok
            else f"FAILED — PIPELINE HALTED ({fail_count} failures, threshold {self.GATE_THRESHOLD})"
        )
        gate_cls  = "gate-pass" if gate_ok else "gate-fail"

        summary = (
            f'<div class="summary">'
            f'  <div class="stat"><div class="val">{total_chk}</div><div class="lbl">Checks Run</div></div>'
            f'  <div class="stat"><div class="val" style="color:#2e7d32">{pass_count}</div><div class="lbl">Passed</div></div>'
            f'  <div class="stat"><div class="val" style="color:#c62828">{fail_count}</div><div class="lbl">Failed</div></div>'
            f'  <div class="stat"><div class="val">{overall_pr}</div><div class="lbl">Pass Rate</div></div>'
            f'  <div class="gate {gate_cls}">Pipeline Gate: {gate_label}</div>'
            f'</div>'
        )

        rows_parts: list[str] = []
        for _, row in report.iterrows():
            status_cls = "pass" if row["status"] == "PASS" else "fail"
            pr_pct = f'{row["pass_rate"]:.2%}'
            rows_parts.append(
                f'<tr>'
                f'<td><span class="check-id">{row["check"]}</span></td>'
                f'<td>{row["description"]}</td>'
                f'<td class="num">{int(row["total"]):,}</td>'
                f'<td class="num">{int(row["passed"]):,}</td>'
                f'<td class="num">{int(row["failed"]):,}</td>'
                f'<td class="num">{pr_pct}</td>'
                f'<td><span class="tag {status_cls}">{row["status"]}</span></td>'
                f'</tr>'
            )

        return (
            _HTML_TEMPLATE
            .replace("__SUMMARY__",   summary)
            .replace("__ROWS__",      "\n      ".join(rows_parts))
            .replace("__GENERATED__", str(date.today()))
        )


# ---------------------------------------------------------------------------
# Convenience entry-point
# ---------------------------------------------------------------------------

def validate_all(dedup_result: dict) -> tuple[pd.DataFrame, bool]:
    """
    Run all validation checks on the post-dedup DataFrames, enforce the
    pipeline gate, and write CSV + HTML outputs.

    Parameters
    ----------
    dedup_result : dict
        Output of deduplicate.dedup_all() — keys "employees", "payroll", "benefits".

    Returns
    -------
    (report, gate_passed)
        report      — pd.DataFrame with one row per check
        gate_passed — True if pipeline may continue, False if halted
    """
    validator = DataQualityValidator(
        employees=dedup_result["employees"],
        payroll=dedup_result["payroll"],
        benefits=dedup_result["benefits"],
    )
    report      = validator.validate()
    gate_passed = validator.gate(report)
    validator.export(report)
    return report, gate_passed


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from ingest import ingest_all
    from clean import clean_all
    from deduplicate import dedup_all

    raw       = ingest_all()
    cleaned   = clean_all(raw)
    deduped   = dedup_all(cleaned)
    report, ok = validate_all(deduped)

    print("\n" + "=" * 72)
    print("DATA QUALITY REPORT")
    print("=" * 72)
    pd.set_option("display.max_colwidth", 55)
    pd.set_option("display.width", 160)
    print(
        report.to_string(
            index=False,
            formatters={
                "pass_rate": lambda v: f"{v:.2%}",
                "total":     lambda v: f"{int(v):>7,}",
                "passed":    lambda v: f"{int(v):>7,}",
                "failed":    lambda v: f"{int(v):>7,}",
            },
        )
    )
    print("=" * 72)
    print(f"Pipeline gate: {'PASSED' if ok else 'FAILED — HALTED'}")
    print()

    # Show any failed checks with detail
    failed = report[report["status"] == "FAIL"]
    if not failed.empty:
        print("Failed checks:")
        for _, row in failed.iterrows():
            print(f"  ✗ {row['check']}: {row['failed']:,} of {row['total']:,} rows failed")
    else:
        print("All checks passed.")

    print(f"\nOutputs written to: {CONFIG['output_dir']}")
