import html
import os
import re
import smtplib
import ssl
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests

STOCKTWITS_API = "https://api.stocktwits.com/api/2"
STOCKTWITS_ACCESS_TOKEN = os.getenv("STOCKTWITS_ACCESS_TOKEN")  # optional
TARGET_USER = "chartistmind"
WINDOW_HOURS = 4  # matches the every-4-hours schedule (6x/day)
ET = ZoneInfo("America/New_York")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

GMAIL_USERNAME = os.getenv("GMAIL_USERNAME")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL") or GMAIL_USERNAME

CHIP_STYLE = (
    "display:inline-block;background:#e8f0fe;color:#1a56db;font-weight:bold;"
    "padding:2px 8px;margin:0 4px 4px 0;border-radius:12px;font-size:12px;"
)


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

        res = requests.get(
            f"{STOCKTWITS_API}/streams/user/{user}.json",
            params=params,
            headers=HEADERS,
            timeout=30,
        )
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


def format_plain(messages, now_et):
    lines = [
        "StockTwits Summary",
        f"User: @{TARGET_USER}",
        f"Window: past {WINDOW_HOURS} hours, as of {now_et.strftime('%Y-%m-%d %I:%M %p ET')}",
        f"Messages found: {len(messages)} (newest first)",
        "",
    ]
    if not messages:
        lines.append(f"No messages from this user in the past {WINDOW_HOURS} hours.")
    for i, m in enumerate(messages, 1):
        ts = parse_ts(m.get("created_at"))
        ts_et = ts.astimezone(ET).strftime("%I:%M %p ET") if ts else "unknown time"
        syms = ", ".join(f"${s}" for s in extract_symbols(m)) or "(none)"
        body = html.unescape(m.get("body", "")).strip()
        lines.append(f"--- {i}. {ts_et} | {syms} ---")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def format_html(messages, now_et):
    """Fully inline-styled HTML (no <style> block) so it renders as true
    HTML in every client, including ones that strip embedded stylesheets."""
    cnt = Counter(s for m in messages for s in extract_symbols(m))
    top = "".join(
        f'<span style="{CHIP_STYLE}">${html.escape(s)} &times;{n}</span>'
        for s, n in cnt.most_common(8)
    )
    rows = []
    if not messages:
        rows.append(
            '<div style="padding:12px 20px;"><div style="font-size:14px;color:#111827;">'
            f"No messages from this user in the past {WINDOW_HOURS} hours.</div></div>"
        )
    for i, m in enumerate(messages, 1):
        ts = parse_ts(m.get("created_at"))
        ts_et = ts.astimezone(ET).strftime("%I:%M %p ET") if ts else "unknown"
        syms = extract_symbols(m)
        chips = " ".join(
            f'<span style="{CHIP_STYLE}">${html.escape(s)}</span>' for s in syms
        ) or '<span style="color:#9ca3af;font-size:12px;">(no symbols)</span>'
        body = html.escape(html.unescape(m.get("body", "")).strip()).replace("\n", "<br>")
        rows.append(
            '<div style="padding:12px 20px;border-bottom:1px solid #eeeeee;">'
            f'<div style="font-size:12px;color:#6b7280;margin-bottom:6px;">#{i} &middot; {ts_et}</div>'
            f'<div style="margin-bottom:6px;">{chips}</div>'
            f'<div style="font-size:14px;line-height:1.55;color:#111827;">{body}</div></div>'
        )
    return (
        '<html><body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">'
        '<div style="max-width:640px;margin:0 auto;background:#ffffff;">'
        '<div style="background:#0f172a;color:#ffffff;padding:20px 24px;">'
        '<div style="font-size:20px;font-weight:bold;">StockTwits Summary</div>'
        f'<div style="font-size:13px;color:#94a3b8;margin-top:4px;">@{TARGET_USER} &middot; past {WINDOW_HOURS} hours &middot; as of {now_et.strftime("%I:%M %p ET")}</div>'
        f'<div style="font-size:13px;color:#94a3b8;margin-top:2px;">{len(messages)} messages &middot; newest first</div>'
        f'<div style="margin-top:10px;">{top}</div></div>'
        + "".join(rows)
        + '<div style="padding:14px;font-size:11px;color:#9ca3af;text-align:center;">Automated by GitHub Actions &middot; every 4 hours</div>'
          "</div></body></html>"
    )


def format_email(messages):
    now_et = datetime.now(ET)
    subject = f"StockTwits Summary - @{TARGET_USER} - {now_et.strftime('%b %d, %Y %I:%M %p ET')}"
    return subject, format_plain(messages, now_et), format_html(messages, now_et)


def send_email(subject, text_body, html_body):
    if not GMAIL_USERNAME or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_USERNAME and GMAIL_APP_PASSWORD secrets are not set.")
        print("Add them under Settings -> Secrets and variables -> Actions, then re-run.")
        sys.exit(1)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USERNAME
    msg["To"] = TO_EMAIL
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(GMAIL_USERNAME, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USERNAME, [TO_EMAIL], msg.as_string())
    print(f"Email sent to {TO_EMAIL}")


def main():
    print(f"Fetching @{TARGET_USER} messages from the past {WINDOW_HOURS} hours...")
    try:
        messages = fetch_user_messages(TARGET_USER)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        print(f"StockTwits API error: {e}")
        if status in (401, 403):
            if STOCKTWITS_ACCESS_TOKEN:
                print("The STOCKTWITS_ACCESS_TOKEN secret was rejected. Generate a fresh token and update the secret.")
            else:
                print("StockTwits blocked the unauthenticated request from this runner.")
                print("Fix: create a free app at https://api.stocktwits.com/developers, copy its access token,")
                print("add it as the STOCKTWITS_ACCESS_TOKEN repository secret, then re-run this workflow.")
        sys.exit(1)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    messages.sort(key=lambda m: parse_ts(m.get("created_at")) or epoch, reverse=True)
    print(f"Found {len(messages)} messages in the window.")
    subject, text_body, html_body = format_email(messages)
    print(f"Subject: {subject}")
    send_email(subject, text_body, html_body)
    print("Done.")


if __name__ == "__main__":
    main()
