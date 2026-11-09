from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from processor import (
    append_run, evaluate, extract_text, history_frame,
    load_po_table, parse_invoice, read_history,
)

BASE_DIR = Path(__file__).parent
PO_FILE = BASE_DIR / "po_data.csv"
HISTORY_FILE = BASE_DIR / "data" / "run_history.json"
HISTORY_FILE.parent.mkdir(exist_ok=True)

DECISION_COLOR = {
    "Approved": "green",
    "Approved with Warning": "orange",
    "Needs Review": "orange",
    "Rejected": "red",
}
STATUS_ICON = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}

st.set_page_config(page_title="PS-1 Invoice to Decision", layout="wide", page_icon="🧾")
st.title("🧾 PS-1 · Invoice Processing — PDF to Decision")
st.caption("Extract → Parse → Validate → Decide. Business rules are in processor.py.")

po_df = load_po_table(PO_FILE)

with st.sidebar:
    st.header("📦 PO database")
    st.dataframe(po_df[["po_number", "vendor", "amount", "status"]], hide_index=True)
    st.header("🧪 Test samples (in samples/)")
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

tab_run, tab_dash = st.tabs(["▶️ Live run view", "📊 Dashboard & history"])


def process_invoice(uploaded, po_df):
    data = uploaded.getvalue()
    with st.status(f"Processing {uploaded.name}", expanded=True) as status:
        st.write("**Stage 1 · Extract** — PDF → text layer")
        try:
            text = extract_text(data)
        except Exception as exc:  # corrupt or unreadable file
            st.error(f"Could not read PDF: {exc}")
            status.update(label=f"{uploaded.name}: unreadable PDF", state="error")
            return

        st.code(text[:500] or "(no text layer found)", language="text")

        st.write("**Stage 2 · Parse** — locate invoice fields")
        parsed = parse_invoice(text)
        st.json(parsed)

        st.write("**Stage 3 · Validate** — duplicate, PO, vendor, tolerance, running total")
        history = history_frame(read_history(HISTORY_FILE))
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

    append_run(HISTORY_FILE, {
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


with tab_run:
    files = st.file_uploader("Upload invoice PDF(s)", type=["pdf"], accept_multiple_files=True)
    if st.button("▶ Run automation", type="primary", disabled=not files):
        # Sorted by name so batches run in a predictable order
        for f in sorted(files, key=lambda x: x.name):
            process_invoice(f, po_df)

with tab_dash:
    records = read_history(HISTORY_FILE)
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
            HISTORY_FILE.write_text("[]")
            st.rerun()
