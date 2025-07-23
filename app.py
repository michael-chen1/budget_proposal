import os
from flask import Flask, request, render_template, session, send_file, redirect, url_for, jsonify
from extractors import get_data_biostats, calculate_dmc, calculate_refresh, get_data_dm, get_data_pm, get_data_conform
from excel_utils import populate_template
from openpyxl import Workbook, load_workbook
import tempfile
from redis import Redis
from rq import Queue
import json
import tasks
import ssl
import certifi

def make_redis_conn():
    return Redis.from_url(
        os.environ["REDIS_URL"],
        ssl_cert_reqs = None,
        ssl_ca_certs=certifi.where(),
    )


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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates_xlsx", "template_full.xlsx")

redis_conn = make_redis_conn()
rq_queue  = Queue("default", connection=redis_conn)

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
    steps = session.get("extraction_steps") or []
    if request.method == "POST":
        # collect docs as before
        docs = request.files.getlist("docs")
        documents = [
            {"file_bytes": f.read(), "format": f.filename.rsplit(".",1)[1],
             "name": os.path.splitext(f.filename)[0]}
            for f in docs if f and allowed_file(f.filename)
        ]

        # gather sub‑step flags & docs but DON'T run them yet:
        do_refresh = request.form.get("calculate_refresh") == "yes"
        refresh_docs = [
            {"file_bytes": f.read(), "format": f.filename.rsplit(".",1)[1],
             "name": os.path.splitext(f.filename)[0]}
            for f in request.files.getlist("refresh_docs")
            if f and allowed_file(f.filename)
        ]

        do_dmc = request.form.get("calculate_dmc") == "yes"
        dmc_docs = [
            {"file_bytes": f.read(), "format": f.filename.rsplit(".",1)[1],
             "name": os.path.splitext(f.filename)[0]}
            for f in request.files.getlist("dmc_docs")
            if f and allowed_file(f.filename)
        ]

        # **enqueue** the full job
        job = rq_queue.enqueue(
            tasks.run_extraction,
            steps,
            documents,
            (do_refresh, refresh_docs),
            (do_dmc,    dmc_docs),
            job_timeout=600  # up to 10 minutes
        )

        # store job_id in session so we can poll
        session["job_id"] = job.get_id()

        # render a “please wait” page that polls /status
        return render_template("waiting.html", job_id=job.get_id())

    # GET …
    return render_template("upload.html")


@app.route("/status/<job_id>")
def job_status(job_id):
    job = rq_queue.fetch_job(job_id)
    if not job:
        return jsonify({"status": "unknown"}), 404

    if job.is_finished:
        # save the result into session for later
        session["extracted"] = job.result
        return jsonify({"status": "finished"})
    elif job.is_failed:
        return jsonify({"status": "failed", "error": str(job.exc_info)}), 500
    else:
        return jsonify({"status": job.get_status()})  # queued, started, etc.

@app.route("/results")
def show_results():
    data = session.get("extracted", {})
    display = {k: ("" if v in (-1,"-1") else v) for k,v in data.items()}
    return render_template("results.html", results=display)

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
