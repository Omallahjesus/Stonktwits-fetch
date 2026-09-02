import os
import time
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

load_dotenv()

STOCKTWITS_API = "https://api.stocktwits.com/api/2"
STOCKTWITS_ACCESS_TOKEN = os.getenv("STOCKTWITS_ACCESS_TOKEN")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
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
    params = {"access_token": STOCKTWITS_ACCESS_TOKEN, "limit": limit}
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
    """Safely extract reply count from StockTwits message.

    StockTwits may return 'replies' as:
      - an integer (e.g. 3)
      - a list of reply objects (e.g. [{...}, {...}])
    This helper returns an int in both cases.
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
    url = f"https://oauth.reddit.com/api/submit"
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
        f"https://oauth.reddit.com/api/comment",
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


def main():
    print("Starting hourly Stocktwits fetch...")
    cursor = {}
    all_messages = []

    while True:
        cursor, messages = get_stocktwits_messages(
            SYMBOLS_TO_MONITOR,
            since=cursor.get("since"),
            max_id=cursor.get("max"),
            limit=50,
        )
        if not messages:
            break
        all_messages.extend(messages)
        if not cursor.get("more"):
            break
        time.sleep(1)

    print(f"Fetched {len(all_messages)} messages.")

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

    print("Done.")


if __name__ == "__main__":
    main()
