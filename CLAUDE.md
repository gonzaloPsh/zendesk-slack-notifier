# zendesk-slack-notifier

## What this project does
Python script that reads Zendesk notification emails from Outlook (Microsoft 365) and sends formatted notifications to a Slack channel. Runs automatically every 10 minutes via cron job.

## Flow
```
Cron job (every 10 min)
  → Reads Outlook via Microsoft Graph API
  → Filters emails from zendesk.com
  → Extracts ticket #, subject, description
  → Sends formatted message to Slack channel via Bot Token
  → Saves processed IDs to avoid duplicates
```

## Stack
- **Python 3.10+**
- **Microsoft Graph API** — to read emails from Outlook M365
- **MSAL** — Device Code Flow authentication (compatible with Follett MFA)
- **Slack API** — `chat.postMessage` with Bot Token (`xoxb-...`)
- **python-dotenv** — environment variable management

## Technical decisions
- **No IMAP** — Follett uses corporate M365, Graph API is the correct approach
- **Device Code Flow** instead of ROPC — Follett enforces MFA
- **Bot Token** instead of Incoming Webhook — more flexible for future use
- **Local deduplication** via `.processed_ids.json` — avoids notifying the same email twice
- **Single tenant** in Azure AD — access only within Follett Corp

## Authentication
### Microsoft (Graph API)
- Type: Device Code Flow (Delegated permissions)
- Required permission: `Mail.Read` (Delegated)
- On first run, displays a code to authenticate at https://microsoft.com/devicelogin
- Token is cached in `token_cache.json` and refreshed automatically
- App registered in Azure AD → Single tenant only - Follett Corp

### Slack
- Type: Bot Token (`xoxb-...`)
- App: `test-follett` at api.slack.com
- Required scope: `chat:write`
- Bot must be invited to the channel with `/invite @test-follett`

## Environment variables (.env)
```
TENANT_ID=           # Azure AD → Directory (tenant) ID
CLIENT_ID=           # Azure AD → Application (client) ID
USER_EMAIL=          # gfernandez@follett.com
SLACK_BOT_TOKEN=     # xoxb-... (from api.slack.com → OAuth & Permissions)
SLACK_CHANNEL_ID=    # Target channel ID (starts with C, right-click channel → View details)
ZENDESK_SENDER_FILTER=zendesk.com
LOOKBACK_MINUTES=10
```

## Key files
| File | Description |
|---|---|
| `zendesk_to_slack.py` | Main script |
| `.env` | Environment variables (DO NOT commit) |
| `.env.example` | Template for .env (safe to commit) |
| `token_cache.json` | Microsoft token cache (DO NOT commit) |
| `.processed_ids.json` | Already-notified email IDs (DO NOT commit) |

## Installation
```bash
pip install requests msal python-dotenv
cp .env.example .env
# Fill in .env with real values
python zendesk_to_slack.py
```

## Cron job (Linux/Mac)
```bash
crontab -e
# Add:
*/10 * * * * /usr/bin/python3 /path/to/project/zendesk_to_slack.py >> /path/to/project/zendesk_slack.log 2>&1
```

## Pending / Next steps
- [ ] Run for the first time and complete Device Code authentication
- [ ] Set up cron job
- [ ] Verify ZENDESK_SENDER_FILTER matches the exact sender domain of Zendesk emails

## Organization context
- Company: Follett Corp
- User: Gonzalo Fernandez (gfernandez@follett.com)
- M365 with mandatory MFA
- Limited Zendesk access (non-admin) — tickets arrive as emails to Outlook
- Slack workspace: Follett