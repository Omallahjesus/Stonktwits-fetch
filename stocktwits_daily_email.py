import html
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests

STOCKTWITS_API = "https://api.stocktwits.com/api/2"
STOCKTWITS_ACCESS_TOKEN = os.getenv("STOCKTWITS_ACCESS_TOKEN")  # optional
TARGET_USER = "chartistmind"
WINDOW_HOURS = 24
ET = ZoneInfo("America/New_York")

GMAIL_USERNAME = os.getenv("GMAIL_USERNAME")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL") or GMAIL_USERNAME


def is_summary_time():
    """Workflow fires at 02:00 and 03:00 UTC to cover EDT and EST;
    only proceed when it is actually 10 PM in New York."""
    now_et = datetime.now(ET)
    if now_et.hour != 22:
        print(f"It is {now_et.strftime('%H:%M')} in New York - not 10 PM ET. Skipping.")
        return False
    return True


def parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_user_messages(user, window_hours=WINDOW_HOURS, max_pages=20):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    collected = []
    max_id = None

    for _ in range(max_pages):
        params = {"limit": 30}
        if STOCKTWITS_ACCESS_TOKEN:
            params["access_token"] = STOCKTWITS_ACCESS_TOKEN
        if max_id:
            params["max"] = max_id

        res = requests.get(f"{STOCKTWITS_API}/streams/user/{user}.json", params=params, timeout=30)
        res.raise_for_status()
        data = res.json()
        batch = data.get("messages", [])
        if not batch:
            break
        collected.extend(batch)

        oldest_ts = parse_ts(batch[-1].get("created_at"))
        max_id = data.get("cursor", {}).get("max")
        if not max_id or not data.get("more") or (oldest_ts and oldest_ts < cutoff):
            break

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return [m for m in collected if (parse_ts(m.get("created_at")) or epoch) >= cutoff]


def extract_symbols(msg):
    symbols = []
    for s in msg.get("symbols", []):
        if isinstance(s, str):
            symbols.append(s)
        elif isinstance(s, dict) and s.get("symbol"):
            symbols.append(s["symbol"])
    if not symbols:
        symbols = re.findall(r"\$([A-Za-z][A-Za-z0-9.]*)", msg.get("body", ""))
    return symbols


def format_email(messages):
    now_et = datetime.now(ET)
    subject = f"StockTwits Daily Summary - @{TARGET_USER} - {now_et.strftime('%b %d, %Y')}"
    lines = [
        "StockTwits Daily Summary",
        f"User: @{TARGET_USER}",
        f"Window: past {WINDOW_HOURS} hours, as of {now_et.strftime('%Y-%m-%d %I:%M %p ET')}",
        f"Messages found: {len(messages)} (newest first)",
        "",
    ]
    if not messages:
        lines.append("No messages from this user in the past 24 hours.")
    for i, m in enumerate(messages, 1):
        ts = parse_ts(m.get("created_at"))
        ts_et = ts.astimezone(ET).strftime("%I:%M %p ET") if ts else "unknown time"
        syms = ", ".join(f"${s}" for s in extract_symbols(m)) or "(none)"
        body = html.unescape(m.get("body", "")).strip()
        lines.append(f"--- {i}. {ts_et} | {syms} ---")
        lines.append(body)
        lines.append("")
    return subject, "\n".join(lines)


def send_email(subject, body):
    if not GMAIL_USERNAME or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_USERNAME and GMAIL_APP_PASSWORD secrets are not set.")
        print("Add them under Settings -> Secrets and variables -> Actions, then re-run.")
        sys.exit(1)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USERNAME
    msg["To"] = TO_EMAIL
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(GMAIL_USERNAME, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USERNAME, [TO_EMAIL], msg.as_string())
    print(f"Email sent to {TO_EMAIL}")


def main():
    if os.getenv("FORCE_RUN") != "1" and not is_summary_time():
        return
    print(f"Fetching @{TARGET_USER} messages from the past {WINDOW_HOURS} hours...")
    try:
        messages = fetch_user_messages(TARGET_USER)
    except requests.HTTPError as e:
        print(f"StockTwits API error: {e}")
        sys.exit(1)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    messages.sort(key=lambda m: parse_ts(m.get("created_at")) or epoch, reverse=True)
    print(f"Found {len(messages)} messages in the window.")
    subject, body = format_email(messages)
    print(f"Subject: {subject}")
    send_email(subject, body)
    print("Done.")


if __name__ == "__main__":
    main()
