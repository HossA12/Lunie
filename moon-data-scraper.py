#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moongiant daily scraper -> CSV

Purpose
-------
Scrape daily lunar/solar fields from Moongiant's per-day pages at:
  https://www.moongiant.com/phase/MM/DD/YYYY/

Collected fields per day:
- Phase (e.g., "Waning Gibbous")
- Illumination (%)               -> "Illumination: X%"
- Moon Age (days)                -> "Moon Age: X days"
- Moon Angle (deg)               -> "Moon Angle: X"
- Moon Distance (km)             -> "Moon Distance: X km"
- Sun Angle (deg)                -> "Sun Angle: X"
- Sun Distance (km)              -> "Sun Distance: X km"

Output CSV columns:
date,phase,illumination_pct,moon_age_days,moon_angle_deg,moon_distance_km,
sun_angle_deg,sun_distance_km,source_url

Default date range:
- The next 10 years starting from the day you run the script, inclusive.
  Example (if run on 2025-11-11): 2025-11-11 through 2035-11-10.

Usage
-----
- Install dependencies:
    pip install requests beautifulsoup4
  Optional but recommended (adds caching for repeat runs):
    pip install requests-cache

- Run with defaults (next 10 years to moongiant_moon_daily.csv):
    python scrape_moongiant.py

- Or specify start, end, and output file explicitly:
    python scrape_moongiant.py 2025-11-11 2035-11-10 my_output.csv

Ethics and site usage
---------------------
- Please use responsibly. The script throttles requests via a small delay
  and optional random jitter. Adjust with care.
- Review Moongiant’s terms before republishing or redistributing their data.

Tested with Python 3.9+.
"""

import csv
import re
import sys
import time
import random
from datetime import date, timedelta
from typing import Optional, Dict, Any

import requests
from bs4 import BeautifulSoup

# ---------------- Configuration constants ----------------

# Template for Moongiant's daily phase pages
BASE_URL = "https://www.moongiant.com/phase/{m}/{d}/{y}/"

# Default output file name if none is provided via CLI
OUT_CSV_DEFAULT = "moongiant_moon_daily.csv"

# Politeness and reliability settings
DELAY_SECONDS = 1.2           # Base delay between requests to avoid hammering the site
JITTER_SECONDS = 0.3          # Small random jitter added to the base delay
MAX_RETRIES = 3               # Number of times to retry a page on transient failures
TIMEOUT = 20                  # Per-request timeout in seconds
USER_AGENT = "Mozilla/5.0 (compatible; moon-data-collector/1.0; +personal-use)"

# Canonical set of phase names (used for normalization)
PHASE_NAMES = {
    "New Moon", "Full Moon", "First Quarter", "Last Quarter",
    "Waxing Crescent", "Waxing Gibbous", "Waning Gibbous", "Waning Crescent",
}


# ---------------- Helper functions ----------------

def to_float_num(s: Optional[str]) -> Optional[float]:
    """
    Convert a numeric string to float, safely.

    Behavior:
    - Strips whitespace and thousands separators (commas).
    - Returns None if the input is None or cannot be parsed.

    Examples:
    - "1,234.56" -> 1234.56
    - "  0.51 "  -> 0.51
    - None       -> None
    - "abc"      -> None

    Parameters
    ----------
    s : Optional[str]
        The numeric string to convert (may be None).

    Returns
    -------
    Optional[float]
        The parsed float value, or None if parsing fails.
    """
    if s is None:
        return None
    s = s.strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def extract_first(patterns, text, flags=re.IGNORECASE | re.DOTALL) -> Optional[str]:
    """
    Search the provided text with a list of regex patterns and return the first match group.

    Why this exists:
    - Moongiant's pages are mostly consistent, but small differences occur. By trying
      a list of alternative patterns we make parsing more robust.

    Parameters
    ----------
    patterns : list[str] | tuple[str, ...]
        A list/tuple of regular expression patterns. Each pattern must have at least
        one capturing group; the first group's text is returned when matched.
    text : str
        The text to search in (usually the entire page text).
    flags : int, optional
        Regex flags (defaults to IGNORECASE | DOTALL).

    Returns
    -------
    Optional[str]
        The first capturing group's content if any pattern matches; otherwise None.
    """
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return m.group(1).strip()
    return None


def normalize_phase(name: Optional[str]) -> Optional[str]:
    """
    Normalize a lunar phase name to a canonical form.

    - Title-cases words.
    - Maps common variants (e.g., 'last quarter' -> 'Last Quarter').
    - Returns None if input is None.

    Parameters
    ----------
    name : Optional[str]
        The raw phase name extracted from the page.

    Returns
    -------
    Optional[str]
        A normalized phase name (e.g., 'Waning Crescent') or None.
    """
    if not name:
        return None

    norm = " ".join(w.capitalize() for w in name.split())

    # Map common textual variants to our canonical set
    aliases = {
        "Last quarter": "Last Quarter",
        "First quarter": "First Quarter",
        "New moon": "New Moon",
        "Full moon": "Full Moon",
        "Waxing crescent": "Waxing Crescent",
        "Waning crescent": "Waning Crescent",
        "Waxing gibbous": "Waxing Gibbous",
        "Waning gibbous": "Waning Gibbous",
    }
    norm = aliases.get(norm, norm)
    return norm


def parse_page(html_text: str) -> Dict[str, Any]:
    """
    Parse all required fields from a single Moongiant daily "phase" page.

    Data source layout (observed):
    - Near the top, a narrative sentence like:
        "On this day the Moon will be in a Waning Crescent phase and has an illumination of 11%."
    - A "Phase Details" panel containing key/value lines:
        Phase: Waning Crescent
        Illumination: 11%
        Moon Age: 26.42 days
        Moon Angle: 0.53
        Moon Distance: 373,394.23 km
        Sun Angle: 0.53
        Sun Distance: 149,282,909.93 km

    Parsing approach:
    - Convert the entire HTML into plain text and apply regexes against it.
    - First try the Phase Details block for each field.
    - Provide fallbacks from the narrative (for Phase and Illumination) if needed.
    - Normalize the Phase string to canonical names.

    Parameters
    ----------
    html_text : str
        The raw HTML content of a daily page.

    Returns
    -------
    Dict[str, Any]
        Dictionary with keys:
        - "phase"                : str (may be empty if not found)
        - "illumination_pct"     : float | None
        - "moon_age_days"        : float | None
        - "moon_angle_deg"       : float | None
        - "moon_distance_km"     : float | None
        - "sun_angle_deg"        : float | None
        - "sun_distance_km"      : float | None
    """
    soup = BeautifulSoup(html_text, "html.parser")
    # We flatten all text to simplify locating fields without depending on HTML structure
    page_text = soup.get_text("\n", strip=True)

    # Attempt to parse from the "Phase Details" block first
    illumination = extract_first([r"Illumination:\s*([\d.]+)\s*%"], page_text)
    moon_age = extract_first([r"Moon Age:\s*([\d.]+)\s*days"], page_text)
    moon_angle = extract_first([r"Moon Angle:\s*([\d.]+)"], page_text)
    moon_dist = extract_first([r"Moon Distance:\s*([\d,]+(?:\.\d+)?)\s*km"], page_text)
    sun_angle = extract_first([r"Sun Angle:\s*([\d.]+)"], page_text)
    sun_dist = extract_first([r"Sun Distance:\s*([\d,]+(?:\.\d+)?)\s*km"], page_text)

    # Phase may sometimes be omitted in the "Phase Details" block
    phase = extract_first([r"Phase:\s*([A-Za-z ]{3,})\s*(?:\n|$)"], page_text)

    # Fallbacks from narrative sentences when Phase or Illumination are missing
    if not phase or phase.lower() in {"phase:", "phase"}:
        phase_alt = extract_first([
            r"Moon (?:will be|was) in a ([A-Za-z ]+?) phase",
            r"The ([A-Za-z ]+?) on [A-Za-z]+\s+\d{1,2}\s+has an illumination",
        ], page_text)
        if phase_alt:
            phase = phase_alt

    if not illumination:
        illumination = extract_first([
            r"has an illumination of\s*([\d.]+)\s*%",
        ], page_text)

    # Normalize Phase to canonical forms
    phase = normalize_phase(phase)

    return {
        "phase": phase or "",
        "illumination_pct": to_float_num(illumination),
        "moon_age_days": to_float_num(moon_age),
        "moon_angle_deg": to_float_num(moon_angle),
        "moon_distance_km": to_float_num(moon_dist),
        "sun_angle_deg": to_float_num(sun_angle),
        "sun_distance_km": to_float_num(sun_dist),
    }


def fetch(url: str, session: requests.Session) -> Optional[str]:
    """
    Fetch a URL with retries and simple backoff, returning the HTML text on success.

    Behavior:
    - Uses a shared requests.Session for connection pooling.
    - Sets a custom User-Agent.
    - Retries transient HTTP errors (429, 503) with incremental backoff.
    - Returns None if all attempts fail.

    Parameters
    ----------
    url : str
        The page URL to fetch.
    session : requests.Session
        A session instance to reuse connections across requests.

    Returns
    -------
    Optional[str]
        The response text (HTML) if status 200 is received; otherwise None.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if r.status_code == 200 and r.text:
                return r.text
            elif r.status_code in (429, 503):
                # Rate-limited or temporarily unavailable: exponential-ish backoff
                sleep_for = (DELAY_SECONDS + random.uniform(0, JITTER_SECONDS)) * attempt
                time.sleep(sleep_for)
            else:
                # Other errors: brief delay then retry
                time.sleep(1.0 * attempt)
        except requests.RequestException:
            # Network-level issues: brief delay then retry
            time.sleep(1.0 * attempt)
    return None


def daterange(d0: date, d1_inclusive: date):
    """
    Generate each date from start to end, inclusive.

    Parameters
    ----------
    d0 : date
        Start date (inclusive).
    d1_inclusive : date
        End date (inclusive).

    Yields
    ------
    date
        Each date from d0 through d1_inclusive, one day at a time.
    """
    d = d0
    while d <= d1_inclusive:
        yield d
        d = d + timedelta(days=1)


def parse_iso_date(s: str) -> date:
    """
    Parse an ISO date string (YYYY-MM-DD) to a datetime.date.

    Parameters
    ----------
    s : str
        The input date string in ISO format.

    Returns
    -------
    date
        The parsed date.

    Raises
    ------
    ValueError
        If the input cannot be parsed.
    """
    return date.fromisoformat(s)


def add_years(d: date, years: int) -> date:
    """
    Safely add whole years to a date.

    Rationale:
    - date.replace(year=d.year+N) raises ValueError on Feb 29 in non-leap years.
    - This helper returns Feb 28 in such cases to keep the date in the same month.

    Parameters
    ----------
    d : date
        The original date.
    years : int
        Number of years to add (may be negative).

    Returns
    -------
    date
        The resulting date, clamped to Feb 28 if the target year lacks Feb 29.
    """
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Handle February 29 -> February 28 fallback
        return d.replace(month=2, day=28, year=d.year + years)


# ---------------- Main workflow functions ----------------

def run(start_date: date, end_date: date, out_csv_path: str):
    """
    Orchestrate the scraping process and write the output CSV.

    Steps:
    1) Validate date range.
    2) Initialize a requests.Session (and optional requests-cache).
    3) Open the CSV and write a header row.
    4) For each day in [start_date, end_date]:
        - Build the Moongiant daily URL.
        - Fetch and parse the page.
        - Write a row with parsed values (or a stub if fetch failed).
        - Sleep a polite amount of time between requests.

    Parameters
    ----------
    start_date : date
        Start date (inclusive).
    end_date : date
        End date (inclusive).
    out_csv_path : str
        Output CSV file path.

    Side effects
    ------------
    - Creates/overwrites the CSV file at out_csv_path.
    - Prints simple progress updates to stdout.
    """
    if end_date < start_date:
        raise SystemExit("END_DATE must be on/after START_DATE")

    total = (end_date - start_date).days + 1
    session = requests.Session()

    # Optional: enable caching if available (helpful during development / retries)
    try:
        import requests_cache  # type: ignore
        requests_cache.install_cache("moongiant_cache", expire_after=7 * 24 * 3600)
        cached = True
    except Exception:
        cached = False

    print(f"Scraping {total} days from {start_date.isoformat()} to {end_date.isoformat()}...")
    if cached:
        print("requests-cache: enabled")

    # Open output CSV and write header
    with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date",
            "phase",
            "illumination_pct",
            "moon_age_days",
            "moon_angle_deg",
            "moon_distance_km",
            "sun_angle_deg",
            "sun_distance_km",
            "source_url",
        ])

        done = 0

        # Iterate day by day
        for d in daterange(start_date, end_date):
            url = BASE_URL.format(m=d.month, d=d.day, y=d.year)

            # Fetch page HTML (with retries/backoff)
            html = fetch(url, session)

            if not html:
                # If we couldn't fetch after retries, record the date and URL
                # with empty fields so you can re-run for just those later.
                writer.writerow([d.isoformat(), "", "", "", "", "", "", "", url])
            else:
                # Parse the HTML into our structured fields
                data = parse_page(html)

                # Write the parsed data row to CSV
                writer.writerow([
                    d.isoformat(),
                    data["phase"],
                    data["illumination_pct"],
                    data["moon_age_days"],
                    data["moon_angle_deg"],
                    data["moon_distance_km"],
                    data["sun_angle_deg"],
                    data["sun_distance_km"],
                    url,
                ])

            done += 1
            if done % 50 == 0 or done == total:
                print(f"{done}/{total} days scraped...")

            # Polite delay between requests to reduce server load
            time.sleep(DELAY_SECONDS + random.uniform(0, JITTER_SECONDS))

    print(f"Saved -> {out_csv_path}")


def main():
    """
    Command-line entry point.

    CLI Arguments (all optional)
    ----------------------------
    1) START_YYYY-MM-DD : Start date (inclusive). Defaults to "today".
    2) END_YYYY-MM-DD   : End date (inclusive). Defaults to start + 10 years - 1 day.
    3) OUT_CSV          : Output CSV file path. Defaults to "moongiant_moon_daily.csv".

    Examples
    --------
    - Default 10-year window starting today:
        python scrape_moongiant.py

    - Explicit range and output path:
        python scrape_moongiant.py 2025-11-11 2035-11-10 my_output.csv
    """
    today = date.today()

    # Default end date is 10 years minus one day (inclusive window of 10 years)
    default_start = today
    default_end = add_years(today, 10) - timedelta(days=1)

    # Parse CLI args: 0, 1, 2, or 3 arguments are accepted
    args = sys.argv[1:]
    if len(args) not in (0, 1, 2, 3):
        print("Usage: python scrape_moongiant.py [START_YYYY-MM-DD] [END_YYYY-MM-DD] [OUT_CSV]")
        sys.exit(1)

    try:
        # Start date: provided or default to today
        if len(args) >= 1 and args[0]:
            start = parse_iso_date(args[0])
        else:
            start = default_start

        # End date: provided or default to start + 10 years - 1 day
        if len(args) >= 2 and args[1]:
            end = parse_iso_date(args[1])
        else:
            end = add_years(start, 10) - timedelta(days=1)

        # Output CSV: provided or default name
        out_csv = args[2] if len(args) >= 3 and args[2] else OUT_CSV_DEFAULT

    except ValueError as e:
        print(f"Date parse error: {e}")
        print("Please use ISO format YYYY-MM-DD.")
        sys.exit(1)

    run(start, end, out_csv)


# Standard "script" guard
if __name__ == "__main__":
    main()