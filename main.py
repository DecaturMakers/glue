#!/usr/bin/env python3

from threading import Thread
from typing import Any, Dict, List, Generator, Optional, Tuple, NoReturn
from typing import NamedTuple
import atexit
import datetime
import json
import logging
import os
import queue
import traceback

from apscheduler.schedulers.background import BackgroundScheduler
from dateutil import tz
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_httpauth import HTTPBasicAuth, HTTPTokenAuth
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import gspread
import requests
from google.oauth2 import service_account
import googleapiclient.discovery

load_dotenv()

# BEGIN CONFIGURABLE OPTIONS

TIMEZONE = tz.gettz("America/New_York")
NEON_ORG_ID = "decaturmakers"
NEON_FIELD_NAME_FOB = "Fob10Digit"
NEON_FIELD_NAME_DM_MEMBERS = "Added to dm-members"
NEON_FIELD_NAME_CHECKR = "Invited to Checkr"
CHECKR_WORK_LOCATIONS = [
    {
        "state": "GA",
        "city": "Atlanta",
    }
]
CHECKR_PACKAGE = os.getenv("CHECKR_PACKAGE")

# Tools or areas which require RFID authentication/authorization are called
# "zones" here. This dict specifies which custom fields a user must have
# checked in NeonCRM to be granted access to each zone. For example, access to
# the "front-door" zone used to require the "COVID traning" field be checked on
# the user's account.
# ZONE_REQUIREMENTS = {
#     "front-door": frozenset(("COVID training",)),
# }

# No longer checking the "COVID training" checkbox
ZONE_REQUIREMENTS = {
    "front-door": frozenset(),
    "side-door": frozenset(),
}

# END CONFIGURABLE OPTIONS

RFID_SHEET_URL = os.getenv("RFID_SHEET_URL")
RFID_TOKENS: List[str] = [
    token for token in os.getenv("RFID_TOKENS", "").split(" ") if token
]

NEON_USERNAME = "neoncrm"
NEON_PASSWORD = os.getenv("NEON_PASSWORD")

NEON_API_ENDPOINT = "https://api.neoncrm.com/v2"
NEON_REQUEST_TIMEOUT = 10  # seconds
NEON_API_KEY = os.getenv("NEON_API_KEY")
NEON_AUTH = (NEON_ORG_ID, NEON_API_KEY)

NEON_MAX_PAGE_SIZE = 200

CHECKR_API_ENDPOINT = "https://api.checkr.com/v1"
CHECKR_AUTH = (os.getenv("CHECKR_API_KEY"), "")
CHECKR_REQUEST_TIMEOUT = 30
CHECKR_PER_PAGE = 100

# See https://developers.google.com/identity/protocols/oauth2/service-account#creatinganaccount
# GOOGLE_SCOPES = ["https://www.googleapis.com/auth/admin.directory.groups"]
# google_credentials = service_account.Credentials.from_service_account_file(
#     "./service_account.json", scopes=GOOGLE_SCOPES
# )
# google_service = googleapiclient.discovery.build(
#     "cloudidentity", "v1", credentials=google_credentials
# )

gc = gspread.service_account(filename="./rfid-sheet-service-account.json")
rfid_sheet = gc.open_by_url(RFID_SHEET_URL)

retry_adapter = HTTPAdapter(max_retries=3)

neon_session = requests.session()
neon_session.mount("https://", retry_adapter)

checkr_session = requests.session()
checkr_session.mount("https://", retry_adapter)

logging.basicConfig(level=logging.DEBUG)


class User(NamedTuple):
    """A user in NeonCRM"""

    account_id: str
    name: str
    email: Optional[str]
    fob: Optional[str]
    zones: frozenset[str]
    is_membership_expired: bool
    added_to_dm_members: bool
    is_minor: Optional[bool]
    invited_to_checkr: bool


# Globally keep tables of users
users_by_email: Dict[str, User] = {}
users_by_fob: Dict[str, User] = {}
are_users_known = False


def can_access(neon_result: Dict[str, str], zone: str) -> bool:
    """Returns whether a user (as returned from NeonCRM API) can access a
    zone"""
    if zone not in ZONE_REQUIREMENTS:
        return False
    return all(neon_result.get(field) for field in ZONE_REQUIREMENTS[zone])


class NeonOption(NamedTuple):
    """One possible value of a "custom field" in NeonCRM"""

    name: str
    option_id: int


class NeonField(NamedTuple):
    """A "custom field" in NeonCRM"""

    name: str
    field_id: int
    options: Dict[str, NeonOption]


def neon_get_fields() -> Dict[str, NeonField]:
    """Returns a dict of available NeonCRM custom fields, indexed by their
    names"""
    custom_fields_res = neon_session.get(
        f"{NEON_API_ENDPOINT}/customFields",
        auth=NEON_AUTH,
        timeout=NEON_REQUEST_TIMEOUT,
        params={"category": "Account"},
    )
    custom_fields_res.raise_for_status()
    field_ids: Dict[str, NeonField] = {}
    for field in custom_fields_res.json():
        neon_options: Dict[str, NeonOption] = {}
        option_values = field.get("optionValues") or []
        for option in option_values:
            neon_options[option["name"]] = NeonOption(option["name"], option["id"])
        neon_field = NeonField(field["name"], int(field["id"]), neon_options)
        field_ids[field["name"]] = neon_field
    return field_ids


def neon_set_checkbox(user: User, field_name: str, checked: bool) -> None:
    """Check or uncheck a checkbox field in NeonCRM"""
    neon_field = neon_get_fields()[field_name]
    if checked:
        # for checkboxes, there is hopefully only one option
        option_name = list(neon_field.options.keys())[0]
        neon_option = neon_field.options[option_name]
        option_values = [
            {
                "id": neon_option.option_id,
                "name": neon_option.name,
            }
        ]
        value = neon_option.name
    else:
        option_values = []
        value = ""

    neon_response = neon_session.patch(
        f"{NEON_API_ENDPOINT}/accounts/{user.account_id}",
        auth=NEON_AUTH,
        timeout=NEON_REQUEST_TIMEOUT,
        json={
            "individualAccount": {
                "accountCustomFields": [
                    {
                        "id": neon_field.field_id,
                        "value": value,
                        "name": field_name,
                        "optionValues": option_values,
                    }
                ],
            }
        },
    )
    neon_response.raise_for_status()


# def google_group_add(group_id: str, user: User) -> None:
#     param = "&groupKey.id=" + group_id
#     lookup_group_name_request = service.groups().lookup()
#     lookup_group_name_request.uri += param
#     try:
#         lookup_group_name_response = lookup_group_name_request.execute()
#         group_name = lookup_group_name_response.get("name")
#     except (GoogleHttpError, KeyError) as e:
#         logging.exception(f"Couldn't find a Google Group with ID {group_id}!")

#     membership = {
#         "preferredMemberKey": {
#             "id": "mail@evangoo.de",
#         },
#         "roles": {
#             "name": "MEMBER",
#         }
#     }
#     try:
#         membership_response = service.groups().memberships().create(parent=group_name, body=membership).execute()
#     except GoogleHttpError as e:
#         logging.exception(f"Couldn't add {user.email} to {group_id}!")


def checkr_send_invite(user: User) -> None:
    """Send a Checkr invite to a user and mark them as invited in NeonCRM"""
    existing_candidates_response = checkr_session.get(
        f"{CHECKR_API_ENDPOINT}/candidates",
        auth=CHECKR_AUTH,
        timeout=CHECKR_REQUEST_TIMEOUT,
        params={
            "per_page": CHECKR_PER_PAGE,
            "email": user.email,
        },
    )
    existing_candidates_response.raise_for_status()
    existing_candidates_json = existing_candidates_response.json()
    if existing_candidates_json["count"]:
        candidate_id = existing_candidates_json["data"][0]["id"]
    else:
        new_candidate_response = checkr_session.post(
            f"{CHECKR_API_ENDPOINT}/candidates",
            auth=CHECKR_AUTH,
            timeout=CHECKR_REQUEST_TIMEOUT,
            json={
                "email": user.email,
                "work_locations": CHECKR_WORK_LOCATIONS,
            },
        )
        new_candidate_response.raise_for_status()
        candidate_id = new_candidate_response.json()["id"]

    existing_invitations_response = checkr_session.get(
        f"{CHECKR_API_ENDPOINT}/invitations",
        auth=CHECKR_AUTH,
        timeout=CHECKR_REQUEST_TIMEOUT,
        params={
            "per_page": CHECKR_PER_PAGE,
            "candidate_id": candidate_id,
        },
    )

    existing_invitations_response.raise_for_status()
    existing_invitations_json = existing_invitations_response.json()
    if existing_invitations_json["count"]:
        logging.info(
            "%s has already been invited to Checkr, setting field in NeonCRM...",
            user.email,
        )
        neon_set_checkbox(user, NEON_FIELD_NAME_CHECKR, True)
        logging.info("Set 'Invited to Checkr' field for %s", user.email)
        return

    logging.info("Inviting %s to Checkr...", user.email)
    invitation_response = checkr_session.post(
        f"{CHECKR_API_ENDPOINT}/invitations",
        auth=CHECKR_AUTH,
        timeout=CHECKR_REQUEST_TIMEOUT,
        json={
            "candidate_id": candidate_id,
            "package": CHECKR_PACKAGE,
            "work_locations": CHECKR_WORK_LOCATIONS,
        },
    )

    invitation_response.raise_for_status()
    logging.info("Invited %s to Checkr, setting field in NeonCRM...", user.email)

    neon_set_checkbox(user, NEON_FIELD_NAME_CHECKR, True)
    logging.info("Set 'Invited to Checkr' field for %s", user.email)


def gen_users() -> Generator[User, None, None]:
    """Pull users from NeonCRM"""
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=7)

    field_ids = [field.field_id for field in neon_get_fields().values()]

    def get_page(page: int) -> Tuple[int, List[Dict[str, Any]]]:
        search_res = neon_session.post(
            f"{NEON_API_ENDPOINT}/accounts/search",
            auth=NEON_AUTH,
            timeout=NEON_REQUEST_TIMEOUT,
            json={
                "outputFields": [
                    "Full Name (F)",
                    "Email 1",
                    "Membership Expiration Date",
                    "Account ID",
                    "DOB Day",
                    "DOB Month",
                    "DOB Year",
                    *field_ids,
                ],
                "pagination": {
                    "currentPage": page,
                    "pageSize": NEON_MAX_PAGE_SIZE,
                },
                "searchFields": [
                    {
                        "field": "Account Type",
                        "operator": "EQUAL",
                        "value": "Individual",
                    },
                    {
                        "field": "Membership Expiration Date",
                        "operator": "GREATER_AND_EQUAL",
                        "value": cutoff.strftime("%Y-%m-%d"),
                    },
                ],
            },
        )
        try:
            search_res.raise_for_status()
        except HTTPError:
            logging.warning("Error response from Neon:")
            logging.warning(search_res.text)
            raise
        search_dict = search_res.json()

        last_page = search_dict["pagination"]["totalPages"] - 1
        results = search_dict["searchResults"]

        return last_page, results

    current_page = 0
    last_page = 0

    while current_page <= last_page:
        last_page, results = get_page(current_page)
        for result in results:
            zones = frozenset(
                zone for zone in ZONE_REQUIREMENTS if can_access(result, zone)
            )
            try:
                expiration_date = datetime.datetime.fromisoformat(
                    result.get("Membership Expiration Date")
                )
                expired = now > expiration_date
            except TypeError:
                expired = False
            try:
                if None not in map(result.get, ("DOB Year", "DOB Month", "DOB Day")):
                    dob_year = int(result["DOB Year"])
                    dob_month = int(result["DOB Month"])
                    dob_day = int(result["DOB Day"])
                    now = datetime.datetime.now(TIMEZONE)
                    is_before_dob = now.month < dob_month or (
                        now.month == dob_month and now.day < dob_day
                    )
                    is_minor: Optional[bool] = (
                        now.year - dob_year - (1 if is_before_dob else 0) < 18
                    )
                else:
                    is_minor = None  # unknown whether user is a minor
                yield User(
                    account_id=result["Account ID"],
                    name=result["Full Name (F)"],
                    email=result["Email 1"] or None,
                    fob=result.get(NEON_FIELD_NAME_FOB),
                    zones=zones,
                    is_membership_expired=expired,
                    is_minor=is_minor,
                    added_to_dm_members=bool(result.get(NEON_FIELD_NAME_DM_MEMBERS)),
                    invited_to_checkr=bool(result.get(NEON_FIELD_NAME_CHECKR)),
                )
            except KeyError:
                pass
        current_page += 1


def update_users() -> None:
    """Update the global tables of users. Send Checkr invites and add to Google
    Groups as necessary. This function is run periodically."""
    global users_by_email
    global users_by_fob
    global are_users_known
    logging.info("Updating users")
    try:
        users = list(gen_users())

        # Sanity check: if no users were found, consider the response invalid
        if not users:
            raise ValueError("Got an empty list of users from NeonCRM!")

        new_users_by_email = {}
        new_users_by_fob = {}
        for user in users:
            if user.email is not None:
                new_users_by_email[user.email] = user
            if user.fob is not None:
                new_users_by_fob[user.fob] = user
            if (
                not user.invited_to_checkr
                and user.email is not None
                and user.is_minor is False
            ):
                try:
                    checkr_send_invite(user)
                except (
                    requests.exceptions.HTTPError,
                    requests.exceptions.ConnectionError,
                    KeyError,
                ) as e:
                    logging.exception(f"Failed to send Checkr invite to {user.email}!")

        # Sanity check: if none of the users have fobs, consider the response invalid
        if not new_users_by_fob:
            raise ValueError("NeonCRM sent at least one user, but none have a fob!")

        users_by_email = new_users_by_email
        users_by_fob = new_users_by_fob
        are_users_known = True

    except (requests.HTTPError, requests.exceptions.ConnectionError) as e:
        logging.exception("Error updating users!")


scheduler = BackgroundScheduler()
scheduler.add_job(
    func=update_users,
    next_run_time=datetime.datetime.now(),
    trigger="interval",
    minutes=10,
    misfire_grace_time=None,
    coalesce=True,
)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())


app = Flask(__name__)
neon_auth = HTTPBasicAuth()

# NeonCRM webhooks
@neon_auth.verify_password
def neon_verify_password(username: str, password: str) -> bool:
    return username == NEON_USERNAME and password == NEON_PASSWORD


@app.route("/account/update", methods=["POST"])
@neon_auth.login_required
def update_account() -> str:
    # print(json.dumps(request.get_json(), indent=2))
    return jsonify(success=True)


@app.route("/membership/create", methods=["POST"])
@neon_auth.login_required
def create_membership() -> Any:
    # update_users()
    print("create membership!")
    return jsonify(success=True)


@app.route("/membership/update", methods=["POST"])
@neon_auth.login_required
def update_membership() -> Any:
    # update_authorized_fobs()
    return jsonify(success=True)


@app.route("/membership/delete", methods=["POST"])
@neon_auth.login_required
def delete_membership() -> Any:
    # update_authorized_fobs()
    return jsonify(success=True)


# RFID
rfid_log_queue = queue.Queue[
    Tuple[datetime.datetime, Optional[str], Optional[str], str, Optional[bool]]
]()


def rfid_log_worker() -> NoReturn:
    """Log RFID events to Google Sheets on a separate thread so we can reply to
    the RFID client as soon as possible"""
    while True:
        (timestamp, fob, name, zone, is_authorized) = rfid_log_queue.get()

        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        month = timestamp.strftime("%b %Y")

        log_worksheet_name = f"{month} Log"

        try:
            try:
                log_worksheet = rfid_sheet.worksheet(log_worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                log_template_name = "Log Template"
                log_template = rfid_sheet.worksheet(log_template_name)
                log_worksheet = log_template.duplicate(
                    new_sheet_name=log_worksheet_name
                )
            log_worksheet.append_row([timestamp_str, fob, name, zone, is_authorized])

            report_worksheet_name = f"{month} Report"
            try:
                rfid_sheet.worksheet(report_worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                report_template_name = "Month Report Template"
                report_template = rfid_sheet.worksheet(report_template_name)
                report_worksheet = report_template.duplicate(
                    new_sheet_name=report_worksheet_name
                )
                report_worksheet.update("B2", month)

        except Exception as e:
            logging.exception("Failed to log RFID event in Google Sheets!")
        finally:
            rfid_log_queue.task_done()


Thread(target=rfid_log_worker, daemon=True).start()


rfid_auth = HTTPTokenAuth(scheme="Bearer")


@rfid_auth.verify_token
def rfid_verify_token(token: str) -> Optional[str]:
    if token in RFID_TOKENS:
        return token
    return None


@app.route("/rfid/auth")
@rfid_auth.login_required
def rfid_authenticate() -> Any:
    fob = request.args.get("fob")
    zone = request.args.get("zone", "front-door")
    name: Optional[str] = None
    if not are_users_known:
        is_authorized = None
        authorized_fobs = None
        name = None
        logging.warning(f"Request from fob {fob} at zone {zone}, but users are not yet known! This should not happen often! Was the server restarted recently?")
    else:
        is_authorized = fob in users_by_fob and zone in users_by_fob[fob].zones
        authorized_fobs = [
            fob for fob, user in users_by_fob.items() if zone in users_by_fob[fob].zones
        ]
        if fob in users_by_fob:
            name = users_by_fob[fob].name
        else:
            name = None

    timestamp = datetime.datetime.now(TIMEZONE)

    rfid_log_queue.put((timestamp, fob, name, zone, is_authorized))

    return {"is_authorized": is_authorized, "authorized_fobs": authorized_fobs}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5050)))
else:
    gunicorn_logger = logging.getLogger("gunicorn.error")
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
