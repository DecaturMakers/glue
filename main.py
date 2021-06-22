#!/usr/bin/env python3

import atexit
import os
import datetime
from typing import Any, Dict, List, Generator, Optional, Tuple
import sys
import json

from apscheduler.schedulers.background import BackgroundScheduler
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from flask import Flask, request, jsonify
from flask_httpauth import HTTPBasicAuth, HTTPTokenAuth
from dotenv import load_dotenv

load_dotenv()

RFID_TOKENS = [token for token in os.getenv("RFID_TOKENS", "").split(" ") if token]

NEON_USERNAME = "neoncrm"
NEON_PASSWORD = os.getenv("NEON_PASSWORD")

NEON_ORG_ID = "decaturmakers"
NEON_API_KEY = os.getenv("NEON_API_KEY")
NEON_AUTH = (NEON_ORG_ID, NEON_API_KEY)
NEON_FOB_FIELD_NAME = "Fob10Digit"
NEON_FOB_FIELD_ID = 79

NEON_MAX_PAGE_SIZE = 200

NEON_API_ENDPOINT = "https://api.neoncrm.com/v2"


neon_session = requests.session()
retry_strategy = Retry(
    total=3,
    status_forcelist=[429],
)
adapter = HTTPAdapter(max_retries=retry_strategy)
neon_session.mount("https://", adapter)

scheduler = BackgroundScheduler()

# TODO support multiple RFID readers and allow access to each to be mapped to a
# field in the CRM
authorized_fobs: Optional[List[str]] = None


def gen_authorized_fobs() -> Generator[str, None, None]:
    today = datetime.datetime.now()
    cutoff = today - datetime.timedelta(days=7)

    def get_page(page: int) -> Tuple[int, List[Dict[str, Any]]]:
        search_res = neon_session.post(
            f"{NEON_API_ENDPOINT}/accounts/search",
            auth=NEON_AUTH,
            timeout=10,
            json={
                "outputFields": [
                    "Full Name (F)",
                    "Membership Expiration Date",
                    NEON_FOB_FIELD_ID,
                ],
                "pagination": {
                    "currentPage": page,
                    "pageSize": NEON_MAX_PAGE_SIZE,
                },
                "searchFields": [
                    {
                        "field": "Membership Expiration Date",
                        "operator": "GREATER_AND_EQUAL",
                        "value": cutoff.strftime("%Y-%m-%d"),
                    },
                    {
                        "field": "COVID Training",
                        "operator": "NOT_BLANK",
                    },
                ],
            },
        )
        search_res.raise_for_status()
        search_dict = search_res.json()

        last_page = search_dict["pagination"]["totalPages"] - 1
        results = search_dict["searchResults"]

        return last_page, results

    current_page = 0
    last_page = 0

    while current_page <= last_page:
        last_page, results = get_page(current_page)
        for result in results:
            fob_id = result[NEON_FOB_FIELD_NAME]
            if fob_id:
                yield fob_id
        current_page += 1


def update_authorized_fobs():
    global authorized_fobs
    print("Updating authorized fobs...")
    try:
        authorized_fobs = list(gen_authorized_fobs())
    except requests.HTTPError as e:
        pass


app = Flask(__name__)
neon_auth = HTTPBasicAuth()

# NeonCRM webhooks
@neon_auth.verify_password
def neon_verify_password(username, password):
    return username == NEON_USERNAME and password == NEON_PASSWORD


@app.route("/account/update", methods=["POST"])
@neon_auth.login_required
def update_account():
    # print(json.dumps(request.get_json(), indent=2))
    return jsonify(success=True)


@app.route("/membership/create", methods=["POST"])
@neon_auth.login_required
def create_membership():
    # update_authorized_fobs()
    return jsonify(success=True)


@app.route("/membership/update", methods=["POST"])
@neon_auth.login_required
def update_membership():
    # update_authorized_fobs()
    return jsonify(success=True)


@app.route("/membership/delete", methods=["POST"])
@neon_auth.login_required
def delete_membership():
    # update_authorized_fobs()
    return jsonify(success=True)


rfid_auth = HTTPTokenAuth(scheme="Bearer")


@rfid_auth.verify_token
def rfid_verify_token(token):
    if token in RFID_TOKENS:
        return token


@app.route("/rfid/authenticate")
@rfid_auth.login_required
def rfid_authorized_fobs():
    fob = request.args.get("fob")
    return {"is_authorized": fob in authorized_fobs, "authorized_fobs": authorized_fobs}


if __name__ == "__main__":
    scheduler.add_job(
        func=update_authorized_fobs,
        next_run_time=datetime.datetime.now(),
        trigger="interval",
        seconds=60,
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    app.run(host="0.0.0.0", port=5050)
