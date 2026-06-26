# IPO Sweep — Slack Delivery Options

Decade Partners, Primary Research. Prepared 25 Jun 2026.
Goal: get the biweekly IPO sweep in front of the team with the cleanest possible read, especially on phone.

## The constraint we hit

- The managed Slack connector used in Cowork is **text-only**. It can post messages but cannot upload files, and there is no alternative Slack connector that adds upload.
- The SharePoint link works, but opening it on mobile triggers a Microsoft login each time.
- Neither limitation can be fixed inside Cowork. Both routes below run the sweep as a proper Decade automation (Claude Code repo plus scheduler), where a secret/token or a deploy step can live. The pull, enrich and render logic we already tested ports straight over.

## Route A — Slack-native file attachment

The job uploads the PDF directly into Slack so it shows as an attached file.

- **What it needs**
  - A small internal Slack app in the Decade workspace with bot scopes `files:write` (upload) and `chat:write` (post).
  - Install it, copy the Bot User OAuth token (xoxb-...), store it as a secret the automation reads.
  - The automation calls Slack `files.uploadV2` with the PDF, target (DM or channel) and a short caption.
- **Who sets it up:** a workspace admin (you or Tom), about 10 to 15 minutes at api.slack.com/apps. One-time.
- **Pros:** best mobile experience by far. The PDF opens inside Slack with no external login, on any device. Fully automatable, works in DMs and channels.
- **Cons:** requires creating and storing a Slack token (a credential to secure). Runs from the automation environment, not the Cowork connector. File lives in Slack, so keep a separate archive copy.
- **Effort:** low to moderate.

## Route B — Public hosted link (Cloudflare)

Each run renders the sweep to a page (and/or the PDF) at a public URL; Slack just carries the link.

- **What it needs**
  - The existing Decade Cloudflare setup (the Automation Handbook already runs on Workers). Add a route or dated path for the sweep, e.g. a page per run plus the PDF at a public path or R2 bucket.
  - The automation renders, deploys, grabs the public URL, and posts it via the current text connector (no upload needed).
  - One access decision: a truly public unguessable URL (zero login anywhere) versus Cloudflare Access (private but SSO-gated).
- **Who sets it up:** the Cloudflare account owner (Tom, per the handbook). Reuses existing infrastructure.
- **Pros:** universal link with zero login on any device if public. Reuses your hosting, clickable in Slack with today's connector, and can host an interactive HTML view, not just a PDF. The link also works outside Slack (email, OneNote, anywhere).
- **Cons:** a real build (render plus deploy). A public URL puts the page on the internet unless you add Access. Another surface to maintain.
- **Effort:** moderate. Fits the existing handbook pipeline.
- **Security note:** the sweep is public-market info with no MNPI, so a public unguessable URL is low risk. Default to an unguessable path or Cloudflare Access if you would rather keep it internal.

## How to choose

- Want it **inside Slack and best on phone**, and fine creating one Slack token: **Route A**.
- Want a **universal link** that also works in email, OneNote and anywhere else, and happy to reuse Cloudflare: **Route B**.
- Many teams run **both**: attach in Slack (A) for the quick read, keep a hosted archive page (B) for linking elsewhere, and keep the SharePoint copy as the system-of-record archive.

## Common next step either way

Stand the sweep up as a Decade automation (Claude Code repo, scheduled biweekly, registered in the Automation Handbook). The tested pull/enrich/render/PDF logic moves over as-is; only the final delivery step differs by route.
