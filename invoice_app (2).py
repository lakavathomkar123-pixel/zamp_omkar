"""Creates realistic test invoice PDFs in samples/.
Run once:  python generate_samples.py
"""
from pathlib import Path

import fitz  # PyMuPDF

OUT = Path(__file__).parent / "samples"
OUT.mkdir(exist_ok=True)


def make_invoice(filename, vendor, invoice_no, total, po_no=None):
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        ("TAX INVOICE", 16),
        (f"Vendor: {vendor}", 12),
        ("Bill To: Mid-Size Co. Pvt Ltd, Accounts Payable", 12),
        (f"Invoice No: {invoice_no}", 12),
        ("Invoice Date: 05-Nov-2026", 12),
    ]
    if po_no:
        lines.append((f"PO Reference: {po_no}", 12))
    lines += [
        ("Description: Supplies as per purchase order", 12),
        (f"Amount: ${total:,.2f}", 12),
        ("Tax: $0.00", 12),
        (f"Total: ${total:,.2f}", 12),
    ]
    y = 70
    for text, size in lines:
        page.insert_text((50, y), text, fontsize=size)
        y += 24 if size > 12 else 20
    doc.save(OUT / filename)
    doc.close()


make_invoice("01_happy_INV-9001.pdf", "Acme Supplies", "INV-9001", 5000, "PO-10245")
make_invoice("02_edge_tolerance_INV-9002.pdf", "Contoso Logistics", "INV-9002", 3300, "PO-10247")
make_invoice("03_edge_duplicate_INV-9001.pdf", "Acme Supplies", "INV-9001", 5000, "PO-10245")
make_invoice("04_edge_no_po_INV-9003.pdf", "Acme Supplies", "INV-9003", 1200)
make_invoice("05_edge_split_INV-9004.pdf", "Acme Supplies", "INV-9004", 7500, "PO-10248")
make_invoice("06_edge_split_INV-9005.pdf", "Acme Supplies", "INV-9005", 2000, "PO-10248")
make_invoice("07_edge_vendor_mismatch_INV-9006.pdf", "Globex Parts", "INV-9006", 12500, "PO-10246")

# Scanned-style file: a page with no text layer
blank = fitz.open()
blank.new_page()
blank.save(OUT / "08_edge_scanned.pdf")
blank.close()

print(f"Created {len(list(OUT.glob('*.pdf')))} sample PDFs in {OUT}")
