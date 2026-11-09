"""
PS-1 · Invoice Processing — PDF to Decision
Single-file app. Run with:  streamlit run invoice_app.py

Pipeline: Extract -> Parse -> Validate -> Decide, with a live run view and dashboard.
Business rules are in the RULES section below.

Requirements:  pip install streamlit pandas PyMuPDF
"""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    import fitz  # PyMuPDF
except ImportError:  # app still imports so the error shows in the UI
    fitz = None

# =====================================================================
# CONFIG
# =====================================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SAMPLE_DIR = BASE_DIR / "samples"
HISTORY_FILE = DATA_DIR / "run_history.json"

# ---- Business rules (change here) ----
EXACT_MATCH_PCT = 0.1    # within this % of PO amount = exact match
TOLERANCE_PCT = 5.0      # within this % = approved with warning
MIN_TEXT_CHARS = 80      # less text than this = scanned / no text layer

# ---- Procurement system (PO database) ----
PO_DATA = [
    {"po_number": "PO-10245", "vendor": "Acme Supplies",     "amount": 5000.0, "currency": "USD", "status": "Approved"},
    {"po_number": "PO-10246", "vendor": "Northwind Tech",    "amount": 12500.0, "currency": "USD", "status": "Approved"},
    {"po_number": "PO-10247", "vendor": "Contoso Logistics", "amount": 3200.0, "currency": "USD", "status": "Approved"},
    {"po_number": "PO-10248", "vendor": "Acme Supplies",     "amount": 7500.0, "currency": "USD", "status": "Approved"},
]

HISTORY_COLUMNS = [
    "timestamp", "filename", "invoice_number", "po_number", "vendor",
    "total", "decision", "confidence", "reasons",
]

# =====================================================================
# STAGE 1 · EXTRACT
# =====================================================================
def extract_text(file_bytes: bytes) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install PyMuPDF")
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

# =====================================================================
# STAGE 2 · PARSE
# =====================================================================
def po_key(po):
    """Normalise PO numbers so 'PO-10245', 'PO 10245' and 'po10245' match."""
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

# =====================================================================
# HISTORY (persisted to data/run_history.json)
# =====================================================================
def po_table() -> pd.DataFrame:
    df = pd.DataFrame(PO_DATA)
    df["po_key"] = df["po_number"].map(po_key)
    return df


def read_history() -> list:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text() or "[]")
    except json.JSONDecodeError:
        return []


def history_frame(records) -> pd.DataFrame:
    return pd.DataFrame(records, columns=HISTORY_COLUMNS)


def append_run(record: dict):
    DATA_DIR.mkdir(exist_ok=True)
    records = read_history()
    records.append(record)
    HISTORY_FILE.write_text(json.dumps(records, indent=2))


def clear_history():
    DATA_DIR.mkdir(exist_ok=True)
    HISTORY_FILE.write_text("[]")

# =====================================================================
# STAGE 3 + 4 · VALIDATE AND DECIDE
# =====================================================================
def _norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _same_vendor(a, b):
    na, nb = _norm_name(a), _norm_name(b)
    return bool(na) and bool(nb) and (na in nb or nb in na)


def evaluate(parsed: dict, text_len: int, po_df: pd.DataFrame, history: pd.DataFrame) -> dict:
    """Run checks in order, stopping at the first decisive failure.
    Returns decision, confidence, reason, and the list of checks for the live view."""
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
    inv, po_num = parsed["invoice_number"], parsed["po_number"]
    vendor, total = parsed["vendor"], parsed["total"]
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

    if prior_total > 0:  # partial billing: check running total against the PO
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

    diff_pct = abs(total - po_amt) / po_amt * 100  # full billing: invoice vs PO amount
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

# =====================================================================
# SAMPLE GENERATOR (creates test PDFs so you can demo every case)
# =====================================================================
SAMPLE_SPECS = [
    ("01_happy_INV-9001.pdf", "Acme Supplies", "INV-9001", 5000, "PO-10245"),
    ("02_edge_tolerance_INV-9002.pdf", "Contoso Logistics", "INV-9002", 3300, "PO-10247"),
    ("03_edge_duplicate_INV-9001.pdf", "Acme Supplies", "INV-9001", 5000, "PO-10245"),
    ("04_edge_no_po_INV-9003.pdf", "Acme Supplies", "INV-9003", 1200, None),
    ("05_edge_split_INV-9004.pdf", "Acme Supplies", "INV-9004", 7500, "PO-10248"),
    ("06_edge_split_INV-9005.pdf", "Acme Supplies", "INV-9005", 2000, "PO-10248"),
    ("07_edge_vendor_mismatch_INV-9006.pdf", "Globex Parts", "INV-9006", 12500, "PO-10246"),
]


def make_invoice_pdf(path, vendor, invoice_no, total, po_no=None):
    doc = fitz.open()
    page = doc.new_page()
    lines = [("TAX INVOICE", 16), (f"Vendor: {vendor}", 12),
             ("Bill To: Mid-Size Co. Pvt Ltd, Accounts Payable", 12),
             (f"Invoice No: {invoice_no}", 12), ("Invoice Date: 05-Nov-2026", 12)]
    if po_no:
        lines.append((f"PO Reference: {po_no}", 12))
    lines += [("Description: Supplies as per purchase order", 12),
              (f"Amount: ${total:,.2f}", 12), ("Tax: $0.00", 12),
              (f"Total: ${total:,.2f}", 12)]
    y = 70
    for text, size in lines:
        page.insert_text((50, y), text, fontsize=size)
        y += 24 if size > 12 else 20
    doc.save(path)
    doc.close()


def generate_samples() -> int:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed")
    SAMPLE_DIR.mkdir(exist_ok=True)
    for name, vendor, inv, total, po_no in SAMPLE_SPECS:
        make_invoice_pdf(SAMPLE_DIR / name, vendor, inv, total, po_no)
    blank = fitz.open()  # scanned-style file: a page with no text layer
    blank.new_page()
    blank.save(SAMPLE_DIR / "08_edge_scanned.pdf")
    blank.close()
    return len(SAMPLE_SPECS) + 1


# ==================================================================
# UI  (Streamlit)
# ==================================================================
if __name__ == "__main__" or "streamlit" in __import__("sys").modules:
    import streamlit as st

    DECISION_COLOR = {"Approved": "green", "Approved with Warning": "orange",
                      "Needs Review": "orange", "Rejected": "red"}
    STATUS_ICON = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}

    st.set_page_config(page_title="PS-1 Invoice to Decision", layout="wide", page_icon="🧾")
    st.title("🧾 PS-1 · Invoice Processing — PDF to Decision")
    st.caption("Extract → Parse → Validate → Decide. Business rules are in the CONFIG section of this file.")

    po_df = po_table()

    with st.sidebar:
        st.header("📦 PO database")
        st.dataframe(po_df[["po_number", "vendor", "amount", "status"]], hide_index=True)

        st.header("🧪 Test samples")
        if st.button("Generate sample invoices"):
            try:
                n = generate_samples()
                st.success(f"Created {n} PDFs in samples/")
            except Exception as exc:
                st.error(str(exc))
        st.markdown("""
Upload in this order, one batch or one at a time:

1. `01_happy` → **Approved**
2. `02_edge_tolerance` (3.1% over) → **Approved with Warning**
3. `03_edge_duplicate` → **Rejected**
4. `04_edge_no_po` → **Needs Review**
5. `05_edge_split` → **Approved** (first partial invoice)
6. `06_edge_split` → **Needs Review** (over-billing)
7. `07_edge_vendor_mismatch` → **Needs Review**
8. `08_edge_scanned` → **Needs Review**
""")

    def process_invoice(uploaded):
        data = uploaded.getvalue()
        with st.status(f"Processing {uploaded.name}", expanded=True) as status:
            st.write("**Stage 1 · Extract** — PDF → text layer")
            try:
                text = extract_text(data)
            except Exception as exc:
                st.error(f"Could not read PDF: {exc}")
                status.update(label=f"{uploaded.name}: unreadable PDF", state="error")
                return

            st.code(text[:500] or "(no text layer found)", language="text")

            st.write("**Stage 2 · Parse** — locate invoice fields")
            parsed = parse_invoice(text)
            st.json(parsed)

            st.write("**Stage 3 · Validate** — duplicate, PO, vendor, tolerance, running total")
            history = history_frame(read_history())
            result = evaluate(parsed, len(text.strip()), po_df, history)
            st.dataframe(
                pd.DataFrame(result["checks"]).assign(
                    status=lambda d: d["status"].map(lambda s: f"{STATUS_ICON[s]} {s}")),
                hide_index=True,
            )

            st.write("**Stage 4 · Decide**")
            status.update(label=f"{uploaded.name} → {result['decision']}",
                          state="complete", expanded=False)

        color = DECISION_COLOR.get(result["decision"], "gray")
        st.markdown(f"**{uploaded.name}** → :{color}[**{result['decision']}**] · Confidence: {result['confidence']}")
        st.caption(result["reason"])

        append_run({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "filename": uploaded.name,
            "invoice_number": parsed["invoice_number"],
            "po_number": parsed["po_number"],
            "vendor": parsed["vendor"],
            "total": parsed["total"],
            "decision": result["decision"],
            "confidence": result["confidence"],
            "reasons": result["reason"],
        })

    tab_run, tab_dash = st.tabs(["▶️ Live run view", "📊 Dashboard & history"])

    with tab_run:
        files = st.file_uploader("Upload invoice PDF(s)", type=["pdf"], accept_multiple_files=True)
        if st.button("▶ Run automation", type="primary", disabled=not files):
            for f in sorted(files, key=lambda x: x.name):  # predictable order
                process_invoice(f)

    with tab_dash:
        records = read_history()
        if not records:
            st.info("No runs yet. Process invoices in the Live run view.")
        else:
            df = history_frame(records)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total runs", len(df))
            c2.metric("Approved", int(df["decision"].astype(str).str.startswith("Approved").sum()))
            c3.metric("Needs review", int((df["decision"] == "Needs Review").sum()))
            c4.metric("Rejected", int((df["decision"] == "Rejected").sum()))
            st.bar_chart(df["decision"].value_counts())
            st.dataframe(df, hide_index=True)
            st.download_button("⬇️ Download CSV", df.to_csv(index=False), "invoice_runs.csv", "text/csv")
            if st.button("Clear history"):
                clear_history()
                st.rerun()
