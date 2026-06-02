#!/usr/bin/env python3
"""
AI News Bot - Telegram daily digest + instant launch alerts.

Run on a schedule (every ~30 min via GitHub Actions). On each run it:
  * fetches every feed listed in feeds.txt
  * sends an INSTANT Telegram message for any brand-new item whose title
    looks like a launch/announcement (keyword match)
  * once a day, at or after DIGEST_HOUR (local time), sends one digest of
    everything new in the last 24h, grouped by source

State (which items were already seen) lives in seen.json, which the GitHub
Actions workflow commits back to the repo between runs.
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:  # very old Python
    ZoneInfo = None

# ----------------------------------------------------------------- config
ROOT = Path(__file__).parent
FEEDS_FILE = ROOT / "feeds.txt"
STATE_FILE = ROOT / "seen.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "8"))      # local hour, 0-23
TZ_NAME = os.environ.get("TZ_NAME", "Europe/Madrid")

# A title containing any of these (case-insensitive) fires an instant alert.
LAUNCH_KEYWORDS = [
    "introducing", "introduces", "launch", "launches", "launching",
    "announcing", "announces", "announced", "unveil", "unveils", "unveiled",
    "now available", "available today", "rolling out", "now live",
    "generally available", "general availability", "is here",
    "debut", "debuts", "new model", "release of",
    "we're releasing", "we are releasing", "now in beta", "now in preview",
]

INSTANT_MAX_AGE_DAYS = 3     # never instant-ping items older than this
DIGEST_WINDOW_HOURS = 24     # digest covers items from the last N hours
PRUNE_AFTER_DAYS = 30        # forget seen items older than this
HTTP_TIMEOUT = 25
USER_AGENT = "Mozilla/5.0 (compatible; ai-news-bot/1.0)"
TG_LIMIT = 4000              # Telegram hard limit is 4096; leave a margin


# ----------------------------------------------------------------- helpers
def log(*args):
    print(*args, file=sys.stderr, flush=True)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def local_tz():
    if ZoneInfo:
        try:
            return ZoneInfo(TZ_NAME)
        except Exception:
            log(f"WARN: unknown TZ_NAME '{TZ_NAME}', falling back to UTC")
    return dt.timezone.utc


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            log("WARN: could not read seen.json, starting fresh:", exc)
    return {"seen": {}, "last_digest_date": None}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_feeds() -> list[str]:
    if not FEEDS_FILE.exists():
        log("ERROR: feeds.txt not found")
        return []
    feeds = []
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            feeds.append(line)
    return feeds


def chunk_text(text: str) -> list[str]:
    if len(text) <= TG_LIMIT:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > TG_LIMIT:
            if cur:
                chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def tg_send(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("ERROR: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set - cannot send")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chunk in chunk_text(text):
        try:
            resp = requests.post(
                url,
                timeout=HTTP_TIMEOUT,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                },
            )
            if resp.status_code != 200:
                log("Telegram error", resp.status_code, resp.text[:300])
        except Exception as exc:
            log("Telegram send failed:", exc)


def source_name(parsed, url: str) -> str:
    try:
        title = (parsed.feed.get("title") or "").strip()
        if title:
            return title
    except Exception:
        pass
    return urlparse(url).netloc


def entry_id(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title") or ""


def entry_time_utc(entry) -> dt.datetime:
    for key in ("published_parsed", "updated_parsed"):
        parsed_time = entry.get(key)
        if parsed_time:
            try:
                return dt.datetime.fromtimestamp(
                    calendar.timegm(parsed_time), dt.timezone.utc
                )
            except Exception:
                pass
    return now_utc()


def is_launch(title: str) -> bool:
    low = (title or "").lower()
    return any(keyword in low for keyword in LAUNCH_KEYWORDS)


def fetch_feed(url: str):
    resp = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def prune(seen: dict) -> None:
    cutoff = now_utc() - dt.timedelta(days=PRUNE_AFTER_DAYS)
    for eid in [
        eid
        for eid, item in seen.items()
        if _safe_ts(item.get("ts")) and _safe_ts(item["ts"]) < cutoff
    ]:
        del seen[eid]


def _safe_ts(value):
    try:
        return dt.datetime.fromisoformat(value)
    except Exception:
        return None


def send_digest(seen: dict, local_now: dt.datetime) -> None:
    cutoff = now_utc() - dt.timedelta(hours=DIGEST_WINDOW_HOURS)
    recent = [
        item
        for item in seen.values()
        if _safe_ts(item.get("ts")) and _safe_ts(item["ts"]) >= cutoff
    ]
    header = f"\U0001F4F0 <b>AI digest - {local_now:%a %d %b}</b>"
    if not recent:
        tg_send(f"{header}\nNothing new in the last 24h.")
        return

    # launches first, then grouped by source, newest last within a group
    recent.sort(key=lambda x: (not x.get("big"), x.get("source", ""), x.get("ts", "")))
    lines = [header, f"{len(recent)} update(s) in the last 24h"]
    current = None
    for item in recent:
        if item.get("source") != current:
            current = item.get("source")
            lines.append(f"\n<b>{html.escape(current or 'Source')}</b>")
        flag = "\U0001F680 " if item.get("big") else "\u2022 "
        link = html.escape(item.get("link", ""))
        title = html.escape(item.get("title", "(untitled)"))
        lines.append(f'{flag}<a href="{link}">{title}</a>')
    tg_send("\n".join(lines))


# ----------------------------------------------------------------- main
def main() -> None:
    state = load_state()
    seen = state.get("seen", {})
    bootstrap = len(seen) == 0 and not state.get("last_digest_date")

    feeds = load_feeds()
    if not feeds:
        return

    instant, new_count, errors = [], 0, []

    for url in feeds:
        try:
            parsed = fetch_feed(url)
        except Exception as exc:
            errors.append(url)
            log("Feed error:", url, exc)
            continue
        src = source_name(parsed, url)
        for entry in parsed.entries:
            eid = entry_id(entry)
            if not eid or eid in seen:
                continue
            title = (entry.get("title") or "(untitled)").strip()
            ts = entry_time_utc(entry)
            big = is_launch(title)
            seen[eid] = {
                "ts": ts.isoformat(),
                "title": title,
                "link": entry.get("link") or url,
                "source": src,
                "big": big,
            }
            new_count += 1
            age_days = (now_utc() - ts).total_seconds() / 86400
            if big and not bootstrap and age_days <= INSTANT_MAX_AGE_DAYS:
                instant.append(seen[eid])

    # instant launch alerts
    for item in instant:
        tg_send(
            "\U0001F680 <b>New launch</b>\n"
            f"<b>{html.escape(item['source'])}</b>\n"
            f"{html.escape(item['title'])}\n"
            f"{html.escape(item['link'])}"
        )

    state["seen"] = seen
    tz = local_tz()
    local_now = now_utc().astimezone(tz)
    today_local = local_now.date().isoformat()

    if bootstrap:
        tg_send(
            "\u2705 <b>AI News Bot is live.</b>\n"
            f"Watching {len(feeds)} feeds. You'll get instant \U0001F680 alerts "
            f"for launches and a daily digest at/after {DIGEST_HOUR:02d}:00 "
            f"{TZ_NAME}."
        )
        state["last_digest_date"] = today_local
        prune(seen)
        save_state(state)
        log(f"Bootstrap done. Indexed {new_count} existing items.")
        return

    # daily digest: first run at/after DIGEST_HOUR each local day
    if state.get("last_digest_date") != today_local and local_now.hour >= DIGEST_HOUR:
        send_digest(seen, local_now)
        state["last_digest_date"] = today_local

    prune(seen)
    save_state(state)
    log(
        f"Done. {new_count} new item(s), {len(instant)} launch alert(s), "
        f"{len(errors)} feed error(s)."
    )


if __name__ == "__main__":
    main()
