import re
from datetime import datetime, timezone

import stocktwits_daily_email as daily

TARGET_USERS = ("chartistmind", "momobull")


def main():
    now_et = datetime.now(daily.ET)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    sections = []
    plain_sections = []
    original_user = daily.TARGET_USER
    try:
        for user in TARGET_USERS:
            print(f"Fetching @{user} messages from the past {daily.WINDOW_HOURS} hours...")
            messages = daily.fetch_user_messages(user)
            messages.sort(
                key=lambda m: daily.parse_ts(m.get("created_at")) or epoch,
                reverse=True,
            )
            daily.TARGET_USER = user
            plain_sections.append(daily.format_plain(messages, now_et))
            rendered = daily.format_html(messages, now_et)
            body = re.search(r"<body[^>]*>(.*)</body>", rendered, re.DOTALL)
            if body is None:
                raise ValueError("Expected an HTML body in the email template")
            sections.append(body.group(1))
            print(f"@{user}: {len(messages)} messages")
    finally:
        daily.TARGET_USER = original_user

    users = " + ".join(f"@{user}" for user in TARGET_USERS)
    subject = f"StockTwits Summary - {users} - {now_et.strftime('%b %d, %Y %I:%M %p ET')}"
    text_body = "\n\n".join(plain_sections)
    html_body = (
        '<html><body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">'
        + '<div style="height:16px;"></div>'.join(sections)
        + "</body></html>"
    )
    daily.send_email(subject, text_body, html_body)
    print("Done.")


if __name__ == "__main__":
    main()
