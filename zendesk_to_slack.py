"""
zendesk_to_slack.py
────────────────────────────────────────────────────────────────
Reads Zendesk notification emails from Outlook (Microsoft 365 via Graph API)
and sends formatted notifications to a Slack channel.

Microsoft authentication: Device Code Flow (MFA-compatible)
Slack authentication: Bot Token (xoxb-...)

Requirements:
    pip install requests msal python-dotenv
────────────────────────────────────────────────────────────────
"""

import os, json, re, requests
from datetime import datetime, timezone, timedelta
from msal import PublicClientApplication, SerializableTokenCache
from dotenv import load_dotenv

load_dotenv()

TENANT_ID             = os.getenv("TENANT_ID")
CLIENT_ID             = os.getenv("CLIENT_ID")
USER_EMAIL            = os.getenv("USER_EMAIL")
SLACK_BOT_TOKEN       = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID      = os.getenv("SLACK_CHANNEL_ID")
LOOKBACK_MINUTES      = int(os.getenv("LOOKBACK_MINUTES", "10"))
ZENDESK_SENDER_FILTER = os.getenv("ZENDESK_SENDER_FILTER", "zendesk.com")

GRAPH_SCOPES     = ["Mail.Read", "User.Read"]
TOKEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_cache.json")

# ── Device Code Flow Authentication ───────────────────────────
def load_token_cache():
    cache = SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_FILE):
        with open(TOKEN_CACHE_FILE) as f:
            cache.deserialize(f.read())
    return cache

def save_token_cache(cache):
    if cache.has_state_changed:
        with open(TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())

def get_access_token():
    cache = load_token_cache()
    app = PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )

    accounts = app.get_accounts(username=USER_EMAIL)
    result = None
    if accounts:
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])

    if not result or "access_token" not in result:
        flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Error initiating Device Code Flow: {flow}")

        print("\n" + "="*60)
        print("AUTHENTICATION REQUIRED — first time only")
        print("="*60)
        print(f"1. Open:   https://microsoft.com/devicelogin")
        print(f"2. Code:   {flow['user_code']}")
        print(f"3. Log in with: {USER_EMAIL}")
        print("="*60 + "\n")

        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Authentication error: {result.get('error_description', result)}")

    save_token_cache(cache)
    return result["access_token"]

# ── Graph API ─────────────────────────────────────────────────
def fetch_recent_zendesk_emails(token):
    since = (datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"https://graph.microsoft.com/v1.0/me/messages"
        f"?$filter=receivedDateTime ge {since}"
        f" and contains(from/emailAddress/address,'{ZENDESK_SENDER_FILTER}')"
        f"&$select=id,subject,receivedDateTime,from,bodyPreview,webLink"
        f"&$orderby=receivedDateTime desc&$top=20"
    )
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code == 401:
        if os.path.exists(TOKEN_CACHE_FILE):
            os.remove(TOKEN_CACHE_FILE)
        raise RuntimeError("Token expired. Run the script again to re-authenticate.")
    response.raise_for_status()
    return response.json().get("value", [])

# ── Parsing ───────────────────────────────────────────────────
def extract_ticket_info(email):
    subject  = email.get("subject", "No subject")
    preview  = email.get("bodyPreview", "")
    received = email.get("receivedDateTime", "")
    link     = email.get("webLink", "")

    match = re.search(r"#(\d+)", subject) or re.search(r"#(\d+)", preview)
    ticket_number = match.group(1) if match else None
    clean_subject = re.sub(r"\[.*?\]\s*", "", subject).strip()

    try:
        dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
        dt_arg = dt.astimezone(timezone(timedelta(hours=-3)))
        formatted_date = dt_arg.strftime("%d/%m/%Y %H:%M")
    except Exception:
        formatted_date = received

    return {
        "ticket_number": ticket_number,
        "subject": clean_subject,
        "received": formatted_date,
        "preview": preview[:300] + ("..." if len(preview) > 300 else ""),
        "link": link,
    }

# ── Slack ─────────────────────────────────────────────────────
def build_slack_blocks(info):
    ticket_label = f"Ticket #{info['ticket_number']}" if info["ticket_number"] else "New ticket"
    return [
        {"type": "header", "text": {"type": "plain_text", "text": f"🎫 {ticket_label} — Zendesk", "emoji": True}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Subject:*\n{info['subject']}"},
            {"type": "mrkdwn", "text": f"*Received:*\n{info['received']}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Description:*\n{info['preview']}"}},
        {"type": "divider"},
        {"type": "actions", "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": "📧 View in Outlook", "emoji": True},
             "url": info["link"],
             "style": "primary"}
        ]},
    ]

def send_to_slack(info):
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "channel": SLACK_CHANNEL_ID,
            "blocks": build_slack_blocks(info),
            "text": f"New Zendesk ticket: {info['subject']}",  # fallback for notifications
        },
        timeout=10,
    )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data.get('error')} — {data}")

# ── Deduplication ─────────────────────────────────────────────
PROCESSED_IDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".processed_ids.json")

def load_processed_ids():
    if os.path.exists(PROCESSED_IDS_FILE):
        with open(PROCESSED_IDS_FILE) as f:
            return set(json.load(f))
    return set()

def save_processed_ids(ids):
    with open(PROCESSED_IDS_FILE, "w") as f:
        json.dump(list(ids)[-500:], f)

# ── Main ──────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking for Zendesk emails...")
    token  = get_access_token()
    emails = fetch_recent_zendesk_emails(token)

    if not emails:
        print("  → No new emails.")
        return

    processed_ids = load_processed_ids()
    new_count = 0

    for email in emails:
        email_id = email["id"]
        if email_id in processed_ids:
            continue
        info = extract_ticket_info(email)
        send_to_slack(info)
        processed_ids.add(email_id)
        new_count += 1
        print(f"  ✓ Notified: {info['subject'][:60]}")

    save_processed_ids(processed_ids)
    print(f"  → {new_count} notification(s) sent.")

if __name__ == "__main__":
    main()
