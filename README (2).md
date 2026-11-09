# PS-1 · Invoice Processing — PDF to Decision

A working automation for accounts payable. It takes invoice PDFs and returns **Approved**, **Approved with Warning**, **Needs Review**, or **Rejected**, with each check shown.

## Pipeline

1. **Extract**: read the PDF text layer (PyMuPDF)
2. **Parse**: find invoice number, PO reference, vendor, and total
3. **Validate**, in order, stopping at the first decisive failure:
   - Text layer present (scanned files go to Needs Review)
   - Required fields present
   - Duplicate invoice number (Rejected)
   - PO exists in the procurement table
   - Vendor on the invoice matches the PO vendor
   - Amount: within 0.1% = Approved; within 5% = Approved with Warning; above 5% = Needs Review
   - Running total: if approved invoices already exist on this PO, the new invoice is checked against the PO total, which catches split billing
4. **Decide**: final status, confidence, and reason, saved to the dashboard history

Rules are constants at the top of `processor.py` (`EXACT_MATCH_PCT`, `TOLERANCE_PCT`).

## Run locally

```bash
pip install -r requirements.txt
python generate_samples.py
streamlit run app.py
```

## Deploy

### Step 1: Push to GitHub
1. Go to github.com, click **+** → **New repository**.
2. Name it `zamp-ps1-invoice`, keep it Public, click **Create repository**.
3. Click **uploading an existing file**, drag in everything in this folder (not the `data/` folder), and click **Commit changes**.

### Step 2: Deploy on Render
1. Go to render.com and sign up with GitHub.
2. Click **New +** → **Web Service**.
3. Choose **Build and deploy from a Git repository**, then connect `zamp-ps1-invoice`.
4. Fill in:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
5. Under **Environment Variables**, add `PYTHON_VERSION` = `3.11.9`.
6. Click **Create Web Service**. Wait for the status to show **Live**, then open the URL at the top. This is your Deliverable 1 link.

Alternatively, use **New +** → **Blueprint**, since `render.yaml` is included.

## Notes and limitations
- **Free-tier Render sleeps after inactivity** and takes about 30 seconds to wake. Open the link once before your interview.
- **Dashboard history is stored in a local file**, which Render's free tier resets on each redeploy. Re-run the samples before a demo.
- **Scanned PDFs are flagged, not OCR'd.** Adding OCR (Tesseract) is the next step.
- **Partial first invoices** are checked against the full PO amount, so they go to Needs Review. Improving this would mean tracking PO remaining balances.
- **Parsing uses regex** for labelled fields. Unlabelled layouts need an LLM extraction step.

## Single-file version
`invoice_app.py` contains the same app in one file (config, logic, sample generator, UI). Use it if you want a single file to open in VS Code:
```bash
streamlit run invoice_app.py
```
Install `streamlit pandas PyMuPDF` first. Either version works; the multi-file version is easier to extend.
