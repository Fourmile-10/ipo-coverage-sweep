"""Optional web-search enrichment for priced names.

Adds ONE short line of external context per company (founder's notable prior
companies/exits, marquee investors, or a recent development) that the
prospectus does not give. Strictly grounded in search snippets, clearly tagged
as external, and never blocking: any failure leaves the name as-is.

Off by default. Enabled only when BOTH BRAVE_API_KEY and ANTHROPIC_API_KEY are
set (see config). LinkedIn is deliberately not used (anti-scraping / ToS); this
goes through the Brave Search API instead.
"""
from __future__ import annotations

import requests

import config

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

_SYSTEM = (
    "You are a research assistant for an investment firm. Given web snippets "
    "about a newly-IPO'd company and its CEO, write ONE concise, factual "
    "sentence of context that ADDS to the prospectus: the founder's notable "
    "prior companies or exits, a marquee pre-IPO investor, or a recent "
    "development. Rules: use only facts present in the snippets; no speculation; "
    "no hype; no buy/sell view; no em-dashes. If the snippets add nothing "
    "reliable beyond the prospectus, output exactly NONE."
)


def _brave_snippets(query: str, count: int = 6) -> list[tuple[str, str]]:
    resp = requests.get(
        BRAVE_URL,
        headers={"X-Subscription-Token": config.BRAVE_API_KEY, "Accept": "application/json"},
        params={"q": query, "count": count},
        timeout=20,
    )
    resp.raise_for_status()
    results = resp.json().get("web", {}).get("results", []) or []
    return [(r.get("title", ""), r.get("description", "")) for r in results[:count]]


def enrich(profile) -> str | None:
    """Return a one-sentence external-context string, or None."""
    if not config.WEB_ENRICH or not profile or not profile.name:
        return None
    query = " ".join(x for x in (profile.name, profile.ceo_name,
                                 "founder investors") if x).strip()
    try:
        snippets = _brave_snippets(query)
    except Exception:
        return None
    if not snippets:
        return None
    context = "\n".join(f"- {t}: {d}" for t, d in snippets if (t or d))

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=config.ENRICH_MODEL,
            max_tokens=120,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Company: {profile.name}\n"
                    f"CEO: {profile.ceo_name or 'unknown'}\n"
                    f"What it does (from prospectus): {profile.business[:300]}\n\n"
                    f"Web snippets:\n{context}\n\n"
                    "Write the one-sentence context now, or NONE."
                ),
            }],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content).strip()
    except Exception:
        return None

    if not text or text.upper().startswith("NONE"):
        return None
    return text
