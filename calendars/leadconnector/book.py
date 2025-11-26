import requests
from datetime import datetime, timedelta
import pytz
import azure.functions as func
import json
import logging
import re


# -------------------------------------------
# ULTRA robust LLM datetime parser
# -------------------------------------------

def parse_human_datetime(dt_str: str):
    """
    Ultra-robust parser for AI-generated datetime strings.
    Handles:
    - commas anywhere
    - ordinals ("28th")
    - abbreviated months ("Nov")
    - missing colon ("11 30 AM")
    - missing minutes ("11 AM")
    - weird spacing
    - "at" missing or moved
    - full or abbreviated timezones
    """

    from datetime import datetime
    import pytz

    original = dt_str

    # 1) Normalize whitespace
    s = re.sub(r"\s+", " ", dt_str).strip()

    # 2) Remove ordinal suffixes ("st", "nd", "rd", "th")
    s = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s, flags=re.IGNORECASE)

    # 3) Remove commas
    s = s.replace(",", "")

    # 4) Fix missing space before AM/PM ("11AM" → "11 AM")
    s = re.sub(r"(\d)(AM|PM)\b", r"\1 \2", s, flags=re.IGNORECASE)

    # 5) Fix missing colon ("11 30 AM" → "11:30 AM")
    s = re.sub(r"\b(\d{1,2}) (\d{2}) (AM|PM)\b", r"\1:\2 \3", s, flags=re.IGNORECASE)

    # 6) Fix hour-only times ("11 AM" → "11:00 AM")
    s = re.sub(r"\b(\d{1,2}) (AM|PM)\b", r"\1:00 \2", s, flags=re.IGNORECASE)

    # 7) Insert "at" if missing ("Friday November 28 2025 11:00 AM")
    s = re.sub(
        r"(\d{4}) (\d{1,2}:\d{2} (AM|PM))",
        r"\1 at \2",
        s,
        flags=re.IGNORECASE,
    )

    logging.info(f"[PARSE] Normalized datetime: {s}")

    # TIMEZONE MAPPING
    full_tz_map = {
        "PACIFIC STANDARD TIME": "America/Los_Angeles",
        "PACIFIC DAYLIGHT TIME": "America/Los_Angeles",
        "MOUNTAIN STANDARD TIME": "America/Denver",
        "MOUNTAIN DAYLIGHT TIME": "America/Denver",
        "CENTRAL STANDARD TIME": "America/Chicago",
        "CENTRAL DAYLIGHT TIME": "America/Chicago",
        "EASTERN STANDARD TIME": "America/New_York",
        "EASTERN DAYLIGHT TIME": "America/New_York",
    }

    abbrev_map = {
        "PST": "America/Los_Angeles",
        "PDT": "America/Los_Angeles",
        "MST": "America/Denver",
        "MDT": "America/Denver",
        "CST": "America/Chicago",
        "CDT": "America/Chicago",
        "EST": "America/New_York",
        "EDT": "America/New_York",
    }

    # Extract timezone
    tz_regex = (
        r"(Pacific Standard Time|Pacific Daylight Time|Mountain Standard Time|"
        r"Mountain Daylight Time|Central Standard Time|Central Daylight Time|"
        r"Eastern Standard Time|Eastern Daylight Time|PST|PDT|MST|MDT|CST|CDT|EST|EDT)$"
    )

    tz_match = re.search(tz_regex, s, re.IGNORECASE)
    if not tz_match:
        raise ValueError(f"[ERROR] Could not extract timezone from: {original}")

    tz_raw = tz_match.group(0).upper()

    if tz_raw in full_tz_map:
        tz_name = full_tz_map[tz_raw]
    else:
        tz_name = abbrev_map[tz_raw]

    # Strip timezone
    s_no_tz = re.sub(tz_regex, "", s, flags=re.IGNORECASE).strip()
    logging.info(f"[PARSE] Datetime without timezone: {s_no_tz}")

    # Try a wide range of formats
    fmts = [
        "%A %B %d %Y at %I:%M %p",
        "%A %b %d %Y at %I:%M %p",
        "%A %B %d %Y %I:%M %p",
        "%A %b %d %Y %I:%M %p",
        "%B %d %Y at %I:%M %p",
        "%b %d %Y at %I:%M %p",
        "%B %d %Y %I:%M %p",
        "%b %d %Y %I:%M %p",
    ]

    naive = None
    for fmt in fmts:
        try:
            naive = datetime.strptime(s_no_tz, fmt)
            break
        except:
            continue

    if naive is None:
        raise ValueError(
            f"[ERROR] Invalid datetime after normalization: '{s_no_tz}'\nOriginal: '{original}'"
        )

    tz = pytz.timezone(tz_name)
    return tz.localize(naive)



# -------------------------------------------
# Detect calendar timezone
# -------------------------------------------

def detect_calendar_timezone(calendar_id, token):
    now = datetime.now(pytz.utc)
    start_ms = int(now.timestamp() * 1000)
    end_ms = int((now + timedelta(days=7)).timestamp() * 1000)

    url = f"https://services.leadconnectorhq.com/calendars/{calendar_id}/free-slots"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"startDate": start_ms, "endDate": end_ms}

    res = requests.get(url, headers=headers, params=params)
    if res.status_code != 200:
        raise Exception(f"[ERROR] Cannot detect calendar timezone: {res.text}")

    data = res.json()
    for _, obj in data.items():
        if isinstance(obj, dict) and "slots" in obj and obj["slots"]:
            dt = datetime.fromisoformat(obj["slots"][0])
            return dt.tzinfo

    raise Exception("[ERROR] Calendar returned no slots to detect timezone")



# -------------------------------------------
# BOOKING FUNCTION
# -------------------------------------------

def book_leadconnector_appointment(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except:
        return func.HttpResponse("Invalid JSON", status_code=400)

    dt_str      = body.get("proposed_datetime")
    calendar_id = body.get("calendar_id")
    lead_name   = body.get("lead_name")
    location_id = body.get("locationId")
    description = body.get("description", "")
    contact_id  = body.get("contactId")
    token       = body.get("token") or req.headers.get("Authorization")

    if not all([dt_str, calendar_id, lead_name, location_id, contact_id, token]):
        return func.HttpResponse(
            "Missing required fields",
            status_code=400
        )

    try:
        requested_dt = parse_human_datetime(dt_str)
    except Exception as e:
        return func.HttpResponse(str(e), status_code=400)

    try:
        calendar_tz = detect_calendar_timezone(calendar_id, token)
    except Exception as e:
        return func.HttpResponse(str(e), status_code=500)

    calendar_dt = requested_dt.astimezone(calendar_tz)

    start_iso = calendar_dt.isoformat()
    end_iso = (calendar_dt + timedelta(minutes=30)).isoformat()

    payload = {
        "title": f"AI Scheduled Call - {lead_name}",
        "calendarId": calendar_id,
        "locationId": location_id,
        "contactId": contact_id,
        "startTime": start_iso,
        "endTime": end_iso,
        "meetingLocationType": "custom",
        "meetingLocationId": "custom_0",
        "overrideLocationConfig": True,
        "appointmentStatus": "confirmed",
        "description": description,
        "ignoreDateRange": False,
        "toNotify": True,
        "ignoreFreeSlotValidation": False,
    }

    url = "https://services.leadconnectorhq.com/calendars/events/appointments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "version": "2021-04-15",
    }

    res = requests.post(url, headers=headers, json=payload)

    return func.HttpResponse(
        json.dumps({
            "response": res.text,
            "payload": payload
        }, indent=2),
        mimetype="application/json",
        status_code=res.status_code
    )
