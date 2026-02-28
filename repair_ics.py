"""
repair_ics.py — Fix Microsoft-exported ICS for Google Calendar compatibility.

Problems this script fixes:
1. Non-IANA TZIDs: "AUS Eastern Standard Time" → "Australia/Melbourne"
                   "Eastern Standard Time"     → "America/New_York"
                   (add more mappings below as needed)
2. Nested VCALENDAR blocks (some exporters produce these)
3. Missing PRODID / VERSION (Google requires them)
4. Generates clean VTIMEZONE blocks with IANA names
5. Preserves all-day events (VALUE=DATE) without timezone mangling
6. CRLF line endings (RFC 5545 requires them)

Input:  calendar.ics        (repo root)
Output: public/calendar.ics (for GitHub Pages deployment)
"""

import os
import re

# ─── Configuration ────────────────────────────────────────────────────────────

SRC = "calendar.ics"
DST_DIR = "public"
DST = os.path.join(DST_DIR, "calendar.ics")

# Microsoft Windows timezone name → IANA timezone name
# Add more mappings here if you encounter other Microsoft TZIDs
TZID_MAP = {
    "AUS Eastern Standard Time": "Australia/Melbourne",
    "Eastern Standard Time": "America/New_York",
    "Pacific Standard Time": "America/Los_Angeles",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Tokyo Standard Time": "Asia/Tokyo",
    "China Standard Time": "Asia/Shanghai",
    "India Standard Time": "Asia/Kolkata",
    "New Zealand Standard Time": "Pacific/Auckland",
    "E. Australia Standard Time": "Australia/Brisbane",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "W. Australia Standard Time": "Australia/Perth",
    "Tasmania Standard Time": "Australia/Hobart",
}

# IANA VTIMEZONE definitions for the timezones we actually use.
# These are correct, standards-compliant blocks that Google/Apple understand.
VTIMEZONE_BLOCKS = {
    "Australia/Melbourne": """\
BEGIN:VTIMEZONE
TZID:Australia/Melbourne
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+1100
TZOFFSETTO:+1000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=4
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+1000
TZOFFSETTO:+1100
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=10
END:DAYLIGHT
END:VTIMEZONE""",

    "America/New_York": """\
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:16010101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE""",
}


def repair_ics(src: str, dst: str) -> None:
    """Read a Microsoft-exported ICS, fix it, write Google-compatible output."""

    if not os.path.exists(src):
        raise FileNotFoundError(
            f"❌ {src} not found! Make sure it's committed to the repo root.\n"
            f"   Files in cwd: {os.listdir('.')}"
        )

    with open(src, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # ── Step 1: Normalise line endings to \n for processing ──────────────
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    # ── Step 2: Remove nested VCALENDAR blocks ───────────────────────────
    # Some exporters wrap the whole thing in an extra BEGIN:VCALENDAR
    # Keep only the outermost one
    inner = content
    while inner.count("BEGIN:VCALENDAR") > 1:
        # Remove the innermost BEGIN/END VCALENDAR pair
        inner = re.sub(
            r"BEGIN:VCALENDAR\n(?!.*BEGIN:VCALENDAR)",
            "",
            inner,
            count=1,
        )
        inner = re.sub(
            r"END:VCALENDAR\n(?=.*END:VCALENDAR)",
            "",
            inner,
            count=1,
            flags=re.DOTALL,
        )
    content = inner

    # ── Step 3: Replace all Microsoft TZIDs with IANA equivalents ────────
    for ms_tz, iana_tz in TZID_MAP.items():
        # Replace in TZID property of VTIMEZONE definitions
        content = content.replace(f"TZID:{ms_tz}", f"TZID:{iana_tz}")
        # Replace in DTSTART;TZID=..., DTEND;TZID=..., etc.
        content = content.replace(f"TZID={ms_tz}", f"TZID={iana_tz}")

    # ── Step 4: Remove old VTIMEZONE blocks, we'll add clean ones ────────
    # Remove all existing VTIMEZONE blocks
    content = re.sub(
        r"BEGIN:VTIMEZONE\n.*?END:VTIMEZONE\n?",
        "",
        content,
        flags=re.DOTALL,
    )

    # ── Step 5: Determine which IANA timezones are actually used ─────────
    used_tzids = set()
    for match in re.finditer(r"TZID=([^;:\n]+)", content):
        tz = match.group(1)
        if tz in VTIMEZONE_BLOCKS:
            used_tzids.add(tz)

    # ── Step 6: Rebuild the file with clean structure ────────────────────
    lines = content.split("\n")

    output_lines = []
    header_done = False
    for line in lines:
        stripped = line.strip()
        if not header_done and stripped == "BEGIN:VCALENDAR":
            output_lines.append("BEGIN:VCALENDAR")
            # Ensure required properties exist right after BEGIN:VCALENDAR
            output_lines.append("VERSION:2.0")
            output_lines.append("PRODID:-//Calendar Sync Pipeline//EN")
            output_lines.append("METHOD:PUBLISH")
            output_lines.append("X-WR-CALNAME:Calendar")
            # Insert clean VTIMEZONE blocks
            for tz in sorted(used_tzids):
                for tz_line in VTIMEZONE_BLOCKS[tz].split("\n"):
                    output_lines.append(tz_line)
            header_done = True
            continue

        # Skip old header properties (we already wrote them)
        if header_done and stripped in (
            "VERSION:2.0",
            "METHOD:PUBLISH",
            "X-WR-CALNAME:Calendar",
        ):
            continue
        if stripped.startswith("PRODID:"):
            continue

        # Skip empty lines (ICS shouldn't have them)
        if stripped == "" and not line.startswith(" "):
            continue

        output_lines.append(line)

    # ── Step 7: Write output with CRLF line endings (RFC 5545) ───────────
    os.makedirs(DST_DIR, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(output_lines))

    # ── Stats ────────────────────────────────────────────────────────────
    event_count = sum(1 for l in output_lines if l.strip() == "BEGIN:VEVENT")
    tz_count = len(used_tzids)
    print(f"✅ ICS repaired successfully!")
    print(f"   Events: {event_count}")
    print(f"   Timezones: {tz_count} ({', '.join(sorted(used_tzids)) or 'none (all-day only)'})")
    print(f"   Output: {dst}")
    for ms_tz, iana_tz in TZID_MAP.items():
        if iana_tz in used_tzids:
            print(f"   Mapped: {ms_tz} → {iana_tz}")


if __name__ == "__main__":
    repair_ics(SRC, DST)
