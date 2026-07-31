# IPO Coverage Sweep — Setup Guide

Everything you need to take this from "code on disk" to "posts to Slack every
second Thursday." Work top to bottom. Steps 1 and 2 get you a real test post in
~20 minutes; steps 3 to 5 put it on the schedule.

The code is already built, committed locally, and verified against a live
window. Nothing below changes the code; it is all accounts, tokens, and config.

---

## Step 0 — What you are setting up

| Piece | Who does it | One-time? |
|---|---|---|
| Slack app + bot token | You (or Tom; needs workspace admin) | Yes |
| Local test run | You | No (repeat anytime) |
| GitHub repo + secrets | You | Yes |
| Schedule (already in the repo) | Automatic once pushed | Yes |
| Handbook registration | You | Yes |

Secrets you will collect along the way:

| Name | Example | Where it is used |
|---|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-1234...` | Posting + file upload |
| `SLACK_CHANNEL_ID` | `C0ABC123` or DM `D0ABC123` | Where it posts |
| `SEC_USER_AGENT` | `Decade Partners Research glenn@decadepartners.com.au` | SEC requires it |
| `SHAREPOINT_DEST` | a synced folder path | Optional PDF archive |
| `BIWEEKLY_PARITY` | `0` or `1` | Which fortnight it runs |

---

## Step 1 — Create the Slack app (about 15 min, needs workspace admin)

1. Go to <https://api.slack.com/apps> and click **Create New App** → **From scratch**.
2. Name it `Decade IPO Sweep`, pick the **Decade Partners** workspace, **Create App**.
3. Left sidebar → **OAuth & Permissions**.
4. Scroll to **Scopes** → **Bot Token Scopes** → **Add an OAuth Scope**, add both:
   - `chat:write`
   - `files:write`
5. Scroll back up → **Install to Workspace** → **Allow**.
6. Copy the **Bot User OAuth Token** (starts `xoxb-`). This is `SLACK_BOT_TOKEN`. Keep it private.

### Pick where it posts and get the channel ID

- **Recommended:** create a private channel, e.g. `#ipo-sweep`. Open it, type
  `/invite @Decade IPO Sweep` so the bot can post there.
- Get the channel ID: click the channel name at the top → scroll to the very
  bottom of the popup → copy the **Channel ID** (looks like `C0ABC123`). This is
  `SLACK_CHANNEL_ID`.
- **To test into your own DM instead:** message the bot once (find it under Apps),
  then use your DM conversation ID (`D0...`). A channel the bot is in is simpler.

---

## Step 2 — Test it locally (no GitHub needed yet)

The Python venv is already created with dependencies installed. From Git Bash:

```bash
cd "Decade Partners/Investing - Frameworks/IPO Coverage Sweep - Automation"

# 1. Put your secrets in a local .env (never committed):
cp .env.example .env
#    edit .env: paste SLACK_BOT_TOKEN and SLACK_CHANNEL_ID

# 2. Dry run first — builds the PDF, pulls live data, posts NOTHING:
./.venv/Scripts/python.exe main.py --dry-run --window-end 2026-06-26

# 3. Real post to Slack (--force bypasses the every-other-week gate):
./.venv/Scripts/python.exe main.py --force
```

After step 3 you should see the sweep land in `#ipo-sweep` with the PDF
attached. Open the PDF on your phone to confirm it reads cleanly with no
Microsoft login (that is the whole point of the Slack-native attachment).

If something is wrong, the run prints a JSON run log (per-source status, counts,
errors) so you can see exactly which source failed.

---

## Step 3 — Put it on GitHub (for the schedule)

GitHub Actions is what runs it automatically. You need the repo pushed and the
secrets stored there.

1. Create an empty repo, e.g. `tt7676/ipo-coverage-sweep` (private).
   *(I can do this for you with the `gh` CLI — just ask.)*
2. Push the local commit:
   ```bash
   git remote add origin https://github.com/tt7676/ipo-coverage-sweep.git
   git branch -M main
   git push -u origin main
   ```
3. In the repo on github.com → **Settings** → **Secrets and variables** →
   **Actions**:
   - **Secrets** tab → **New repository secret**, add:
     - `SLACK_BOT_TOKEN`
     - `SEC_USER_AGENT` = `Decade Partners Research glenn@decadepartners.com.au`
   - **Variables** tab → **New repository variable**, add:
     - `BIWEEKLY_PARITY` = `0` (see Step 5 to pick the right fortnight)
     - `SLACK_CHANNEL_ID` is **optional** — the code defaults to
       #ipos-spacs-things (`C01R4V0ESDT`). Add it as a repo *variable* only to
       post somewhere else. (The bot must be invited to whichever channel.)

That is it. The workflow file (`.github/workflows/biweekly.yml`) is already in
the repo and turns on automatically.

---

## Step 4 — First scheduled-style test from GitHub

Do not wait until Thursday to find out if CI works.

1. Repo → **Actions** tab → **IPO Coverage Sweep** → **Run workflow**.
2. Leave the inputs blank (or put your test channel ID in `channel_id`).
3. Click **Run workflow**. Manual runs always run (they bypass the off-week gate).
4. Confirm the Slack post appears and the green check shows in Actions.

A manual run is the same path the schedule uses, so a green manual run means the
schedule will work.

---

## Step 5 — Choose the fortnight (`BIWEEKLY_PARITY`)

The job fires every Thursday but only acts on every second one, decided by
whether the ISO week number is even or odd.

- `BIWEEKLY_PARITY = 0` → runs on **even** ISO weeks.
- `BIWEEKLY_PARITY = 1` → runs on **odd** ISO weeks.

To check which fortnight you will get, look at the current ISO week number and
set parity to match the Thursday you want the first real run. If the first
scheduled run lands on the wrong week, just flip the variable (`0` ↔ `1`).

To see the upcoming behaviour without waiting:
```bash
./.venv/Scripts/python.exe main.py --dry-run   # uses today; prints if it would skip
```

---

## Step 6 — Register in the Automation Handbook (optional, recommended)

So the team can see it on the status page:

1. Add the discovery topic to the repo:
   ```bash
   gh repo edit tt7676/ipo-coverage-sweep --add-topic decade-automation
   ```
2. Run `/refresh-handbook` (or wait for the nightly rebuild). The card uses the
   `automation.yaml` already in the repo.

---

## About the SharePoint archive (read this)

The Slack attachment is the primary, reliable delivery and needs nothing more.

The optional `SHAREPOINT_DEST` archive copies the PDF into a **synced local
folder**. That works when you run locally (your OneDrive is mounted), but a
**GitHub-hosted runner cannot see your OneDrive**, so the archive step is
skipped on scheduled CI runs (the run log says `skipped`). This is expected and
never fails the run.

If you want the archive to happen automatically every run, the options are:
- (a) Leave it Slack-only from CI; run locally when you want a filed copy. *(Simplest, recommended for now.)*
- (b) Have me add a Microsoft Graph upload so CI pushes the PDF straight to SharePoint. *(A small add-on build.)*
- (c) Use a self-hosted runner that has the OneDrive folder.

---

## Day-to-day

- **Trigger a run anytime:** Actions → Run workflow (with an optional channel or
  window-end override).
- **Something broke:** the run fails loud — it posts a red alert to Slack and the
  Actions run goes red. Open the run log in the Actions output to see which
  source errored. No silent all-clear is ever posted.
- **Pause it:** set `status: paused` in `automation.yaml` and disable the
  workflow in the Actions tab.
- **Change cadence or time:** edit the `cron` in `.github/workflows/biweekly.yml`
  (it is UTC; Wed 20:00 UTC = Thu 07:00 Sydney).

---

## Quick checklist

- [ ] Slack app created, `chat:write` + `files:write`, installed
- [ ] Bot token copied, bot invited to the target channel
- [ ] `.env` filled, local `--force` run lands in Slack with the PDF
- [ ] GitHub repo created and pushed
- [ ] Secrets + `BIWEEKLY_PARITY` set in the repo
- [ ] Manual "Run workflow" goes green and posts
- [ ] (Optional) `decade-automation` topic added, handbook refreshed
