from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

import boto3
from botocore.exceptions import ClientError
import json
from typing import List, Dict, Any, Optional
import math
import os

import ast, re

_FENCE_RE = re.compile(
    r"```(?:\s*python)?\s*(.*?)\s*```",
    flags=re.IGNORECASE | re.DOTALL,
)

def _first_braced_block(s: str) -> str:
    """Fallback: return the first balanced { ... } block."""
    i = s.find("{")
    if i == -1:
        raise ValueError("No JSON object found in response")
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[i:j+1]
    # Unbalanced braces; return the remainder so downstream tries can still work
    return s[i:]


def extract_dict(response_text: str):
    """
    Parse a dict from a model reply of the form:

        [some text]
        ```python
        { ... dictionary contents ... }
        ```
        [more text]

    Strategy:
      1) Extract first fenced code block (```python ... ```); parse that.
      2) If no fence, fall back to the first balanced { ... } slice.
      3) Try ast.literal_eval (accepts single quotes/True/None).
      4) Fall back to strict JSON (with light normalization).
    """
    s = (response_text or "").strip()

    # 1) Prefer content inside the first fenced code block
    m = _FENCE_RE.search(s)
    if m:
        body = m.group(1).strip()
    else:
        # 2) Fallback: isolate the first { ... } block from the whole text
        body = _first_braced_block(s).strip()

    # 3) Python-literal parse first (handles single quotes, True/False/None)
    try:
        obj = ast.literal_eval(body)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 4) Light normalization → JSON, then parse strictly
    body_jsonish = (
        body
        .replace(": True", ": true")
        .replace(": False", ": false")
        .replace(": None", ": null")
    )
    # As a final nudge, if keys/strings are single-quoted, convert to double quotes.
    # (Most tricky apostrophes are already handled by ast path above.)
    if "'" in body_jsonish and '"' not in body_jsonish:
        body_jsonish = re.sub(r"'", '"', body_jsonish)

    try:
        obj = json.loads(body_jsonish)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError as e:
        raise ValueError("Parsed JSON is not an object/dict") from e

    

session = boto3.Session(profile_name = "michael-chen", region_name = "us-east-1")
brt = session.client("bedrock-runtime")
model_id = 'anthropic.claude-3-5-sonnet-20240620-v1:0'


def _is_missing(value: Any) -> bool:
    """Return True when a value represents an unknown quantity."""
    return value in (-1, "-1", None, "")


def _coerce_number(value: Any) -> Optional[float]:
    """Best-effort conversion of ``value`` to a float, respecting sentinel values."""
    if _is_missing(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_set_total_duration(data: Dict[str, Any]) -> None:
    """Populate ``total_dur`` when possible, otherwise leave the sentinel value."""
    if not _is_missing(data.get("total_dur")):
        return

    enroll = _coerce_number(data.get("enroll_dur"))
    subj = _coerce_number(data.get("subj_dur"))

    if enroll is None or subj is None:
        return

    data["total_dur"] = max(enroll + subj, enroll, subj)



def _build_conversation(prompt: str, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    documents: [
      {"file_bytes": b"...", "format": "pdf", "name": "MyProtocol"},
      {"file_bytes": b"...", "format": "docx","name": "Supplement"}
      ...
    ]
    """
    docs_payload = []
    for doc in documents:
        docs_payload.append({
            "document": {
                "format": doc["format"],
                "name":   doc["name"],
                "source": {"bytes": doc["file_bytes"]}
            }
        })
    return [{
        "role": "user",
        "content": [{"text": prompt}, *docs_payload]
    }]


def get_provided_data(documents):
    prompt = """You are an expert in the clinical data management industry, trained to extract study information from provided documents.
You will receive a study protocol along with other supporting document(s), and a list of variables with brief descriptions that you need to extract from the documents.
Below are the variables to extract:

to_extract = {
    num_countries: specified number of countries,
    num_sites: specified number of sites,
    num_subj: specified number of enrolled subjects,
    enroll_dur: specified duration of enrollment in months,
    subj_dur: specified duration of subject participation/treatment in months,
    total_dur: specified duration of the whole study in months,
    num_visits: specified number of visits per patient,
    avg_unscheduled_visits: estimated average number of unscheduled vists per patient,
    dmc/ia: whether or not this study involves the use of a data monitoring committee (dmc) or interim analysis (ia) (output as a boolean true/false)

}

Output the extracted quantities in the format of a Python dictionary with keys written exactly as above. If a quantity cannot be found, write its value as -1. Make sure you enter an integer only for each entry.
It is imperative that the durations are in months. Make sure to convert them to months."""
    conversation = _build_conversation(prompt, documents)
    client = brt
    mid = model_id

    try:
        response = client.converse(
            modelId=mid,
            messages=conversation,
            inferenceConfig={"maxTokens": 1000, "temperature": 0.3},
        )
        response_text = response["output"]["message"]["content"][0]["text"]
    except (ClientError, Exception) as e:
        raise RuntimeError(f"Failed to invoke model: {e}")

    data = extract_dict(response_text)
    return data


def get_assumed_data(documents: List[Dict[str, Any]]) -> dict:
    prompt = """You are an expert in the clinical data management industry, trained to read provided documents to accurately predict study-related variables.
You will receive a study protocol along with other supporting document(s), and a list of variables with brief descriptions that you need to predict from the documents.
Below are the variables to predict:

to_extract = {
    sdtm_sd: predicted number of SDTM subject domains,
    adam_simp: predicted number of simple ADaM domains (most safety domains),
    adam_compl: predicted number of complex ADaM domains (ADSL, ADLB, ADEX, Efficacy),
    stat_support_requests: predicted number of statistical support hours needed throughout study duration,
    prog_support_requests: predicted number of programming support hours needed throughout study duration,
    tlf_unique_tables: specified number of unique tables,
    tlf_repeat_tables: specified number of repeat tables,
    tlf_unique_figures: specified number of unique figures,
    tlf_repeat_figures: specified number of repeat figures,
    tlf_unique_listings: specified number of unique listings,
    tlf_repeat_listings: specified number of repeat listings
}

Output the predicted quantities in the format of a Python dictionary with keys written exactly as above. These quantities should be estimated using the following non-comprehensive general guidelines:

How to estimate the number of SDTM domains, ADaM datasets and TLFs (unique tables, repeat tables, unique listings, repeat listings, unique figures and repeat figures) based on the protocol? 

sdtm_sd: Under the protocol, there will be one table named schedule of assessments (SOA). Procedure name can be mapped with SDTM datasets.
The trial design domains TA, TE, TI, TS, TV, are automatically included. The special purpose domains SE, SV, and RELREC will also be automatically included.
Generally, subject level datasets DM, AE, MH, PE, CM, EC, EX, DS, DV, EG, VS, IE, LB, SC, RP will be included.
So at least 18 subject domains (sdtm_sd) will be needed (do not count the trial design domains as subject domains).

adam_simp and adam_compl: Under protocol synopsis, the objective and endpoint section contains all necessary information.
Based on the contents, the ADaM datasets will be estimated. Generally, 6 simple datasets ADAE, ADCM, ADMH, ADDV, ADEG, ADVS and 4 complex datasets ADLB, ADSL, ADEX, ADEXSUM will be needed.
For oncology study, complex datasets ADRS, ADTR, ADTTE, ADEFF (optional) will be needed. For pharmacokinetics study, simple datasets ADPP, ADPC will be needed.
For anti-drug antibodies analysis, simple dataset ADADA will be needed. For pharmacodynamics analysis, complex dataset ADPD will be needed.
This means adam_simp and adam_compl should be at least 6 and 4, respectively (and potentially more).

tlf: It is hard to estimate TLF count based on protocol. Generally, at a minimum, 16 unique tables and 9 repeat tables
[1 analysis set, 1 demographic, 1 MH, 1 CM, 1 DV, 1 disposition, 8 AE (3 unique, 5 repeat), 6 LB (2 unique 4 repeat), 2 VS, 2 EG, 1 EX] will be needed for the safety and baseline analysis.
16 unique listings and 5 repeat listings will be needed.
The count of figure will depend, not all studies need figures. Furthermore, the count of repeat tables will be determined by the study design.
If there are multiple phases/periods/parts, the repeat tables might be multiplied by the count of phases/periods/parts.  
Generally, for Phase 1 study, the count will be 50-100 (25 unique tables 20 repeat tables 20 unique listings 10 repeat listings 5 unique figures 3 repeat figures) if no multiple study parts. For Phase 2 study, 80-150 tlfs (30 unique tables 30RT 30 unique listings 10 repeat listings 10 unique figures 5 repeat figures).
For Phase 3 study, 120-200 tlfs (35 unique tables 50 repeat tables 40 unique listings 15 repeat listings 15 unique figures 10 repeat figures).  


"""
    conversation = _build_conversation(prompt, documents)
    client = brt
    mid = model_id

    try:
        response = client.converse(
            modelId=mid,
            messages=conversation,
            inferenceConfig={"maxTokens": 1000, "temperature": 0.3},
        )
        response_text = response["output"]["message"]["content"][0]["text"]
    except (ClientError, Exception) as e:
        raise RuntimeError(f"Failed to invoke model: {e}")

    print(response_text)
    data = extract_dict(response_text)
    data.update(
        tlf_final_unique_tables = data["tlf_unique_tables"],
        tlf_final_repeat_tables = data["tlf_repeat_tables"],
        tlf_final_unique_figures = data["tlf_unique_figures"],
        tlf_final_repeat_figures = data["tlf_repeat_figures"],
        tlf_final_unique_listings = data["tlf_unique_listings"],
        tlf_final_repeat_listings = data["tlf_repeat_listings"],
    )
    return data




def get_data_biostats(documents):
    #Returns dictionary of data needed to calculate price
    data = {
        "dmc/ia": False,
        "num_countries": -1,
        "num_sites": -1,
        "screen_failure_rate": 0.2,
        "dropout_rate": 0.15,
        "num_screened_subj": -1,
        "num_screen_fail": -1,
        "num_subj": -1,
        "num_complete": -1,
        "num_withdrawn": -1,
        "num_visits": -1,
        "avg_unscheduled_visits": -1,
        "start_dur": -1,
        "enroll_dur": -1,
        "subj_dur": -1,
        "close_dur": -1,
        "analysis_dur": -1,
        "total_dur": -1,
        "sdtm_tdd": 5, #always 5
        "sdtm_sd": -1,
        "sdtm_dmc_fr": -1,
        "sdtm_ia_fr": -1,
        "sdtm_fr":  -1,
        "adam_simp": -1,
        "adam_compl": -1,
        "adam_dmc_fr": -1,
        "adam_ia_fr": -1,
        "adam_fr": -1,
        "tlf_dmc_unique_tables": -1,
        "tlf_dmc_repeat_tables": -1,
        "tlf_dmc_unique_figures": -1,
        "tlf_dmc_repeat_figures": -1,
        "tlf_dmc_unique_listings": -1,
        "tlf_dmc_repeat_listings": -1,
        "tlf_dmc_fr": -1,
        "tlf_ia_unique_tables": -1,
        "tlf_ia_repeat_tables": -1,
        "tlf_ia_unique_figures": -1,
        "tlf_ia_repeat_figures": -1,
        "tlf_ia_unique_listings": -1,
        "tlf_ia_repeat_listings": -1,
        "tlf_ia_fr": -1,
        "tlf_final_unique_tables": -1,
        "tlf_final_repeat_tables": -1,
        "tlf_final_unique_listings": -1,
        "tlf_final_repeat_listings": -1,
        "tlf_final_unique_listings": -1,
        "tlf_final_repeat_listings": -1,
        "tlf_final_fr": -1,
        "safety_signal_report": -1,
        "stat_support_requests": -1,
        "prog_support_requests": -1,
        "num_dmc_meet": -1, 
        "dsur_report_tables": -1,
        "dsur_report_listings": -1,
        "dsur_datasets": -1,
        "dsur_years": -1,
        "investigator_tables": -1,
        "investigator_listings": -1,
        "investigator_datasets": -1,
        "investigator_years": -1,
        "patient_profile": -1,
        "num_meetings": -1
        }
    print(6)
    data1 = get_provided_data(documents)
    data2 = get_assumed_data(documents)
    
    data.update(data1)
    data.update(data2)

    #run hard-coded formulas
    _maybe_set_total_duration(data)
    
##    enroll = data.get("enroll_dur", 0)
##    subject = data.get("subj_dur", 0)
##    total = data.get("total_dur", 0)
##
##    #check for wrong units (weeks/months) -> months
##    if enroll != 0 and enroll > 2 * subject:
        

    return data



def calculate_dmc(data, documents, use_files):
    def to_number(key):
        return _coerce_number(data.get(key, -1))

    def set_scaled(target: str, value: Optional[float], factor: float) -> None:
        data[target] = -1 if value is None else math.floor(value * factor)

    fu = to_number("tlf_final_unique_tables")
    fr = to_number("tlf_final_repeat_tables")
    gu = to_number("tlf_final_unique_figures")
    gr = to_number("tlf_final_repeat_figures")
    lu = to_number("tlf_final_unique_listings")
    lr = to_number("tlf_final_repeat_listings")
    sd = to_number("subj_dur")
    td = to_number("total_dur")

    set_scaled("tlf_dmc_unique_tables", fu, 0.6)
    set_scaled("tlf_dmc_repeat_tables", fr, 0.6)
    set_scaled("tlf_dmc_unique_figures", gu, 0.6)
    set_scaled("tlf_dmc_repeat_figures", gr, 0.6)
    set_scaled("tlf_dmc_unique_listings", lu, 0.6)
    set_scaled("tlf_dmc_repeat_listings", lr, 0.6)

    set_scaled("tlf_ia_unique_tables", fu, 0.75)
    set_scaled("tlf_ia_repeat_tables", fr, 0.75)
    set_scaled("tlf_ia_unique_figures", gu, 0.75)
    set_scaled("tlf_ia_repeat_figures", gr, 0.75)
    set_scaled("tlf_ia_unique_listings", lu, 0.75)
    set_scaled("tlf_ia_repeat_listings", lr, 0.75)

    data["dsur_report_tables"] = 10
    data["dsur_report_datasets"] = 7
    data["dsur_years"] = -1 if td is None else math.ceil(td / 12.0)

    if use_files:
        prompt = """You are an expert in the clinical data management industry, trained to extract study information related to the DMC (data monitoring committee) from provided documents. Below are the variable(s) to extract:

    to_extract = {
        num_dmc_meet: specified number of meetings for the DMC (data monitoring committee),
        dmc_meet_freq: frequency of DMC meetings in terms of months (i.e. every 3 months)

    }

    Output the extracted quantities in the format of a Python dictionary with keys written exactly as above. If a quantity cannot be found, write its value as -1. Make sure you enter an integer only for each entry.
    It is imperative that the durations are in months. Make sure to convert them to months."""
        client = brt
        mid = model_id
        conversation = _build_conversation(prompt, documents)

        try:
            response = client.converse(
                modelId=mid,
                messages=conversation,
                inferenceConfig={"maxTokens": 1000, "temperature": 0.3},
            )
            response_text = response["output"]["message"]["content"][0]["text"]
        except (ClientError, Exception) as e:
            raise RuntimeError(f"Failed to invoke model: {e}")

        dmc_data = extract_dict(response_text)
        
        reported_meetings = _coerce_number(dmc_data.get("num_dmc_meet"))
        meet_freq = _coerce_number(dmc_data.get("dmc_meet_freq"))

        if reported_meetings is not None:
            num_dmc_meet = int(reported_meetings)
        elif sd is None:
            num_dmc_meet = -1
        elif meet_freq is not None and meet_freq != 0:
            num_dmc_meet = math.ceil(sd / meet_freq) + 1
        else:
            num_dmc_meet = math.ceil(sd / 6) + 1

    else:
        if sd is None:
            num_dmc_meet = -1
        else:
            num_dmc_meet = math.ceil(sd / 6) + 1

    data["num_dmc_meet"] = num_dmc_meet
    data["sdtm_dmc_fr"] = num_dmc_meet
    data["adam_dmc_fr"] = num_dmc_meet
    data["tlf_dmc_fr"] = num_dmc_meet
        

    return data
    
def calculate_refresh(data, documents, use_files):
    sd = _coerce_number(data.get("subj_dur"))
    if use_files:
        prompt = """You are an expert in the clinical data management industry, trained to extract certain study-related information from provided documents. Below are the variable(s) to extract:

    to_extract = {
        sdtm_fr: specified number of full refreshes for SDTM datasets,
        adam_fr: specified number of full refreshes for ADaM datasets,
        tlf_final_fr: specified number of full refreshes for TLFs
        

    }

    Output the extracted quantities in the format of a Python dictionary with keys written exactly as above. If a quantity cannot be found, write its value as -1. Make sure you enter an integer only for each entry.
    It is imperative that the durations are in months. Make sure to convert them to months."""
        conversation = _build_conversation(prompt, documents)
        mid = model_id
        client = brt

        try:
            response = client.converse(
                modelId=mid,
                messages=conversation,
                inferenceConfig={"maxTokens": 1000, "temperature": 0.3},
            )
            response_text = response["output"]["message"]["content"][0]["text"]
        except (ClientError, Exception) as e:
            raise RuntimeError(f"Failed to invoke model: {e}")

        refresh_data = extract_dict(response_text)
        
        if _is_missing(refresh_data.get("sdtm_fr")):
            sdtm_fr = -1 if sd is None else sd * 1.5
        else:
            sdtm_fr = refresh_data["sdtm_fr"]
        if _is_missing(refresh_data.get("adam_fr")):
            adam_fr = -1 if sd is None else sd * 3
        else:
            adam_fr = refresh_data["adam_fr"]
        if _is_missing(refresh_data.get("tlf_final_fr")):
            tlf_fr = -1 if sd is None else sd
        else:
            tlf_fr = refresh_data["tlf_final_fr"]



    else:
        if sd is None:
            sdtm_fr = -1
            adam_fr = -1
            tlf_fr = -1
        else:
            sdtm_fr = sd * 1.5
            adam_fr = sd * 3
            tlf_fr = sd

    data["sdtm_fr"] = sdtm_fr
    data["adam_fr"] = adam_fr
    data["tlf_final_fr"] = tlf_fr
        

    return data 

def get_data_dm(documents):
    #Returns dictionary of data needed to calculate price
    data = {
        "num_countries": -1,
        "num_sites": -1,
        "screen_failure_rate": 0.2, #change if necessary
        "dropout_rate": 0.15, #change if necessary
        "num_screened_subj": -1,
        "num_screen_fail": -1,
        "num_subj": -1,
        "num_complete": -1,
        "num_withdrawn": -1,
        "num_visits": -1,
        "avg_unscheduled_visits": -1,
        "start_dur": -1,
        "enroll_dur": -1,
        "subj_dur": -1,
        "close_dur": -1,
        "analysis_dur": -1,
        "total_dur": -1,
        "data_review_listings": 50, #assumed
        "protocol_deviation_check": 40, #assumed
        "crf_pages_per_visit": 10, #assumed
        "crf_withdrawn_multiplier": 0.5, #assumed
        "crf_pages_screen_fail": 5, #assumed
        "crf_pages_complete": 300, #assumed
        "crf_pages_withdrawn": 150, #assumed
        "crf_pages_total": -1,
        "manual_queries_complete": 20, #assumed
        "manual_queries_withdrawn": 10, #assumed
        "manual_queries_total": -1,
        "auto_queries_complete": 30, #assumed
        "auto_queries_screen_fail": 5, #assumed
        "auto_queries_withdrawn": 15, #assumed
        "auto_queries_total": -1,
        "num_sae": 30, #assumed
        "num_unique_terms_aemh": 2000, #assumed
        "num_unique_terms_cm": 2000, #assumed
        "num_external_data_source": 3, #assumed
        "external_data_reconcilation": 87, #assumed
        "num_local_lab": 25, #assumed
        "num_lab_panel": 5, #assumed
        "num_data_metrics_report": 15, #assumed
        }

    data1 = get_provided_data(documents)
    sf = data.get("screen_failure_rate", 0)
    dr = data.get("dropout_rate", 0)
    sd = data.get("subj_dur", 0)
    if data1["num_subj"] == -1:
        data1["num_subj"] = 100
    data1["num_screened_subj"] = round(1/(1-sf) * data1["num_subj"])
    data1["num_screen_fail"] = round(sf * data1["num_screened_subj"])
    data1["num_complete"] = round((1-dr) * data1["num_subj"])
    data1["num_withdrawn"] = round(dr * data1["num_subj"])
    data.update(data1)
    
    #run hard-coded formulas
    _maybe_set_total_duration(data)

    num_visits = _coerce_number(data.get("num_visits"))
    if num_visits is None:
        num_visits = 2 * sd + 2
        data["num_visits"] = num_visits
    
    crf_pages_per_visit = _coerce_number(data.get("crf_pages_per_visit"))
    if num_visits is None or crf_pages_per_visit is None:
        data["crf_pages_complete"] = -1
    else:
        data["crf_pages_complete"] = int(num_visits * crf_pages_per_visit)

    num_screen_fail = _coerce_number(data.get("num_screen_fail"))
    crf_pages_screen_fail = _coerce_number(data.get("crf_pages_screen_fail"))
    num_complete = _coerce_number(data.get("num_complete"))
    crf_pages_complete = _coerce_number(data.get("crf_pages_complete"))
    avg_unscheduled = _coerce_number(data.get("avg_unscheduled_visits"))
    num_withdrawn = _coerce_number(data.get("num_withdrawn"))
    crf_pages_withdrawn = _coerce_number(data.get("crf_pages_withdrawn"))
    if avg_unscheduled is None:
        avg_unscheduled = num_visits/4.0
        data["avg_unscheduled_visits"] = avg_unscheduled
    if (
        None in (
            num_screen_fail,
            crf_pages_screen_fail,
            num_complete,
            crf_pages_complete,
            avg_unscheduled,
            crf_pages_per_visit,
            num_withdrawn,
            crf_pages_withdrawn,
        )
    ):
        data["crf_pages_total"] = -1
    else:
        crf_total = (
            num_screen_fail * crf_pages_screen_fail
            + num_complete * (crf_pages_complete + avg_unscheduled * crf_pages_per_visit)
            + num_withdrawn * crf_pages_withdrawn
        )
        data["crf_pages_total"] = int(crf_total)

    manual_queries_complete = _coerce_number(data.get("manual_queries_complete"))
    manual_queries_withdrawn = _coerce_number(data.get("manual_queries_withdrawn"))
    if (
        None in (
            num_complete,
            manual_queries_complete,
            num_withdrawn,
            manual_queries_withdrawn,
        )
    ):
        data["manual_queries_total"] = -1
    else:
        manual_total = (
            num_complete * manual_queries_complete
            + num_withdrawn * manual_queries_withdrawn
        )
        data["manual_queries_total"] = int(manual_total)

    auto_queries_screen_fail = _coerce_number(data.get("auto_queries_screen_fail"))
    auto_queries_complete = _coerce_number(data.get("auto_queries_complete"))
    auto_queries_withdrawn = _coerce_number(data.get("auto_queries_withdrawn"))
    if (
        None in (
            num_screen_fail,
            auto_queries_screen_fail,
            num_complete,
            auto_queries_complete,
            num_withdrawn,
            auto_queries_withdrawn,
        )
    ):
        data["auto_queries_total"] = -1
    else:
        auto_total = (
            num_screen_fail * auto_queries_screen_fail
            + num_complete * auto_queries_complete
            + num_withdrawn * auto_queries_withdrawn
        )
        data["auto_queries_total"] = int(auto_total)
        

##    enroll = data.get("enroll_dur", 0)
##    subject = data.get("subj_dur", 0)
##    total = data.get("total_dur", 0)
##
##    #check for wrong units (weeks/months) -> months
##    if enroll != 0 and enroll > 2 * subject:
        

    return data

def get_data_pm(documents):
    data = {
        "start_dur": -1,
        "enroll_dur": -1,
        "subj_dur": -1,
        "close_dur": -1,
        "analysis_dur": -1,
        "total_dur": -1,
    }

    data1 = get_provided_data(documents)

    data.update(data1)
    _maybe_set_total_duration(data)

    return data

def get_data_conform(documents):
    data = {
        "start_dur": -1,
        "enroll_dur": -1,
        "subj_dur": -1,
        "close_dur": -1,
        "analysis_dur": -1,
        "total_dur": -1,
    }

    data1 = get_provided_data(documents)

    data.update(data1)
    _maybe_set_total_duration(data)

    return data

def extract_wo(documents):
    prompt = """You are an expert in the clinical data management industry, trained to extract study information from provided documents.
You will receive a study protocol along with other supporting document(s), and a list of variables with brief descriptions that you need to extract from the documents.
Below are the variables to extract:

to_extract = {
    study_number: The name/number of the study which the protocol references,
    sponsor: The name of the company that is sponsoring this study,

}

Output the extracted quantities in the format of a Python dictionary with keys written exactly as above."""
    conversation = _build_conversation(prompt, documents)
    client = brt
    mid = model_id
    
    try:
        response = client.converse(
            modelId=mid,
            messages=conversation,
            inferenceConfig={"maxTokens": 1000, "temperature": 0.3},
        )
        response_text = response["output"]["message"]["content"][0]["text"]
    except (ClientError, Exception) as e:
        raise RuntimeError(f"Failed to invoke model: {e}")
    print(response_text)
    data = extract_dict(response_text)
    print(data)
    return data
    

    


