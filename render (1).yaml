"""Core logic for PS-1 invoice processing.
No Streamlit code here, so the rules are easy to read and test.
"""
import json
import re
from pathlib import Path

import pandas as pd

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None

# ---- Business rules (change here, not in the UI) ----
EXACT_MATCH_PCT = 0.1   # within this % of PO amount = exact match
TOLERANCE_PCT = 5.0     # within this % = approved with warning
MIN_TEXT_CHARS = 80     # less text than this = scanned / no text layer

HISTORY_COLUMNS = [
    "timestamp", "filename", "invoice_number", "po_number", "vendor",
    "total", "decision", "confidence", "reasons",
]


# ---------- Stage 1: extract ----------
def extract_text(file_bytes: bytes) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install PyMuPDF")
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


# ---------- Stage 2: parse ----------
def po_key(po):
    """Normalise a PO number so 'PO-10245', 'PO 10245' and 'po10245' match."""
    if not isinstance(po, str) or not po.strip():
        return None
    return re.sub(r"[\s\-]", "", po).upper()


def _find(pattern, text):
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _to_money(value):
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def parse_invoice(text: str) -> dict:
    po = _find(r"\b(PO[-\s]?\d{3,}[\w\-/]*)", text)
    if not po:
        po = _find(r"Purchase\s+Order\s*(?:No\.?|#)?\s*[:#]?\s*([A-Z0-9][\w\-/]*)", text)
    return {
        "invoice_number": _find(r"Invoice\s*(?:No\.?|Number|#)\s*[:#]?\s*([A-Z0-9][\w\-/]*)", text),
        "po_number": po,
        "vendor": _find(r"(?:Vendor|Bill\s+From)\s*:\s*([^\r\n]+)", text),
        "total": _to_money(_find(r"\b(?:Grand\s+Total|Amount\s+Due|Total)\s*:?\s*\$?\s*([\d,]+\.\d{2})", text)),
    }


# ---------- Reference data and history ----------
def load_po_table(path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["amount"] = df["amount"].astype(float)
    df["po_key"] = df["po_number"].map(po_key)
    return df


def read_history(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text() or "[]")
    except json.JSONDecodeError:
        return []


def history_frame(records) -> pd.DataFrame:
    return pd.DataFrame(records, columns=HISTORY_COLUMNS)


def append_run(path, record: dict):
    records = read_history(path)
    records.append(record)
    Path(path).write_text(json.dumps(records, indent=2))


# ---------- Stage 3 and 4: validate and decide ----------
def _norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _same_vendor(a, b):
    na, nb = _norm_name(a), _norm_name(b)
    return bool(na) and bool(nb) and (na in nb or nb in na)


def evaluate(parsed: dict, text_len: int, po_df: pd.DataFrame, history: pd.DataFrame) -> dict:
    """Run every check in order. Stop at the first failure that decides the outcome.
    Returns the decision, a reason, and a list of checks for the live run view.
    """
    checks = []
    po = None

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    def done(decision, confidence, reason):
        return {"decision": decision, "confidence": confidence,
                "reason": reason, "po": po, "checks": checks}

    # 1. Readable text layer
    if text_len < MIN_TEXT_CHARS:
        add("Text layer", "FAIL", f"Only {text_len} characters found, so this looks like a scan")
        return done("Needs Review", "Low", "Scanned or image-only PDF. Needs OCR or manual keying.")
    add("Text layer", "PASS", f"{text_len} characters extracted")

    # 2. Required fields
    inv = parsed["invoice_number"]
    po_num = parsed["po_number"]
    vendor = parsed["vendor"]
    total = parsed["total"]
    missing = [label for label, value in [
        ("invoice number", inv), ("vendor", vendor),
        ("total", total), ("PO reference", po_num)] if not value]
    if missing:
        add("Required fields", "FAIL", "Missing: " + ", ".join(missing))
        return done("Needs Review", "Medium",
                    f"Missing {', '.join(missing)}. Ask the vendor for a corrected invoice.")
    add("Required fields", "PASS", f"Invoice {inv} · PO {po_num} · ${total:,.2f}")

    # 3. Duplicate check (blocks double payment)
    seen = set(history["invoice_number"].dropna().astype(str).str.upper())
    if inv.upper() in seen:
        add("Duplicate check", "FAIL", f"{inv} was already processed")
        return done("Rejected", "High", f"Duplicate of invoice {inv}. Blocked to prevent double payment.")
    add("Duplicate check", "PASS", "Invoice number not seen before")

    # 4. PO lookup
    matches = po_df[po_df["po_key"] == po_key(po_num)]
    if matches.empty:
        add("PO lookup", "FAIL", f"{po_num} not found in procurement system")
        return done("Needs Review", "Medium", f"PO {po_num} not found. Route to procurement to confirm.")
    po = matches.iloc[0].to_dict()
    add("PO lookup", "PASS", f"Found {po['po_number']} issued to {po['vendor']}")

    # 5. Vendor match
    if not _same_vendor(vendor, po["vendor"]):
        add("Vendor match", "FAIL", f"Invoice from '{vendor}', PO issued to '{po['vendor']}'")
        return done("Needs Review", "High",
                    "Vendor on the invoice does not match the PO. Possible wrong PO or fraud.")
    add("Vendor match", "PASS", f"'{vendor}' matches PO vendor")

    # 6. Amount checks
    po_amt = po["amount"]
    prior = history[
        (history["po_number"].map(po_key) == po["po_key"])
        & (history["decision"].astype(str).str.startswith("Approved"))
    ]
    prior_total = float(prior["total"].fillna(0).astype(float).sum()) if not prior.empty else 0.0

    if prior_total > 0:
        # Partial billing: check the running total against the PO
        cumulative = prior_total + total
        limit = po_amt * (1 + TOLERANCE_PCT / 100)
        if cumulative > limit + 0.01:
            add("Cumulative billing", "FAIL",
                f"Approved so far ${prior_total:,.2f} + this ${total:,.2f} = ${cumulative:,.2f} vs PO ${po_amt:,.2f}")
            return done("Needs Review", "High",
                        "Possible split or over-billing: approved invoices on this PO would exceed the PO amount.")
        add("Cumulative billing", "PASS", f"Running total ${cumulative:,.2f} within PO ${po_amt:,.2f}")
        return done("Approved", "High",
                    f"Partial invoice on {po_num}. Running total ${cumulative:,.2f} of ${po_amt:,.2f}.")

    # Full billing: compare the invoice to the PO amount
    diff_pct = abs(total - po_amt) / po_amt * 100
    if diff_pct > TOLERANCE_PCT:
        add("Amount tolerance", "FAIL",
            f"Invoice ${total:,.2f} vs PO ${po_amt:,.2f} ({diff_pct:.2f}% off, limit {TOLERANCE_PCT}%)")
        return done("Needs Review", "High",
                    f"Amount is {diff_pct:.2f}% off the PO, above the {TOLERANCE_PCT}% limit.")

    if diff_pct <= EXACT_MATCH_PCT:
        add("Amount tolerance", "PASS", f"Exact match: ${total:,.2f} = PO ${po_amt:,.2f}")
        return done("Approved", "High", f"Exact match to {po_num} (${po_amt:,.2f}).")

    add("Amount tolerance", "WARN",
        f"Invoice ${total:,.2f} vs PO ${po_amt:,.2f} ({diff_pct:.2f}% off, within {TOLERANCE_PCT}%)")
    return done("Approved with Warning", "Medium",
                f"Within {TOLERANCE_PCT}% tolerance ({diff_pct:.2f}% off the PO). Auto-approved with warning.")
