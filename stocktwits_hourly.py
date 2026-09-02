#!/usr/bin/env python3
"""
StockTwits hourly summary for a tracked user -> Gmail.
Fetches new messages since last run, classifies into Position Actions vs
Commentary, pulls price-at-post and price-now via yfinance, builds a
color-coded HTML email (tight padding, fit-to-content columns), and sends
via Gmail SMTP. Skips sending entirely if there are no new messages.
"""

import os
import re
import json
import smtplib
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
import yfinance as yf

# ---------- CONFIG ----------
TRACKED_USER = "chartistmind"
STATE_FILE = "last_seen_id.json"
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", GMAIL_USER)
ET = dt.timezone(dt.timedelta(hours=-4))  # switch to -5 outside US daylight saving

POSITION_KEYWORDS = [
    "starter", "startee", "added", "all-in", "swapped", "sold puts",
    "sold calls", "stole", "back in", "banked", "adding", "added back",
    "bought", "sold my", "closed", "took profit", "starting"
]

# ---------- STATE ----------
def load_last_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f).get("last_seen_id")
    return None

def save_last_seen(msg_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_seen_id": msg_id}, f)

# ---------- STOCKTWITS ----------
def fetch_new_messages(username, since_id):
    url = f"https://api.stocktwits.com/api/2/streams/user/{username}.json"
    params = {}
    if since_id:
        params["since"] = since_id
    r = requests.get(url, params=params, timeout=15,
                      headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    return data.get("messages", [])

def extract_symbols(msg):
    syms = [s["symbol"] for s in msg.get("symbols", [])]
    if not syms:
        syms = re.findall(r"\$([A-Za-z\.]{1,10})", msg.get("body", ""))
    return list(dict.fromkeys(syms))  # de-dupe, keep order

def is_position_action(text):
    t = text.lower()
    return any(k in t for k in POSITION_KEYWORDS)

# ---------- PRICING ----------
_price_cache = {}

def get_current_price(symbol):
    if symbol in _price_cache:
        return _price_cache[symbol]
    try:
        t = yf.Ticker(symbol)
        px = t.fast_info.get("lastPrice") or t.history(period="1d")["Close"].iloc[-1]
        _price_cache[symbol] = float(px)
        return float(px)
    except Exception:
        return None

def get_price_at_time(symbol, when_utc):
    """Best-effort 1-minute-bar lookup for the given UTC datetime (only works
    for the last ~7 days per yfinance limits, which is fine for an hourly job)."""
    try:
        t = yf.Ticker(symbol)
        start = when_utc - dt.timedelta(minutes=10)
        end = when_utc + dt.timedelta(minutes=10)
        hist = t.history(start=start, end=end, interval="1m")
        if hist.empty:
            return None
        hist.index = hist.index.tz_convert("UTC")
        idx = (hist.index - when_utc).abs().argmin()
        return float(hist["Close"].iloc[idx])
    except Exception:
        return None

# ---------- CLASSIFICATION ----------
def classify(messages):
    positions, commentary = [], []
    for m in messages:
        body = m.get("body", "")
        syms = extract_symbols(m)
        created = dt.datetime.strptime(m["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        entry = {
            "id": m["id"], "created_utc": created, "body": body, "symbols": syms,
            "likes": m.get("likes", {}).get("total", 0) if isinstance(m.get("likes"), dict) else m.get("likes", 0),
            "replies": len(m.get("conversation", {}).get("replies", [])) if isinstance(m.get("conversation"), dict) else 0,
            "reshares": m.get("reshare_message", {}).get("reshared_count", 0) if isinstance(m.get("reshare_message"), dict) else 0,
        }
        if syms and is_position_action(body):
            positions.append(entry)
        else:
            commentary.append(entry)
    return positions, commentary

# ---------- HTML BUILD (tight padding, fit-to-content) ----------
GREEN, RED = "#1a7f37", "#c0392b"
GREEN_BG, RED_BG = "#e6f4ea", "#fdeceb"
TD = 'style="padding:3px 6px;border:1px solid #ddd;white-space:nowrap;"'
TH = 'style="padding:3px 6px;border:1px solid #ddd;white-space:nowrap;background:#f0f0f0;"'

def build_position_table(positions):
    rows = []
    for p in positions:
        sym = p["symbols"][0] if p["symbols"] else "?"
        post_px = get_price_at_time(sym, p["created_utc"])
        now_px = get_current_price(sym)
        if post_px is None or now_px is None:
            delta_cell = '<td style="padding:3px 6px;border:1px solid #ddd;">n/a</td>' * 2
        else:
            delta = now_px - post_px
            pct = delta / post_px * 100
            color, bg = (GREEN, GREEN_BG) if delta >= 0 else (RED, RED_BG)
            arrow = "&#9650;" if delta >= 0 else "&#9660;"
            delta_cell = (
                f'<td style="padding:3px 6px;border:1px solid #ddd;text-align:right;'
                f'color:{color};background:{bg};font-weight:bold;white-space:nowrap;">{arrow} {delta:+.2f}</td>'
                f'<td style="padding:3px 6px;border:1px solid #ddd;text-align:right;'
                f'color:{color};background:{bg};font-weight:bold;white-space:nowrap;">{arrow} {pct:+.2f}%</td>'
            )
        et_time = p["created_utc"].astimezone(ET).strftime("%I:%M %p")
        post_px_str = f"${post_px:.2f}" if post_px else "n/a"
        now_px_str = f"${now_px:.2f}" if now_px else "n/a"
        rows.append(
            f'<tr><td {TD}>{et_time}</td>'
            f'<td {TD} style="font-weight:bold;">${sym}</td>'
            f'<td {TD}>{p["body"][:60]}</td>'
            f'<td {TD} style="text-align:right;">{post_px_str}</td>'
            f'<td {TD} style="text-align:right;">{now_px_str}</td>'
            f'{delta_cell}</tr>'
        )
    if not rows:
        return "<p>No position actions this hour.</p>"
    return (
        '<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;">'
        f'<thead><tr><th {TH}>Time (ET)</th><th {TH}>Symbol</th><th {TH}>Action</th>'
        f'<th {TH}>Price @ Post</th><th {TH}>Price Now</th><th {TH}>&Delta; $</th><th {TH}>&Delta; %</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )

def build_commentary_table(commentary):
    scored = []
    for c in commentary:
        score = c["likes"] + 2 * c["replies"] + 3 * c["reshares"]
        scored.append((score, c))
    scored.sort(key=lambda x: (-x[0], -(x[1]["created_utc"].timestamp())))
    rows = []
    for i, (score, c) in enumerate(scored, 1):
        et_time = c["created_utc"].astimezone(ET).strftime("%I:%M %p")
        sym_str = " ".join(f"${s}" for s in c["symbols"]) or "&mdash;"
        bg = "#fff8e1" if score >= 6 else "#ffffff"
        rows.append(
            f'<tr style="background:{bg};"><td {TD}>{i}</td><td {TD}>{et_time}</td>'
            f'<td {TD} style="font-weight:bold;">{sym_str}</td>'
            f'<td {TD} style="text-align:center;">L{c["likes"]} R{c["replies"]} RS{c["reshares"]}</td>'
            f'<td {TD} style="white-space:normal;max-width:480px;">{c["body"]}</td></tr>'
        )
    if not rows:
        return "<p>No commentary this hour.</p>"
    return (
        '<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:12.5px;">'
        f'<thead><tr><th {TH}>#</th><th {TH}>Time</th><th {TH}>Symbol(s)</th>'
        f'<th {TH}>Engagement</th><th {TH} style="text-align:left;">What He Said</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )

def build_email_html(positions, commentary):
    return f"""
    <div style="font-family:Arial,sans-serif;">
    <h2>StockTwits Hourly Update &mdash; @{TRACKED_USER}</h2>
    <p style="color:#555;">{dt.datetime.now(ET).strftime('%b %d, %Y %I:%M %p ET')}</p>
    <h3>Table 1: Position Actions</h3>
    {build_position_table(positions)}
    <h3>Table 2: Commentary &amp; Stance (Most to Least Significant)</h3>
    {build_commentary_table(commentary)}
    </div>
    """

# ---------- EMAIL ----------
def send_email(html_body, subject):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText("This email requires HTML rendering.", "plain"))
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [RECIPIENT_EMAIL], msg.as_string())

# ---------- MAIN ----------
def main():
    last_seen = load_last_seen()
    messages = fetch_new_messages(TRACKED_USER, last_seen)

    if not messages:
        print("No new messages since last run. Skipping email.")
        return

    positions, commentary = classify(messages)
    html = build_email_html(positions, commentary)
    subject = f"StockTwits Hourly Update - @{TRACKED_USER} ({len(messages)} new)"
    send_email(html, subject)

    newest_id = max(m["id"] for m in messages)
    save_last_seen(newest_id)
    print(f"Sent update with {len(messages)} new messages. last_seen_id={newest_id}")

if __name__ == "__main__":
    main()
