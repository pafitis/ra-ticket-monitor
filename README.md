# RA Ticket Monitor

Monitors Resident Advisor (ra.co) for resale ticket availability and sends push notifications via [ntfy.sh](https://ntfy.sh).

Runs on GitHub Actions — no servers, no cost.

## How it works

1. GitHub Actions runs every 5 minutes
2. Each run polls RA's ticket widget every ~15 seconds for 4.5 minutes
3. When resale tickets appear, you get an instant push notification with a direct link to buy
4. Daily heartbeat notification confirms the monitor is alive

## Setup

### 1. Install ntfy on your phone

- [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)
- [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)

Open the app and subscribe to a topic — pick something random and hard to guess (e.g. `ra-tix-k8f2m9x`). This is your notification channel.

### 2. Create the GitHub repo

Fork or push this repo to your GitHub account. **Must be public** for unlimited free GitHub Actions minutes.

### 3. Add secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value | Example |
|--------|-------|---------|
| `RA_EVENT_URLS` | Comma-separated RA event URLs | `https://ra.co/events/1234567,https://ra.co/events/7654321` |
| `NTFY_TOPIC` | Your ntfy topic string | `ra-tix-k8f2m9x` |

### 4. Enable the workflow

The monitor starts automatically once the code is pushed. You can also trigger it manually from the Actions tab → "RA Ticket Monitor" → "Run workflow".

### 5. Verify

- Trigger a manual run from the Actions tab
- Check the workflow logs for "Monitoring N event(s)"
- You should receive a heartbeat notification at 9am UTC daily

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `RA_EVENT_URLS` | (required) | Comma-separated RA event URLs to monitor |
| `NTFY_TOPIC` | (required) | ntfy.sh topic for notifications |
| `CHECK_INTERVAL` | `15` | Seconds between checks within a run |
| `RUN_DURATION` | `270` | How long each run polls (seconds) |
| `HEARTBEAT_HOUR` | `9` | Hour (UTC) to send daily heartbeat |

## Stopping

Disable or delete the workflow from the Actions tab in your repo settings.

## How detection works

RA's embed ticket widget at `/widget/event/{id}/embedtickets` contains the string `"onsale"` when tickets are available. The monitor checks for this string — the same proven method used by every known RA ticket checker.
