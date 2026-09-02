import os
import time
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

load_dotenv()

STOCKTWITS_API = "https://api.stocktwits.com/api/2"
STOCKTWITS_ACCESS_TOKEN = os.getenv("STOCKTWITS_ACCESS_TOKEN")  # optional
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")  # optional
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")  # optional
REDDIT_USER_AGENT = "StocktwitsToReddit/1.0 by u/YourRedditUsername"
REDDIT_ACCESS_TOKEN = None
TOKEN_EXPIRY = None

SYMBOLS_TO_MONITOR = [
    "SPY",
    "AAPL",
    "NVDA",
    "AMD",
    "TSLA",
    "COIN",
    "MSTR",
    "PLTR",
    "SOFI",
    "GOOGL",
    "META",
    "AMZN",
    "MSFT",
    "QQQ",
    "IWM",
    "DIA",
    "BITO",
    "BITX",
    "CONY",
    "YMAX",
]

SYMBOL_SET = set(SYMBOLS_TO_MONITOR)


def reddit_enabled():
    return bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)


def get_reddit_access_token():
    global REDDIT_ACCESS_TOKEN, TOKEN_EXPIRY
    if REDDIT_ACCESS_TOKEN and TOKEN_EXPIRY and time.time() < TOKEN_EXPIRY:
        return REDDIT_ACCESS_TOKEN

    auth = requests.auth.HTTPBasicAuth(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET)
    data = {"grant_type": "client_credentials"}
    headers = {"User-Agent": REDDIT_USER_AGENT}
    res = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=auth,
        data=data,
        headers=headers,
    )
    res.raise_for_status()
    j = res.json()
    REDDIT_ACCESS_TOKEN = j["access_token"]
    TOKEN_EXPIRY = time.time() + int(j["expires_in"]) - 60
    return REDDIT_ACCESS_TOKEN


def get_stocktwits_messages(symbols, since=None, max_id=None, limit=50):
    params = {"limit": limit}
    if STOCKTWITS_ACCESS_TOKEN:
        params["access_token"] = STOCKTWITS_ACCESS_TOKEN
    if symbols:
        params["symbols"] = ",".join(symbols)
    if since:
        params["since"] = since
    if max_id:
        params["max"] = max_id

    res = requests.get(f"{STOCKTWITS_API}/streams/symbol.json", params=params)
    res.raise_for_status()
    data = res.json()
    return data.get("cursor", {}), data.get("messages", [])


def normalize_reply_count(msg):
    """Safely extract reply count from a StockTwits message.

    'replies' may be an int (e.g. 3) or a list of reply objects.
    """
    replies = msg.get("replies", 0)
    if isinstance(replies, int):
        return replies
    if isinstance(replies, list):
        return len(replies)
    return 0


def post_to_reddit(title, text, symbol, message_url, subreddit="stonks"):
    token = get_reddit_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": REDDIT_USER_AGENT,
    }
    url = "https://oauth.reddit.com/api/submit"
    data = {
        "api_type": "json",
        "sr": subreddit,
        "title": title,
        "text": text,
        "kind": "self",
    }
    res = requests.post(url, headers=headers, data=data)
    res.raise_for_status()
    json = res.json()
    if "error" in json:
        raise Exception(f"Reddit error: {json['error']}")
    post_id = (
        json.get("json", {})
        .get("data", {})
        .get("thing_id", "")
        .replace("t3_", "")
    )
    post_url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
    requests.post(
        "https://oauth.reddit.com/api/comment",
        headers=headers,
        data={
            "api_type": "json",
            "thing_id": f"t3_{post_id}",
            "text": f"Source: {message_url}",
        },
    )
    return post_url


def format_message_for_reddit(msg):
    body = msg.get("body", "")
    user = msg.get("user", {}).get("username", "unknown")
    created = msg.get("created_at", "")
    link = msg.get("links", [{}])[0].get("url", "")
    symbol = msg.get("symbols", [{}])[0].get("symbol", "UNKNOWN")
    reply_count = normalize_reply_count(msg)

    title = f"{symbol}: {body[:200]}"
    text = f"User: {user}\nTime: {created}\n\n{body}\n\nLink: {link}"
    return title, text, symbol, link, reply_count


def classify(messages):
    """Split messages into positions (bullish/bearish on a tracked symbol)
    and commentary (everything else).
    """
    positions = []
    commentary = []

    for m in messages:
        if not isinstance(m, dict):
            continue

        conv = m.get("conversation")
        if isinstance(conv, dict):
            replies_raw = conv.get("replies", 0)
            if isinstance(replies_raw, int):
                reply_count = replies_raw
            elif isinstance(replies_raw, list):
                reply_count = len(replies_raw)
            else:
                reply_count = 0
        else:
            reply_count = 0

        msg = {
            "id": m.get("id"),
            "body": m.get("body", ""),
            "created_at": m.get("created_at", ""),
            "user": m.get("user", {}).get("username", "unknown"),
            "symbols": [s.get("symbol") for s in m.get("symbols", []) if s.get("symbol")],
            "sentiment": m.get("entities", {}).get("sentiment", {}).get("basic"),
            "replies": reply_count,
        }

        symbols = msg["symbols"]
        sentiment = msg["sentiment"]

        if symbols and any(sym in SYMBOL_SET for sym in symbols) and sentiment in ("Bullish", "Bearish"):
            positions.append(msg)
        else:
            commentary.append(msg)

    return positions, commentary


def format_hourly_summary(positions, commentary, hour_start: datetime, hour_end: datetime):
    lines = [
        "StockTwits Hourly Summary",
        f"Period: {hour_start.strftime('%Y-%m-%d %H:%M')} - {hour_end.strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        f"Total messages: {len(positions) + len(commentary)}",
        f"Positions: {len(positions)}",
        f"Commentary: {len(commentary)}",
        "",
        "## Positions",
        "",
    ]

    if not positions:
        lines.append("_No position messages in this hour._")
    else:
        for p in positions:
            lines.append(
                f"- **{p['symbols'][0]}** ({p['sentiment']}) by {p['user']} at {p['created_at']}: {p['body'][:120]}"
            )

    lines.extend(["", "## Commentary", ""])

    if not commentary:
        lines.append("_No commentary messages in this hour._")
    else:
        for c in commentary:
            syms = ", ".join(c["symbols"]) if c["symbols"] else "(no symbols)"
            lines.append(
                f"- **{syms}** by {c['user']} at {c['created_at']}: {c['body'][:120]}"
            )

    return "\n".join(lines)


def main():
    print("Starting hourly Stocktwits fetch...")
    if STOCKTWITS_ACCESS_TOKEN:
        print("Using authenticated StockTwits API access.")
    else:
        print("No STOCKTWITS_ACCESS_TOKEN set - using public (unauthenticated) API access.")

    cursor = {}
    all_messages = []

    while True:
        try:
            cursor, messages = get_stocktwits_messages(
                SYMBOLS_TO_MONITOR,
                since=cursor.get("since"),
                max_id=cursor.get("max"),
                limit=50,
            )
        except requests.HTTPError as e:
            print(f"StockTwits API error: {e}. Stopping fetch.")
            break
        if not messages:
            break
        all_messages.extend(messages)
        if not cursor.get("more"):
            break
        time.sleep(1)

    print(f"Fetched {len(all_messages)} messages.")

    positions, commentary = classify(all_messages)
    now = datetime.now(timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0)

    summary = format_hourly_summary(positions, commentary, hour_start, hour_start)
    print(summary)

    if reddit_enabled():
        for msg in all_messages:
            title, text, symbol, link, reply_count = format_message_for_reddit(msg)
            if reply_count > 0:
                print(f"Skipping message with {reply_count} replies: {link}")
                continue
            try:
                post_url = post_to_reddit(title, text, symbol, link)
                print(f"Posted to Reddit: {post_url}")
            except Exception as e:
                print(f"Error posting to Reddit: {e}")
    else:
        print("Reddit credentials not set - skipping cross-posting.")

    print("Done.")


if __name__ == "__main__":
    main()
