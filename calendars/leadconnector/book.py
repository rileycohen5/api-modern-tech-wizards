import requests
from datetime import datetime, timedelta
import pytz
import azure.functions as func
import json
import logging

def parse_human_datetime(dt_str: str):
    """
    Ultra-robust parser for AI-generated datetime strings.
    Accepts:
    - Missing commas
    - Hours without minutes ("11 AM")
    - Missing colon ("11 30 AM")
    - Weird spacing
    - Full or abbreviated timezones
    """

    import re
    from datetime import datetime
    import pytz

    # Normalize commas + spaces
    s = re.sub(r"[,\s]+", " ", dt_str).strip()

    # Fix missing colon between HH and MM (e.g. "11 30 AM")
    s = re.sub(r"\b(\d{1,2}) (\d{2}) (AM|PM)\b", r"\1:\2 \3", s, flags=re.IGNORECASE)

    # Fix "11 AM" → "11:00 AM"
    s = re.sub(r"\b(\d{1,2}) (AM|PM)\b", r"\1:00 \2", s, flags=re.IGNORECASE)

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
    tz_regex = r"(Pacific Standard Time|Pacific Daylight Time|Mountain Standard Time|Mountain Daylight Time|Central Standard Time|Central Daylight Time|Eastern Standard Time|Eastern Daylight Time|PST|PDT|MST|MDT|CST|CDT|EST|EDT)$"
    tz_match = re.search(tz_regex, s, re.IGNORECASE)

    if not tz_match:
        raise ValueError(f"Could not extract timezone from: {dt_str}")

    tz_raw = tz_match.group(0).upper()

    # Determine IANA zone
    if tz_raw in full_tz_map:
        tz_name = full_tz_map[tz_raw]
    elif tz_raw in abbrev_map:
        tz_name = abbrev_map[tz_raw]
    else:
        raise ValueError(f"Unknown timezone: {tz_raw}")

    # Strip timezone from string
    s_no_tz = re.sub(tz_regex, "", s, flags=re.IGNORECASE).strip()

    # Try multiple formats
    fmts = [
        "%A %B %d %Y at %I:%M %p",
        "%A %B %d %Y %I:%M %p",
        "%A %B %d %Y at %I:%M%p",
        "%A %B %d %Y %I:%M%p",
    ]

    naive = None
    for fmt in fmts:
        try:
            naive = datetime.strptime(s_no_tz, fmt)
            break
        except:
            pass

    if naive is None:
        raise ValueError(f"Invalid datetime portion: '{s_no_tz}'")

    tz = pytz.timezone(tz_name)
    return tz.localize(naive)





def detect_calendar_timezone(calendar_id, token):
    """
    Makes a free-slots API call and returns the timezone from one slot.
    """
    now = datetime.now(pytz.utc)
    start_ms = int(now.timestamp() * 1000)
    end_ms = int((now + timedelta(days=7)).timestamp() * 1000)

    url = f"https://services.leadconnectorhq.com/calendars/{calendar_id}/free-slots"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"startDate": start_ms, "endDate": end_ms}

    res = requests.get(url, headers=headers, params=params)
    if res.status_code != 200:
        raise Exception(f"Cannot detect calendar timezone: {res.text}")

    data = res.json()
    for _, obj in data.items():
        if isinstance(obj, dict) and "slots" in obj and obj["slots"]:
            dt = datetime.fromisoformat(obj["slots"][0])
            return dt.tzinfo

    raise Exception("Calendar returned no slots to detect timezone")


def book_leadconnector_appointment(req: func.HttpRequest) -> func.HttpResponse:
    """
    Schedules an appointment in LeadConnector via POST body payload.
    """

    # Try parsing JSON body
    try:
        body = req.get_json()
    except:
        return func.HttpResponse(
            "Request body must be valid JSON.",
            status_code=400
        )

    # Extract fields from JSON
    dt_str      = body.get("proposed_datetime")
    calendar_id = body.get("calendar_id")
    lead_name   = body.get("lead_name")
    location_id = body.get("locationId")
    description = body.get("description", "")
    contact_id  = body.get("contactId")
    token       = body.get("token") or req.headers.get("Authorization")

    # Validate required fields
    if not all([dt_str, calendar_id, lead_name, location_id, contact_id, token]):
        return func.HttpResponse(
            "Missing parameters. Required: proposed_datetime, calendar_id, lead_name, locationId, contactId, token",
            status_code=400
        )

    # 1️⃣ Convert user datetime → timezone-aware
    try:
        requested_customer_dt = parse_human_datetime(dt_str)
    except Exception as e:
        return func.HttpResponse(str(e), status_code=400)

    # 2️⃣ Detect calendar timezone
    try:
        calendar_tz = detect_calendar_timezone(calendar_id, token)
    except Exception as e:
        return func.HttpResponse(str(e), status_code=500)

    # 3️⃣ Convert customer → calendar timezone
    calendar_dt = requested_customer_dt.astimezone(calendar_tz)

    # 4️⃣ Create start/end
    start_iso = calendar_dt.isoformat()
    end_iso = (calendar_dt + timedelta(minutes=30)).isoformat()

    # Build API payload
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
        "ignoreFreeSlotValidation": False
    }

    #print(payload)
    #logging.info(f"Payload being sent: {json.dumps(payload, indent=2)}")


    # Make booking request
    url = "https://services.leadconnectorhq.com/calendars/events/appointments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "version":"2021-04-15"
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


