import requests
from datetime import datetime, timedelta, time
import pytz
from utils.state_timezones import get_timezone_for_state
import azure.functions as func
import json


# --------------------------------------------------
# Helper to log dependency calls
# --------------------------------------------------
def log_dep(url, payload, status_code, response_json):
    return {
        "url": url,
        "payload": payload,
        "status_code": status_code,
        "response": response_json
    }


def _is_offset_like_tz(tz_str: str) -> bool:
    """
    Treat pure offset-style strings (e.g. '-03', '+02') as invalid
    for customer timezone resolution so we can fall back to state-based TZ.
    """
    if not tz_str:
        return False
    s = tz_str.strip()
    return s.startswith(("+", "-")) and len(s) <= 5


# --------------------------------------------------
# Resolve customer timezone using API calls
# --------------------------------------------------
def resolve_customer_timezone(contact_id, location_id, state_code, auth_token, dep_logs):
    base_headers = {"Authorization": f"Bearer {auth_token}"}

    # 1️⃣ Contact timezone (customer)
    if contact_id:
        contact_url = f"https://services.leadconnectorhq.com/contacts/{contact_id}"
        headers = base_headers.copy()
        headers["version"] = "2021-04-15"

        res = requests.get(contact_url, headers=headers)

        try_json = {}
        try:
            try_json = res.json()
        except Exception:
            pass

        dep_logs.append(log_dep(contact_url, {}, res.status_code, try_json))

        if res.status_code == 200:
            tz = try_json.get("contact", {}).get("timezone")
            if tz and not _is_offset_like_tz(tz):
                try:
                    return pytz.timezone(tz)
                except Exception:
                    # fall through to state
                    pass

    # 2️⃣ Location call ONLY for logging (do NOT use its timezone for customer)
    if location_id:
        loc_url = f"https://services.leadconnectorhq.com/locations/{location_id}"
        headers = base_headers.copy()
        headers["version"] = "2021-07-28"

        res = requests.get(loc_url, headers=headers)

        try_json = {}
        try:
            try_json = res.json()
        except Exception:
            pass

        dep_logs.append(log_dep(loc_url, {}, res.status_code, try_json))
        # intentionally NOT using location.timezone as customer timezone

    # 3️⃣ State fallback (customer timezone)
    return pytz.timezone(get_timezone_for_state(state_code))



def format_datetime(dt):
    offset_minutes = int(dt.utcoffset().total_seconds() / 60)

    tz_full_names = {
        -480: "Pacific Standard Time",   # UTC-8
        -420: "Pacific Daylight Time",   # UTC-7
        # you can add more here if you want nice names for EST, CST, etc.
    }

    full_name = tz_full_names.get(offset_minutes, dt.tzinfo.tzname(dt))

    return dt.strftime(f"%A %B %#d, %Y at %#I:%M %p {full_name}")


def parse_requested_datetime(dt_str, customer_tz):
    try:
        naive = datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")
        return customer_tz.localize(naive)
    except Exception:
        raise ValueError("requested_date_time must be in format YYYY-MM-DD HH:MM AM/PM")


def is_in_business_hours(dt):
    return time(8, 0) <= dt.time() <= time(17, 0)



def handle_leadconnector_request(req):
    orgid = req.params.get("orgid")
    requested_dt_str = req.params.get("requested_date_time")
    state_code = req.params.get("state_code")
    calendar_id = req.params.get("calendar_id")
    contact_id = req.params.get("contact_id")
    location_id = req.params.get("location_id")

    if not orgid or not state_code:
        return func.HttpResponse(
            "Missing parameters: orgid, state_code",
            status_code=400
        )

    auth_token = req.params.get("token") or req.headers.get("Authorization")
    if not auth_token:
        return func.HttpResponse("Missing API token", status_code=400)

    # Collect logs for dependent requests
    dep_logs = []

    # --------------------------------------------------
    # Resolve customer timezone (CONTACT → STATE; location only logged)
    # --------------------------------------------------
    customer_tz = resolve_customer_timezone(
        contact_id, location_id, state_code, auth_token, dep_logs
    )

    # --------------------------------------------------
    # Fetch free slots from LeadConnector
    # --------------------------------------------------
    now_utc = datetime.now(pytz.utc)
    start_ms = int(now_utc.timestamp() * 1000)
    end_ms = int((now_utc + timedelta(days=7)).timestamp() * 1000)

    slot_url = f"https://services.leadconnectorhq.com/calendars/{calendar_id}/free-slots"
    slot_headers = {"Authorization": f"Bearer {auth_token}"}
    slot_params = {"startDate": start_ms, "endDate": end_ms}

    slot_res = requests.get(slot_url, headers=slot_headers, params=slot_params)

    slot_json = {}
    try:
        slot_json = slot_res.json()
    except Exception:
        pass

    dep_logs.append(log_dep(slot_url, slot_params, slot_res.status_code, slot_json))

    if slot_res.status_code != 200:
        return func.HttpResponse(
            json.dumps({
                "available_times": {"array": [], "string": ""},
                "dependent_requests": dep_logs
            }),
            mimetype="application/json",
            status_code=500
        )

    data = slot_json

    # Flatten provider slots (these are in provider/calendar timezone, e.g. -03)
    provider_slots = []
    for _, obj in data.items():
        if isinstance(obj, dict) and "slots" in obj:
            for slot_str in obj["slots"]:
                provider_slots.append(datetime.fromisoformat(slot_str))

    if not provider_slots:
        result = {
            "available_times": {"array": [], "string": ""},
            "dependent_requests": dep_logs
        }
        return func.HttpResponse(json.dumps(result), mimetype="application/json")

    provider_tz = provider_slots[0].tzinfo  # e.g. -03 / America/Sao_Paulo

    # --------------------------------------------------
    # Request-date handling
    # --------------------------------------------------
    if requested_dt_str:
        # requested time is in customer's local timezone
        requested_customer = parse_requested_datetime(requested_dt_str, customer_tz)
        requested_provider = requested_customer.astimezone(provider_tz)

        start_day_provider = requested_provider.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        eligible_slots = [slot for slot in provider_slots if slot >= start_day_provider]

        # convert provider slots -> customer timezone
        times_customer = [
            slot.astimezone(customer_tz) for slot in eligible_slots
        ]

        times_customer = [dt for dt in times_customer if is_in_business_hours(dt)]

        times_customer = sorted(
            times_customer,
            key=lambda dt: abs(dt - requested_customer)
        )

    else:
        now_provider = datetime.now(provider_tz)
        eligible_slots = [slot for slot in provider_slots if slot >= now_provider]

        # convert provider slots -> customer timezone
        times_customer = [slot.astimezone(customer_tz) for slot in eligible_slots]
        times_customer = [dt for dt in times_customer if is_in_business_hours(dt)]

    # --------------------------------------------------
    # Format output
    # --------------------------------------------------
    formatted_array = [format_datetime(dt) for dt in times_customer]
    formatted_string = "; ".join(formatted_array)

    response_payload = {
        "available_times": {
            "array": formatted_array,
            "string": formatted_string
        },
        "dependent_requests": dep_logs
    }

    return func.HttpResponse(
        json.dumps(response_payload),
        mimetype="application/json",
        status_code=200
    )
