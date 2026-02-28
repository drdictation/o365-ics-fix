#!/usr/bin/env python3
import os
import sys
import hashlib
import requests
from datetime import datetime, timezone
from dateutil import tz
from icalendar import Calendar, Event

# --- Config via env vars ---
ICS_URL = os.environ.get("OFFICE_ICS_URL", "").strip()
OUT_PATH = os.environ.get("OUT_ICS_PATH", "public/calendar_fixed.ics")
SCRUB = os.environ.get("SCRUB_DETAILS", "1").strip()  # "1" = scrub, "0" = keep details

# Windows TZID -> IANA (we'll convert to UTC anyway, but needed to interpret local times correctly)
WINDOWS_TZ_MAP = {
    "AUS Eastern Standard Time": "Australia/Sydney",
    "E. Australia Standard Time": "Australia/Brisbane",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin",
    "W. Australia Standard Time": "Australia/Perth",
}

def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)

def get_iana_tz(windows_tz: str):
    return WINDOWS_TZ_MAP.get(windows_tz)

def to_utc_dt(dt_val, tzid: str | None):
    """
    Convert DTSTART/DTEND values from ICS into UTC-aware datetime.
    Handles:
      - aware datetime
      - naive datetime + TZID (Windows or IANA)
      - date (all-day): we keep it as date (Google can handle DATE), but we can also convert to midnight UTC.
    """
    # All-day event: dt_val can be datetime.date
    if not isinstance(dt_val, datetime):
        return dt_val  # keep date as-is

    if dt_val.tzinfo is not None:
        return dt_val.astimezone(timezone.utc)

    # Naive datetime: attach timezone via TZID if supplied
    if tzid:
        # Try IANA directly
        z = tz.gettz(tzid)
        if z is None:
            # Try Windows->IANA
            mapped = get_iana_tz(tzid)
            if mapped:
                z = tz.gettz(mapped)

        if z is None:
            # Fallback: assume local Australia/Melbourne (close enough for your case)
            z = tz.gettz("Australia/Melbourne")

        aware = dt_val.replace(tzinfo=z)
        return aware.astimezone(timezone.utc)

    # No TZID: assume UTC to avoid shifting unexpectedly
    return dt_val.replace(tzinfo=timezone.utc)

def scrub_event(ev: Event):
    """
    Remove or sanitize fields so the published ICS does not leak content.
    Keep only timing + busy blocks by default.
    """
    # Keep summary but make generic; remove description/location/attendees/organizer/url etc.
    ev["SUMMARY"] = "Busy"

    for key in [
        "DESCRIPTION",
        "LOCATION",
        "ORGANIZER",
        "ATTENDEE",
        "URL",
        "CONFERENCE",
        "X-MICROSOFT-SKYPETEAMSMEETINGURL",
        "X-MICROSOFT-ONLINE-MEETING-CONFLINK",
        "X-MICROSOFT-ONLINE-MEETING-EXTERNALLINK",
    ]:
        if key in ev:
            del ev[key]

    # Some calendars use these:
    for k in list(ev.keys()):
        if str(k).upper().startswith("X-") and "CDO" not in str(k).upper():
            # keep CDO busy status but remove other X- noise
            try:
                del ev[k]
            except Exception:
                pass

def normalize_calendar(cal: Calendar) -> Calendar:
    """
    Output calendar:
      - removes VTIMEZONE blocks
      - normalizes DTSTART/DTEND to UTC with Z for datetime events
      - optionally scrubs sensitive content
    """
    out = Calendar()
    # Preserve basic headers if present
    for k in ["PRODID", "VERSION", "CALSCALE", "METHOD", "X-WR-CALNAME"]:
        if k in cal:
            out.add(k, cal[k])

    # Ensure minimal standard headers
    if "PRODID" not in out:
        out.add("PRODID", "-//o365-ics-fix//EN")
    if "VERSION" not in out:
        out.add("VERSION", "2.0")
    if "CALSCALE" not in out:
        out.add("CALSCALE", "GREGORIAN")

    for component in cal.walk():
        if component.name == "VEVENT":
            ev = Event()
            # Copy everything first
            for key, val in component.property_items():
                ev.add(key, val)

            # Determine TZID attached to DTSTART/DTEND properties (if any)
            dtstart_prop = component.get("DTSTART")
            dtend_prop = component.get("DTEND")
            tzid_start = None
            tzid_end = None

            if dtstart_prop is not None and hasattr(dtstart_prop, "params"):
                tzid_start = dtstart_prop.params.get("TZID")
            if dtend_prop is not None and hasattr(dtend_prop, "params"):
                tzid_end = dtend_prop.params.get("TZID")

            # Normalize start/end
            if dtstart_prop is not None:
                dtstart_val = dtstart_prop.dt
                dtstart_utc = to_utc_dt(dtstart_val, tzid_start)
                # Replace DTSTART cleanly
                ev.pop("DTSTART", None)
                ev.add("DTSTART", dtstart_utc)

            if dtend_prop is not None:
                dtend_val = dtend_prop.dt
                dtend_utc = to_utc_dt(dtend_val, tzid_end or tzid_start)
                ev.pop("DTEND", None)
                ev.add("DTEND", dtend_utc)

            # If SCRUB enabled, remove sensitive details
            if SCRUB == "1":
                scrub_event(ev)

            out.add_component(ev)

    return out

def main():
    if not ICS_URL:
        die("Missing OFFICE_ICS_URL env var")

    r = requests.get(ICS_URL, timeout=60)
    r.raise_for_status()

    raw = r.content
    if b"BEGIN:VCALENDAR" not in raw:
        die("Downloaded content does not look like an ICS VCALENDAR")

    cal = Calendar.from_ical(raw)
    fixed = normalize_calendar(cal)

    out_bytes = fixed.to_ical()

    # Ensure directory exists
    out_dir = os.path.dirname(OUT_PATH)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Only write if changed (prevents noisy commits)
    existing = b""
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, "rb") as f:
            existing = f.read()

    if hashlib.sha256(existing).hexdigest() == hashlib.sha256(out_bytes).hexdigest():
        print("No change in ICS output; nothing to write.")
        return

    with open(OUT_PATH, "wb") as f:
        f.write(out_bytes)

    print(f"Wrote: {OUT_PATH} ({len(out_bytes)} bytes)")

if __name__ == "__main__":
    main()
