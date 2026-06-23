from pathlib import Path
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

CONFIG = {
    # Directories
    "input_dir":  Path("Data/raw"),
    "output_dir": Path("Data/processed"),

    # Source file paths
    "globaltech_csv":  Path("Data/raw/globaltech_hris.csv"),
    "acquiredco_json": Path("Data/raw/acquiredco_api.json"),
    "payroll_xlsx":    Path("Data/raw/payroll_data.xlsx"),
    "benefits_xml":    Path("Data/raw/benefits_enrollment.xml"),

    # Source-specific ingestion settings
    "globaltech_encoding":  "utf-8",
    "acquiredco_page_size": 100,        # records per simulated BambooHR API page
    "payroll_sheet":        "Payroll",

    # Standard employee schema — column order is the contract for downstream modules
    "standard_columns": [
        "employee_id",
        "source_system",
        "first_name",
        "last_name",
        "email",
        "department",
        "job_title",
        "hire_date",
        "country",
        "employment_type",
        "employment_status",
        "manager_id",
    ],

    # BambooHR abbreviated employment-type codes → canonical long form
    "acqco_emp_type_map": {
        "FT":         "Full-Time",
        "PT":         "Part-Time",
        "CONTRACTOR": "Contractor",
    },

    # Validation allow-lists
    "valid_employment_types":    ["Full-Time", "Part-Time", "Contractor"],
    "valid_employment_statuses": ["Active", "Inactive", "On Leave"],
    "valid_currencies":          ["USD", "EUR", "GBP"],

    # Email format check
    "email_regex": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",

    # Currency symbol → ISO code (used to parse formatted salary strings)
    "currency_symbol_map": {"$": "USD", "£": "GBP", "€": "EUR"},

    # Source system → two-letter prefix for the GT-/AC- scheme
    "employee_id_prefix": {
        "GlobalTech_HRIS":  "GT",
        "AcquiredCo_HRIS":  "AC",
    },

    "fx_rates_to_usd": {
        "USD": 1.00,
        "EUR": 1.08, 
    },
    "pay_frequency_multiplier": {
        "Annual":    1,
        "Monthly":   12,
        "Bi-Weekly": 26,
    },
    "department_taxonomy": [
        "Business Development",
        "Communications",
        "Customer Success",
        "Data Science",
        "DevOps",
        "Engineering",
        "Finance",
        "Human Resources",
        "Information Technology",
        "Legal",
        "Manufacturing",
        "Marketing",
        "Operations",
        "Product",
        "Quality Assurance",
        "Sales",
        "Strategy",
        "Supply Chain",
    ],
    "department_map": {
        # ── Exact matches (both sources) ──
        "Business Development":  "Business Development",
        "Communications":        "Communications",
        "Customer Success":      "Customer Success",
        "Data Science":          "Data Science",
        "DevOps":                "DevOps",
        "Engineering":           "Engineering",
        "Finance":               "Finance",
        "Human Resources":       "Human Resources",
        "Information Technology": "Information Technology",
        "Legal":                 "Legal",
        "Manufacturing":         "Manufacturing",
        "Marketing":             "Marketing",
        "Operations":            "Operations",
        "Product":               "Product",
        "Quality Assurance":     "Quality Assurance",
        "Sales":                 "Sales",
        "Strategy":              "Strategy",
        "Supply Chain":          "Supply Chain",
        # ── GlobalTech legacy codes (if ever encountered) ──
        "ENG-01": "Engineering",
        "MKT-03": "Marketing",
        "HR-02":  "Human Resources",
        "FIN-01": "Finance",
        "OPS-04": "Operations",
        "IT-05":  "Information Technology",
    },

    # -----------------------------------------------------------------------
    # Date plausibility bounds
    # -----------------------------------------------------------------------
    "hire_date_min": "1970-01-01",
}

for d in [CONFIG["input_dir"], CONFIG["output_dir"]]:
    d.mkdir(parents=True, exist_ok=True)
