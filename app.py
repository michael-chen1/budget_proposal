from dotenv import load_dotenv
load_dotenv()

import os
from flask import Flask, request, render_template, session, send_file, redirect, url_for, jsonify
from extractors import get_data_biostats, calculate_dmc, calculate_refresh, get_data_dm, get_data_pm, get_data_conform
from excel_utils import populate_template
from openpyxl import Workbook, load_workbook
import tempfile
import json


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_KEY", "edetek123")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

ALLOWED_EXTENSIONS = {"pdf", "docx"}


# Short descriptions for each extracted field. Any field not listed here will
# simply render with a blank description cell in the results table.
FIELD_DESCRIPTIONS = {
    "Study Title": "Official name of the study or protocol.",
    "Therapeutic Area": "Primary therapeutic area or indication for the study.",
    "Planned Enrollment": "Total number of participants expected to enroll.",
    "Study Start Date": "Projected first subject in date.",
    "Study End Date": "Projected last subject out or database lock date.",
}


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates_xlsx", "template_full.xlsx")

SHEETS_MAP = {"biostats": ["Study Information", "Biostatistics and Programming"],
              "data_management": ["Study Information", "Clinical Data Management"],
              "project_management": ["Study Information", "Project Management"],
              "conform": ["Study Information", "CONFORM Informatics"],
              }


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )

def run_extraction(steps, documents, refresh_opts, dmc_opts):

    data = {}
    print(2)
    if "conform" in steps:
        data.update(get_data_conform(documents))
    if "project_management" in steps:
        data.update(get_data_pm(documents))
    if "data_management" in steps:
        data.update(get_data_dm(documents))
    if "biostats" in steps:
        data.update(get_data_biostats(documents))

    return data

def run_substeps(steps, data, refresh_opts, dmc_opts):
    # if refresh_opts is a tuple (do_refresh, docs[])
    do_refresh, refresh_docs = refresh_opts
    r = True
    if not refresh_docs:
        r = False        
        
    if "biostats" in steps and do_refresh:
        data.update(calculate_refresh(data, refresh_docs, r))

    # same for DMC:
    do_dmc, dmc_docs = dmc_opts
    d = True
    if not dmc_docs:
        d = False
    if "biostats" in steps and do_dmc:
        data.update(calculate_dmc(data, dmc_docs, d))

    return data



@app.route("/", methods=["GET", "POST"])
def select_types():
    if request.method == "POST":
        chosen = request.form.getlist("steps")
        if not chosen:
            return render_template("select_types.html", error="Pick at least one.")
        session["extraction_steps"] = chosen
        session.pop("extracted", None)
        return redirect(url_for("upload_and_extract"))
    return render_template("select_types.html")


@app.route("/upload", methods=["GET", "POST"])
def upload_and_extract():
    steps = session.get("extraction_steps") or []

    if request.method == "POST":
        # 1) Gather any top‑level uploads
        raw_docs = request.files.getlist("docs")
        documents = [
            {
              "file_bytes": f.read(),
              "format": f.filename.rsplit(".", 1)[1].lower(),
              "name":    os.path.splitext(f.filename)[0]
            }
            for f in raw_docs
            if f and allowed_file(f.filename)
        ]

        # 2) Check sub‑step flags and their helper docs
        do_refresh = request.form.get("calculate_refresh") == "yes"
        refresh_docs = [
            {
              "file_bytes": f.read(),
              "format": f.filename.rsplit(".", 1)[1].lower(),
              "name":    os.path.splitext(f.filename)[0]
            }
            for f in request.files.getlist("refresh_docs")
            if f and allowed_file(f.filename)
        ]

        do_dmc = request.form.get("calculate_dmc") == "yes"
        dmc_docs = [
            {
              "file_bytes": f.read(),
              "format": f.filename.rsplit(".", 1)[1].lower(),
              "name":    os.path.splitext(f.filename)[0]
            }
            for f in request.files.getlist("dmc_docs")
            if f and allowed_file(f.filename)
        ]

        # —————————————————————————————
        # A) SAVE‑CHANGES ONLY (no docs, no flags)
        # —————————————————————————————
        if not documents and not do_refresh and not do_dmc:
            # Merge every non‑control form field into session["extracted"]
            data = session.get("extracted", {}).copy()
            for key, val in request.form.items():
                if key not in (
                    "calculate_refresh",
                    "calculate_dmc",
                    "refresh_file_opt_in",
                    "dmc_file_opt_in"
                ):
                    data[key] = val
            session["extracted"] = data

            # Re‑render results table with blanks for any -1
            display = {
              k: ("" if v in (-1, "-1") else v)
              for k, v in data.items()
            }
            return render_template(
                "results.html",
                results=display,
                descriptions=FIELD_DESCRIPTIONS,
            )
        
        if session.get("base_done") and (do_refresh or do_dmc):
            data = session.get("extracted", {}).copy()
            extract = run_substeps(
                steps,
                data,
                (do_refresh, refresh_docs),
                (do_dmc,     dmc_docs),
            )
            session["extracted"] = extract


            display = {k: ("" if v in (-1, "-1") else v) for k, v in data.items()}
            return render_template(
                "results.html",
                results=display,
                descriptions=FIELD_DESCRIPTIONS,
            )

        
        print(8)
        session.pop("base_done", None)
        data = run_extraction(steps, documents, (do_refresh, refresh_docs), (do_dmc, dmc_docs))  
        session["base_done"] = True
        session["extracted"] = data
        print(1)
        display = {k: ("" if v in (-1, "-1") else v) for k, v in data.items()}
        return render_template(
            "results.html",
            results=display,
            descriptions=FIELD_DESCRIPTIONS,
        )

    # GET → show upload form
    return render_template("upload.html")



@app.route("/export", methods=["POST"])
def export():
    steps = session.get("extraction_steps", [])
    data  = session.get("extracted")
    if not steps or not data:
        return redirect(url_for("select_types"))

    # --- sanitize ---
    sanitized = {
        k: ("" if v == -1 or v == "-1" else v)
        for k, v in data.items()
    }
    
    # 1) Fill the master template into a temp file
    tmp_in = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_in.close()
    populate_template(sanitized,TEMPLATE_PATH,tmp_in.name)

    # 2) Load the populated workbook and decide which sheets to keep
    wb = load_workbook(tmp_in.name)
    to_keep = set()
    for step in steps:
        # grab the list of sheet names for this step
        for sheet_name in SHEETS_MAP.get(step, []):
            if sheet_name in wb.sheetnames:
                to_keep.add(sheet_name)

    # 3) Remove any sheet not in the to_keep set
    for sheet in list(wb.sheetnames):
        if sheet not in to_keep:
            wb.remove(wb[sheet])

    # 4) Save the pruned workbook to another temp file and send it
    tmp_out = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_out.close()
    wb.save(tmp_out.name)

    resp = send_file(
        tmp_out.name,
        as_attachment=True,
        download_name="budget_proposal.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    @resp.call_on_close
    def cleanup():
        try:
            os.remove(tmp_in.name)
            os.remove(tmp_out.name)
        except OSError:
            pass

    return resp

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
