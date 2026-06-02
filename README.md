# AI News Bot

Get a **once-a-day Telegram digest** of new AI tool updates, plus an **instant
ping** the moment something that looks like a launch goes live (Claude, GPT,
Gemini, Mistral, Perplexity, Cursor, and more).

It runs entirely on GitHub Actions, so there's no server to manage and it's
free.

---

## How it works

A small Python script runs every 30 minutes. Each run it:

- checks all the feeds in `feeds.txt`,
- sends you an instant 🚀 message for any new item whose title looks like a
  launch ("introducing", "now available", "new model", etc.),
- once a day, at or after your chosen hour, sends one tidy digest of everything
  new in the last 24 hours, grouped by source.

It remembers what it has already seen in `seen.json` so you never get the same
item twice.

---

## Setup (about 10 minutes, one time)

### 1. Create your Telegram bot
1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, pick a name, and copy the **token** it gives you
   (looks like `123456:ABC-DEF...`).

### 2. Get your chat ID
1. Send any message (e.g. "hi") to your **new bot** — this is required, because
   bots can't message you until you message them first.
2. In a browser, open:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":...}` in the response. That number is your **chat ID**.

### 3. Put the files on GitHub
1. Create a new repository (private is fine).
2. Upload all the files in this folder, keeping the structure intact —
   especially `.github/workflows/ai-news.yml`.

### 4. Add your secrets
In the repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add two:
- `TELEGRAM_TOKEN` → your bot token
- `TELEGRAM_CHAT_ID` → your chat ID

### 5. Allow the workflow to save state
**Settings → Actions → General → Workflow permissions** → select
**Read and write permissions** → Save. (This lets it commit `seen.json`.)

### 6. First run
Go to the **Actions** tab → **AI News Bot** → **Run workflow**.
On the first run it quietly indexes everything that already exists (so you
aren't flooded) and sends you a "✅ AI News Bot is live" confirmation. From then
on it runs automatically.

---

## Customizing

- **Digest time / timezone:** edit `DIGEST_HOUR` and `TZ_NAME` in
  `.github/workflows/ai-news.yml`. Defaults are 08:00 `Europe/Madrid`.
- **Feeds:** add or remove URLs in `feeds.txt`. To track a tool with no feed
  (like Manus), create a Google Alert set to "Deliver to: RSS feed" and paste
  its URL in — instructions are at the bottom of `feeds.txt`.
- **What counts as a "launch":** edit the `LAUNCH_KEYWORDS` list near the top of
  `ai_news_bot.py`.
- **Polling speed:** the `*/30` in the workflow cron is minutes. You can lower
  it, but note GitHub sometimes delays scheduled runs by a few minutes, and
  faster polling uses more Actions minutes (a **public** repo gets unlimited
  free minutes; a **private** repo gets 2,000/month, which 30-minute polling
  stays under).

---

## Notes

- `seen.json` only contains titles and links, nothing private. Your bot token
  lives in GitHub Secrets and is never written to a file.
- If a feed URL ever dies, the bot logs a warning and keeps going — it won't
  crash the run.
