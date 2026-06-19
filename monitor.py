import os
import sys
import time
import json
import random
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

GRAPHQL_URL = "https://ra.co/graphql"
WIDGET_URL = "https://ra.co/widget/event/{event_id}/embedtickets"
NTFY_URL = "https://ntfy.sh/"
CACHE_FILE = "notified.json"

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "15"))
RUN_DURATION = int(os.environ.get("RUN_DURATION", "270"))  # 4.5 minutes
HEARTBEAT_HOUR = int(os.environ.get("HEARTBEAT_HOUR", "9"))

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

MAX_BACKOFF = 300
INITIAL_BACKOFF = 5


def random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://ra.co/events",
    }


def parse_event_id(url: str) -> str:
    parts = url.rstrip("/").split("/")
    for i, part in enumerate(parts):
        if part == "events" and i + 1 < len(parts):
            return parts[i + 1]
    raise ValueError(f"Cannot parse event ID from URL: {url}")


def fetch_event_info(client, event_id):
    query = """
    query GetEvent($id: ID!) {
      event(id: $id) {
        id title date startTime endTime
        venue { name area { name } }
        artists { name }
      }
    }
    """
    try:
        resp = client.post(
            GRAPHQL_URL,
            json={"query": query, "variables": {"id": event_id}},
            headers={**random_headers(), "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("event", {})
        return {
            "id": event_id,
            "title": data.get("title", f"Event {event_id}"),
            "venue": data.get("venue", {}).get("name", "Unknown venue"),
            "area": data.get("venue", {}).get("area", {}).get("name", ""),
            "date": data.get("date", ""),
            "artists": ", ".join(a["name"] for a in data.get("artists", [])) or "TBA",
            "url": f"https://ra.co/events/{event_id}",
        }
    except Exception as e:
        log.warning("GraphQL enrichment failed for %s: %s", event_id, e)
        return {
            "id": event_id,
            "title": f"Event {event_id}",
            "venue": "Unknown",
            "area": "",
            "date": "",
            "artists": "Unknown",
            "url": f"https://ra.co/events/{event_id}",
        }


def check_tickets(client, event_id):
    url = WIDGET_URL.format(event_id=event_id)
    resp = client.get(url, headers=random_headers(), timeout=10)
    resp.raise_for_status()
    return "onsale" in resp.text.lower()


def notify(client, topic, title, message, priority="high", tags="ticket", click_url=None):
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if click_url:
        headers["Click"] = click_url
        headers["Actions"] = f"view, Open RA, {click_url}"
    try:
        resp = client.post(
            f"{NTFY_URL}{topic}",
            content=message.encode(),
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Notification sent: %s", title)
    except Exception as e:
        log.error("Failed to send notification: %s", e)


def load_notified():
    path = Path(CACHE_FILE)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            cutoff = time.time() - 86400  # 24 hour expiry
            return {k: v for k, v in data.items() if v > cutoff}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def save_notified(notified):
    Path(CACHE_FILE).write_text(json.dumps(notified))


def should_send_heartbeat():
    now = datetime.now(timezone.utc)
    if now.hour != HEARTBEAT_HOUR:
        return False
    marker = Path(".heartbeat_sent")
    today = now.strftime("%Y-%m-%d")
    if marker.exists() and marker.read_text().strip() == today:
        return False
    marker.write_text(today)
    return True


def main():
    raw_urls = os.environ.get("RA_EVENT_URLS", "")
    topic = os.environ.get("NTFY_TOPIC", "")

    if not raw_urls or not topic:
        log.error("RA_EVENT_URLS and NTFY_TOPIC environment variables are required")
        sys.exit(1)

    event_urls = [u.strip() for u in raw_urls.split(",") if u.strip()]
    event_ids = []
    for url in event_urls:
        try:
            event_ids.append(parse_event_id(url))
        except ValueError as e:
            log.error(str(e))
            sys.exit(1)

    log.info("Monitoring %d event(s): %s", len(event_ids), ", ".join(event_ids))

    notified = load_notified()
    backoff = 0
    last_error_notify = 0

    with httpx.Client() as client:
        events = [fetch_event_info(client, eid) for eid in event_ids]
        log.info("Events loaded: %s", [e["title"] for e in events])

        if should_send_heartbeat():
            event_list = "\n".join(f"  - {e['title']} ({e['venue']})" for e in events)
            notify(
                client, topic,
                "RA Monitor: Still Alive",
                f"Monitoring {len(events)} event(s):\n{event_list}",
                priority="low",
                tags="green_circle,heartbeat",
            )

        start = time.time()
        while time.time() - start < RUN_DURATION:
            for event in events:
                if event["id"] in notified:
                    continue

                try:
                    available = check_tickets(client, event["id"])
                    if available:
                        notify(
                            client, topic,
                            f"TICKETS AVAILABLE: {event['title']}",
                            f"{event['venue']}, {event['area']}\n"
                            f"Date: {event['date']}\n"
                            f"Artists: {event['artists']}\n\n"
                            f"BUY NOW: {event['url']}",
                            priority="urgent",
                            tags="rotating_light,ticket",
                            click_url=event["url"],
                        )
                        notified[event["id"]] = time.time()
                        log.info("TICKETS FOUND for %s", event["title"])
                    else:
                        log.info("No tickets for %s", event["title"])

                    backoff = 0

                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    log.warning("HTTP %d checking %s", status, event["title"])

                    if status == 429:
                        backoff = min((backoff or INITIAL_BACKOFF) * 2, MAX_BACKOFF)
                        if time.time() - last_error_notify > 3600:
                            notify(
                                client, topic,
                                "RA Monitor: Rate Limited",
                                f"Backing off {backoff}s. Will resume automatically.",
                                priority="default",
                                tags="warning",
                            )
                            last_error_notify = time.time()
                    elif status >= 500:
                        backoff = min((backoff or INITIAL_BACKOFF) * 2, MAX_BACKOFF)
                    elif status == 403:
                        if time.time() - last_error_notify > 3600:
                            notify(
                                client, topic,
                                "RA Monitor: Blocked (403)",
                                "RA may have blocked this IP. Check GitHub Actions logs.",
                                priority="high",
                                tags="warning",
                            )
                            last_error_notify = time.time()
                        backoff = 60

                except (httpx.ConnectError, httpx.ReadTimeout) as e:
                    log.warning("Connection issue checking %s: %s", event["title"], e)
                    backoff = min((backoff or INITIAL_BACKOFF) * 2, MAX_BACKOFF)

                except Exception as e:
                    log.error("Unexpected error checking %s: %s", event["title"], e)
                    if time.time() - last_error_notify > 3600:
                        notify(
                            client, topic,
                            "RA Monitor: Error",
                            str(e),
                            priority="default",
                            tags="warning",
                        )
                        last_error_notify = time.time()
                    backoff = min((backoff or INITIAL_BACKOFF) * 2, 60)

                time.sleep(random.uniform(1, 3))

            sleep_time = max(CHECK_INTERVAL + backoff + random.uniform(-2, 2), 5)
            log.info("Sleeping %.1fs (backoff=%ds)", sleep_time, backoff)
            time.sleep(sleep_time)

    save_notified(notified)
    log.info("Run complete. Checked for %.0fs.", time.time() - start)


if __name__ == "__main__":
    main()
