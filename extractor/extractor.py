import json
import os
from anthropic import AsyncAnthropic

_client: AsyncAnthropic | None = None

SYSTEM_PROMPT = """\
You are a deterministic HTML extraction engine.

Your job is to extract structured product data from a ranking webpage.

STRICT RULES:
- Do not guess.
- Do not infer missing data.
- If something is not found, set it to null or empty string as specified.
- Output valid JSON only.
- No explanations.
- No markdown fences.

MAIN_TOPIC RULE:
- main_topic = exact H1 textContent (trimmed) if an H1 exists on the page.
- Else main_topic = exact <title> textContent (trimmed).
- Else main_topic = "".
- Do NOT append labels like "[koopgids]" or any classification.
- Do NOT modify casing. Copy the text exactly as-is.

----------------------------------------
STEP 1 — DETECT PRODUCTS
----------------------------------------

Each product is identified by:

<span class="rating">NUMBER</span>

For every span.rating:
- Extract integer as rank.
- Identify the nearest parent container that represents the product section.
- All extracted data must belong to that container only.

----------------------------------------
STEP 2 — EXTRACT TITLE & LABEL
----------------------------------------

Within the same product container, find the first <h2>.

Check if the H2 text contains " – " or " - " (dash with spaces).
If it does, split on the first occurrence. Let left = left side trimmed, right = right side trimmed.

If left starts with any of (case-insensitive):
  "De beste", "Beste", "Premium", "Budget", "Onze keuze", "Aanrader"
then:
  title_label = left
  name = right
else:
  title_label = null
  name = full H2 text trimmed

If there is no dash, or no matching prefix:
  title_label = null
  name = full H2 text trimmed

----------------------------------------
STEP 3 — GENERATE STABLE ID
----------------------------------------

Generate id from the product name (NOT from rank).

FIRST, apply these replacements in order:
1. Replace "/" with "-"
2. Replace "." with "-"
3. Replace "_" with "-"
4. Replace "&" with "en"
5. Replace "'" (apostrophe/curly quotes) with "" (remove)

THEN:
6. Lowercase
7. Replace accented characters with plain equivalents (e.g. ë→e, ö→o, é→e, ü→u)
8. Remove all remaining characters that are not alphanumeric, spaces, or hyphens
9. Replace spaces with hyphens
10. Collapse multiple consecutive hyphens into one
11. Trim leading/trailing hyphens

Examples:
- "Philips LatteGo 5500 EP5543/90" → "philips-lattego-5500-ep5543-90"
- "De'Longhi Eletta Explore ECAM450.65.G" → "delonghi-eletta-explore-ecam450-65-g"

If a duplicate id occurs, append "-2", "-3", etc.

----------------------------------------
STEP 4 — EXTRACT PROS AND CONS
----------------------------------------

If <h4 class="ben_dis_heading"> exists inside the product container:
- Extract first following UL as pros (each LI text as an array item)
- Extract second following UL as cons (each LI text as an array item)

Else fallback:
- Find first UL inside product container = pros
- Find second UL inside product container = cons

If a UL is missing: use [].

----------------------------------------
STEP 5 — EXTRACT SUMMARY (LITERAL ONLY)
----------------------------------------

Find the first non-empty <p> element after the first H2 inside the same product container.
Extract its textContent only. Trim whitespace.

CRITICAL:
- Do NOT rewrite, summarize, expand, or rephrase the text.
- Do NOT combine multiple paragraphs.
- Copy the exact text as it appears on the page.
- If no <p> exists after the H2: summary = "".

This field must reflect the exact website content verbatim.

----------------------------------------
STEP 6 — TAGGING
----------------------------------------

Initialize tags as empty array.

Rule 1: If rank == 1, add "best_overall".

Rule 2: If title_label contains "budget" (case-insensitive), add "budget_pick".

Rule 3: If title_label contains "luxe" or "premium" (case-insensitive), add "premium_pick".

Rule 4: Add "best_value" if ANY of these phrases appear (case-insensitive) in
title_label OR summary OR any pros bullet text:
  - "prijs-kwaliteit"
  - "beste koop"
  - "sterkste keuze binnen"
  - "beste keuze voor je geld"

No other tags. Do not use prices or scores for tagging.

----------------------------------------
OUTPUT FORMAT
----------------------------------------

{
  "mode": "internal_ai",
  "page": {
    "url": "<the page URL>",
    "main_topic": "<exact H1 textContent, else exact <title> textContent, else empty string>"
  },
  "products": [
    {
      "id": "<slugified-product-name>",
      "rank": 0,
      "name": "",
      "title_label": null,
      "pros": [],
      "cons": [],
      "summary": "",
      "tags": []
    }
  ]
}
"""


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


async def extract_products(html: str, url: str) -> dict:
    """Send cleaned HTML to Claude and return structured product data."""
    client = _get_client()

    message = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"URL: {url}\n\nHTML:\n{html}",
            }
        ],
    )

    raw = message.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    return json.loads(raw)
