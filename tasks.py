# tasks.py
import os, json
from redis import Redis
from rq import get_current_job
from extractors import (
    get_data_conform, get_data_pm,
    get_data_dm, get_data_biostats,
    calculate_refresh, calculate_dmc
)
from flask import current_app
import ssl
import certifi

def make_redis_conn():
    return Redis.from_url(
        os.environ["REDIS_URL"],
        ssl_cert_reqs = None,
        ssl_ca_certs=certifi.where(),
    )

# Connect to the same Redis:
redis_conn = make_redis_conn()
QUEUE_NAME = "default"

def run_extraction(steps, documents, refresh_opts, dmc_opts):
    """
    runs core extraction + optional sub‑steps, returns the final data dict.
    Called in a background RQ worker.
    """
    data = {}
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
