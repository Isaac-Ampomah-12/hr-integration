from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from config import CONFIG, logger


# ---------------------------------------------------------------------------
# Palette & style constants
# ---------------------------------------------------------------------------

# Okabe-Ito colorblind-safe palette
CB = [
    "#E69F00",   # 0  orange
    "#56B4E9",   # 1  sky blue
    "#009E73",   # 2  bluish green
    "#F0E442",   # 3  yellow
    "#0072B2",   # 4  blue
    "#D55E00",   # 5  vermillion
    "#CC79A7",   # 6  reddish purple
]

_SOURCE = "Source: GlobalTech HR Integration Pipeline"

# Abbreviated check-type prefixes for the DQ chart
_CHECK_ABBR = {
    "not_null":              "NN",
    "unique":                "UQ",
    "values_in_set":         "VS",
    "regex":                 "RX",
    "numeric_range":         "NR",
    "date_range":            "DR",
    "referential_integrity": "RI",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_note(ax: plt.Axes, note: str = _SOURCE) -> None:
    """Append a small data-source annotation below the axis."""
    ax.annotate(
        note,
        xy=(0, -0.095), xycoords="axes fraction",
        fontsize=7, color="#aaaaaa", ha="left", va="top",
    )


def _shorten_check(name: str) -> str:
    """'not_null.salary_usd_annual' → 'NN · salary usd annual'"""
    prefix, _, field = name.partition(".")
    abbr  = _CHECK_ABBR.get(prefix, prefix[:2].upper())
    label = field.replace("_", " ")
    return f"{abbr} · {label}"


def _apply_style() -> None:
    """Global rcParams for a clean, publication-ready look."""
    plt.rcParams.update({
        "font.family":       "DejaVu Sans",
        "axes.spines.top":   False,
        "axes.spines.right": False,
        "axes.grid":         True,
        "grid.alpha":        0.3,
        "grid.linewidth":    0.55,
        "grid.color":        "#c8c8c8",
        "axes.titlepad":     8,
        "legend.frameon":    False,
        "legend.fontsize":   8.5,
    })


# ---------------------------------------------------------------------------
# Chart 1 — Headcount by Department
# ---------------------------------------------------------------------------

def _chart1_headcount_dept(ax: plt.Axes, employees: pd.DataFrame) -> None:
    counts = (
        employees.groupby("department", dropna=False)["employee_id"]
        .count()
        .sort_values(ascending=True)
    )
    dept_labels = counts.index.fillna("(no department)")
    y = np.arange(len(counts))

    bars = ax.barh(y, counts.values, color=CB[0], edgecolor="white", linewidth=0.4, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels(dept_labels, fontsize=8.5)
    ax.tick_params(axis="x", labelsize=8.5)

    gap = counts.max() * 0.012
    for bar, val in zip(bars, counts.values):
        ax.text(
            val + gap, bar.get_y() + bar.get_height() / 2,
            f"{val:,}", va="center", ha="left", fontsize=8,
        )

    ax.set_xlim(0, counts.max() * 1.16)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.set_title("Headcount by Department", fontsize=12.5, fontweight="bold")
    ax.set_xlabel("Number of Employees", fontsize=9.5)
    _source_note(ax)


# ---------------------------------------------------------------------------
# Chart 2 — Headcount by Country (top 20)
# ---------------------------------------------------------------------------

def _chart2_headcount_country(ax: plt.Axes, employees: pd.DataFrame) -> None:
    counts = (
        employees["country"].dropna()
        .value_counts()
        .head(20)
        .sort_values(ascending=True)
    )

    bars = ax.barh(counts.index, counts.values, color=CB[1], edgecolor="white", linewidth=0.4, zorder=3)
    ax.tick_params(axis="y", labelsize=8.5)
    ax.tick_params(axis="x", labelsize=8.5)

    gap = counts.max() * 0.012
    for bar, val in zip(bars, counts.values):
        ax.text(
            val + gap, bar.get_y() + bar.get_height() / 2,
            f"{val:,}", va="center", ha="left", fontsize=8,
        )

    ax.set_xlim(0, counts.max() * 1.16)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.set_title("Top 20 Countries by Headcount", fontsize=12.5, fontweight="bold")
    ax.set_xlabel("Number of Employees", fontsize=9.5)
    _source_note(ax)


# ---------------------------------------------------------------------------
# Chart 3 — Salary Distribution by Employment Type (violin, log scale)
# ---------------------------------------------------------------------------

def _chart3_salary_dist(
    ax:        plt.Axes,
    employees: pd.DataFrame,
    payroll:   pd.DataFrame,
) -> None:
    merged = (
        payroll[["employee_id", "salary_usd_annual"]]
        .merge(employees[["employee_id", "employment_type"]], on="employee_id", how="inner")
        .dropna(subset=["salary_usd_annual", "employment_type"])
    )
    order   = ["Full-Time", "Part-Time", "Contractor"]
    palette = dict(zip(order, [CB[0], CB[2], CB[4]]))

    sns.violinplot(
        data      = merged,
        x         = "employment_type",
        y         = "salary_usd_annual",
        hue       = "employment_type",   # seaborn 0.13+ requires hue for palette
        order     = order,
        palette   = palette,
        ax        = ax,
        inner     = "box",
        linewidth = 0.8,
        cut       = 0,       # don't extend violin beyond data range
        legend    = False,
    )

    ax.set_yscale("log")
    ax.set_title("Annual Salary Distribution by Employment Type",
                 fontsize=12.5, fontweight="bold")
    ax.set_xlabel("Employment Type", fontsize=9.5)
    ax.set_ylabel("Annual Salary USD  (log scale)", fontsize=9.5)
    ax.tick_params(axis="both", labelsize=8.5)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(
            lambda y, _: f"${y/1_000_000:.1f}M" if y >= 1_000_000 else f"${y/1_000:.0f}K"
        )
    )
    ax.annotate(
        f"n = {len(merged):,} payroll records matched to HRIS",
        xy=(0.98, 0.02), xycoords="axes fraction",
        fontsize=7, color="#888", ha="right",
    )
    _source_note(ax)


# ---------------------------------------------------------------------------
# Chart 4 — Tenure Distribution (histogram)
# ---------------------------------------------------------------------------

def _chart4_tenure_dist(ax: plt.Axes, employees: pd.DataFrame) -> None:
    today  = pd.Timestamp(date.today())
    hire   = pd.to_datetime(employees["hire_date"], errors="coerce")
    tenure = ((today - hire).dt.days / 365.25).dropna()
    tenure = tenure[tenure >= 0]

    ax.hist(tenure, bins=35, color=CB[4], edgecolor="white", linewidth=0.4, zorder=3)

    med  = float(tenure.median())
    mean = float(tenure.mean())
    ax.axvline(med,  color=CB[5], linewidth=2.0, linestyle="--",
               label=f"Median  {med:.1f} yr", zorder=4)
    ax.axvline(mean, color=CB[6], linewidth=2.0, linestyle=":",
               label=f"Mean    {mean:.1f} yr",  zorder=4)
    ax.legend(fontsize=9)

    # Stats inset
    ax.text(
        0.965, 0.965,
        f"n = {len(tenure):,}\nMin  {tenure.min():.1f} yr\nMax  {tenure.max():.1f} yr\nStd  {tenure.std():.1f} yr",
        transform=ax.transAxes, fontsize=7.5, va="top", ha="right",
        bbox=dict(boxstyle="round,pad=0.4", fc="white", alpha=0.88, ec="#c8c8c8"),
    )

    ax.set_title("Tenure Distribution", fontsize=12.5, fontweight="bold")
    ax.set_xlabel("Tenure (Years)", fontsize=9.5)
    ax.set_ylabel("Number of Employees", fontsize=9.5)
    ax.tick_params(axis="both", labelsize=8.5)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{int(y):,}"))
    _source_note(ax)


# ---------------------------------------------------------------------------
# Chart 5 — Benefits Enrollment Rate by Department
# ---------------------------------------------------------------------------

def _chart5_benefits_enrollment(
    ax:        plt.Axes,
    employees: pd.DataFrame,
    benefits:  pd.DataFrame,
) -> None:
    # Benefits XML covers GlobalTech employees only — denominator must match
    gt = employees[employees["source_system"] == "GlobalTech_HRIS"]
    emp_per_dept = gt.groupby("department", dropna=False)["employee_id"].count()
    enrolled = (
        benefits
        .merge(gt[["employee_id", "department"]], on="employee_id", how="left")
        .groupby("department", dropna=False)["employee_id"]
        .nunique()
    )
    rate = (enrolled / emp_per_dept * 100).fillna(0).sort_values(ascending=True)

    # Dynamic x-range — tight around actual data so small differences are visible
    lo = max(0, rate.min() - 5)
    hi = min(100, rate.max() + 10)

    y_pos       = np.arange(len(rate))
    dept_labels = rate.index.fillna("(no department)")
    colors      = [CB[2] if v >= 50 else CB[5] for v in rate.values]

    bars = ax.barh(y_pos, rate.values, color=colors, edgecolor="white", linewidth=0.4, zorder=3)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(dept_labels, fontsize=8.5)
    ax.tick_params(axis="x", labelsize=8.5)

    for bar, val in zip(bars, rate.values):
        ax.text(
            val + 0.4, bar.get_y() + bar.get_height() / 2,
            f"{val:.1f}%", va="center", ha="left", fontsize=7.5,
        )

    ax.axvline(50, color="#aaaaaa", linewidth=1.0, linestyle="--", zorder=2)
    ax.set_xlim(lo, hi)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.set_title("Benefits Enrollment Rate by Department\n(GlobalTech employees)",
                 fontsize=12.5, fontweight="bold")
    ax.set_xlabel("Enrollment Rate (%)", fontsize=9.5)

    patch_hi = mpatches.Patch(color=CB[2], label="≥ 50%")
    patch_lo = mpatches.Patch(color=CB[5], label="< 50%")
    ax.legend(handles=[patch_hi, patch_lo], fontsize=9, loc="lower right")
    _source_note(ax)


# ---------------------------------------------------------------------------
# Chart 6 — Data Quality Summary (horizontal grouped bar, log scale)
# ---------------------------------------------------------------------------

def _chart6_data_quality(ax: plt.Axes, report: pd.DataFrame) -> None:
    labels        = [_shorten_check(c) for c in report["check"]]
    passed_vals   = report["passed"].values.astype(float)
    failed_vals   = report["failed"].values.astype(float)

    # On a log axis, zero doesn't render — floor failed at 0.5 so zero-failure
    # bars appear as a barely-visible sliver (clearly "no failures")
    failed_plot   = np.where(failed_vals == 0, 0.5, failed_vals)

    n      = len(labels)
    y      = np.arange(n)
    height = 0.38

    ax.barh(y + height / 2, passed_vals,  height, color=CB[2], label="Passed",
            edgecolor="white", linewidth=0.4, zorder=3)
    ax.barh(y - height / 2, failed_plot,  height, color=CB[5], label="Failed",
            edgecolor="white", linewidth=0.4, zorder=3)

    # Label actual failure counts on the red bars
    for i, (f, fp) in enumerate(zip(failed_vals, failed_plot)):
        ax.text(
            max(fp * 1.6, 2), i - height / 2,
            f"{int(f):,}" if f > 0 else "0",
            va="center", fontsize=7.5, color=CB[5], fontweight="bold",
        )

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xscale("log")
    ax.set_title("Data Quality — Passed vs Failed per Check",
                 fontsize=12.5, fontweight="bold")
    ax.set_xlabel("Record Count  (log scale)", fontsize=9.5)
    ax.tick_params(axis="x", labelsize=8.5)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.legend(fontsize=9, loc="lower right")

    n_pass = int((report["status"] == "PASS").sum())
    n_fail = int((report["status"] == "FAIL").sum())
    gate_color = CB[2] if n_fail <= 2 else CB[5]
    ax.annotate(
        f"{n_pass}/{len(report)} checks PASS",
        xy=(0.98, 0.99), xycoords="axes fraction",
        fontsize=9.5, color=gate_color, ha="right", va="top", fontweight="bold",
    )
    _source_note(ax)


# ---------------------------------------------------------------------------
# Main report generator
# ---------------------------------------------------------------------------

def generate_report(
    dedup_result:      dict,
    validation_report: pd.DataFrame,
    output_path:       Path | None = None,
) -> Path:

    employees = dedup_result["employees"]
    payroll   = dedup_result["payroll"]
    benefits  = dedup_result["benefits"]

    if output_path is None:
        output_path = Path(CONFIG["output_dir"]) / "eda_report.png"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _apply_style()

    # ── Figure scaffold ───────────────────────────────────────────────────
    fig, axes = plt.subplots(3, 2, figsize=(22, 27))
    fig.patch.set_facecolor("#f9f9f9")
    for ax in axes.flat:
        ax.set_facecolor("#ffffff")

    # ── Overall title + timestamp ─────────────────────────────────────────
    n_gt  = int((employees["source_system"] == "GlobalTech_HRIS").sum())
    n_acq = int((employees["source_system"] == "AcquiredCo_HRIS").sum())
    fig.suptitle(
        "GlobalTech Corp — Post-Merger Workforce Analytics\n"
        f"{len(employees):,} Employees  ({n_gt:,} GlobalTech + {n_acq:,} AcquiredCo)"
        f"  ·  Generated {date.today()}",
        fontsize=15, fontweight="bold", y=0.987, color="#1a1a2e",
        linespacing=1.5,
    )

    # ── Render each chart ─────────────────────────────────────────────────
    _chart1_headcount_dept(         axes[0, 0], employees)
    _chart2_headcount_country(      axes[0, 1], employees)
    _chart3_salary_dist(            axes[1, 0], employees, payroll)
    _chart4_tenure_dist(            axes[1, 1], employees)
    _chart5_benefits_enrollment(    axes[2, 0], employees, benefits)
    _chart6_data_quality(           axes[2, 1], validation_report)

    # ── Save ──────────────────────────────────────────────────────────────
    plt.tight_layout(rect=[0, 0, 1, 0.972], h_pad=4.5, w_pad=3.0)
    fig.savefig(
        output_path,
        dpi         = 300,
        bbox_inches = "tight",
        facecolor   = fig.get_facecolor(),
    )
    plt.close(fig)

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info("[visualize] EDA report saved → %s  (%.1f MB, 300 DPI)", output_path, size_mb)
    return output_path


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

def visualize_all(
    dedup_result:      dict,
    validation_report: pd.DataFrame,
    output_path:       Path | None = None,
) -> Path:
    """Alias for generate_report — matches the deliverable naming convention."""
    return generate_report(dedup_result, validation_report, output_path)


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from ingest import ingest_all
    from clean import clean_all
    from deduplicate import dedup_all
    from validate import validate_all

    raw           = ingest_all()
    cleaned       = clean_all(raw)
    deduped       = dedup_all(cleaned)
    val_report, _ = validate_all(deduped)

    out = generate_report(deduped, val_report)
    print(f"\nEDA report saved: {out}")
    print(f"File size       : {out.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"Resolution      : 300 DPI  (22 × 27 in = {22*300} × {27*300} px)")
