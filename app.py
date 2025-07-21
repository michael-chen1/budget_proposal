import os
from flask import Flask, request, render_template, session, send_file, redirect, url_for
from extractors import get_data_biostats, calculate_dmc, calculate_refresh, get_data_dm, get_data_pm, get_data_conform
from excel_utils import populate_template
from openpyxl import Workbook, load_workbook
import tempfile


app = Flask(__name__)
app.secret_key = os.environ.get("flask_key", "edetek123")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

ALLOWED_EXTENSIONS = {"pdf", "docx"}

# map each top‑level step to its Excel template
SHEETS_MAP = {
    "biostats":        ["Study Information", "Biostatistics and Programming"],
    "data_management": ["Study Information", "Clinical Data Management"],
    "project_management": ["Study Information", "Project Management"],
    "conform": ["Study Information", "CONFORM Informatics"],
    
}

TEMPLATE_PATH = r"C:\Users\MichaelChen\AppData\Local\Programs\Python\Python312\budget_proposal\templates_xlsx\template_full.xlsx"

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


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
    steps = session.get("extraction_steps")
    if not steps:
        return redirect(url_for("select_types"))

    if request.method == "POST":
        docs = request.files.getlist("docs")

        # ——— 1) INITIAL UPLOAD & CORE EXTRACTION ———
        # Only if there are real uploads in `docs` and no sub-step flags
        if docs and any(f.filename for f in docs) \
           and "calculate_refresh" not in request.form \
           and "calculate_dmc" not in request.form:

            # build documents list
            documents = []
            for f in docs:
                if not allowed_file(f.filename):
                    return render_template("upload.html",
                                           error="Only PDF or DOCX allowed")
                fmt  = f.filename.rsplit(".",1)[1].lower()
                name = os.path.splitext(f.filename)[0]
                documents.append({
                    "file_bytes": f.read(),
                    "format":    fmt,
                    "name":      name
                })

            # run the selected core extractors
            data = {}
            if "conform" in steps:            data.update(get_data_conform(documents))
            if "project_management" in steps: data.update(get_data_pm(documents))
            if "data_management" in steps:   data.update(get_data_dm(documents))
            if "biostats" in steps:           data.update(get_data_biostats(documents))

            # save & render
            session["extracted"] = data
            display = {
                k: ("" if v == -1 or v == "-1" else v)
                for k, v in data.items()
            }
            return render_template("results.html", results=display)

        # ——— 2) ANY OTHER POST ———
        #  2a) First, merge manual edits (Save Changes) into session
        updated = session.get("extracted", {}).copy()
        # take every form field except our control flags and file‑opt flags
        for key, val in request.form.items():
            if key not in ("calculate_refresh","calculate_dmc",
                           "refresh_file_opt_in","dmc_file_opt_in"):
                updated[key] = val
        session["extracted"] = updated
        data = updated

        #  2b) Then run Refresh if requested
        if "biostats" in steps and request.form.get("calculate_refresh"):
            do_refresh   = request.form["calculate_refresh"] == "yes"
            refresh_docs = []
            if request.form.get("refresh_file_opt_in") == "yes":
                for f in request.files.getlist("refresh_docs"):
                    if f and allowed_file(f.filename):
                        fmt  = f.filename.rsplit(".",1)[1].lower()
                        name = os.path.splitext(f.filename)[0]
                        refresh_docs.append({
                            "file_bytes": f.read(),
                            "format":    fmt,
                            "name":      name
                        })
            extra = calculate_refresh(data, refresh_docs, do_refresh)
            data.update(extra)
            session["extracted"] = data

        #  2c) Then run DMC if requested
        if "biostats" in steps and request.form.get("calculate_dmc"):
            do_dmc   = request.form["calculate_dmc"] == "yes"
            dmc_docs = []
            if request.form.get("dmc_file_opt_in") == "yes":
                for f in request.files.getlist("dmc_docs"):
                    if f and allowed_file(f.filename):
                        fmt  = f.filename.rsplit(".",1)[1].lower()
                        name = os.path.splitext(f.filename)[0]
                        dmc_docs.append({
                            "file_bytes": f.read(),
                            "format":    fmt,
                            "name":      name
                        })
            extra = calculate_dmc(data, dmc_docs, do_dmc)
            data.update(extra)
            session["extracted"] = data

        #  3) Finally, render with all updates
        display = {
                k: ("" if v == -1 or v == "-1" else v)
                for k, v in data.items()
        }
        return render_template("results.html", results=display)

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
