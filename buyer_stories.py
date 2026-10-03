"""
buyer_stories.py - prompts an ICP buyer would actually type, built from their story.

prompt_generator fills one profile field into a template: "how to move off spreadsheets
and QuickBooks invoicing", "Zuora alternatives for Healthcare technology platform". Those
are keyword strings. A buyer asking an AI assistant writes their situation: what kind of
company they are, what they run today, what changed, what hurts, what they need. The
buyer profile already has that material, but split across fields - and the fields came
from whole stories. Each case study discovery read is one buyer: Gentreo was a digital
estate planning SaaS on Recurly that moved into B2B. So a story is rebuilt per page, not
per field.

Two model steps, each checked against something it cannot invent:

1. read_story: from the page's text, the parts of the buyer's situation, each with the
   sentence it came from. A part whose quote is not on the page is dropped.
2. write_prompts: first-person prompts written from those parts only. A prompt that names
   the vendor, uses a part the story does not have, or carries a number or a name nothing
   in the story supports is rejected, with the reason kept.

The model chooses words; it never chooses facts. Every prompt lists the parts it used,
and every part carries its quote and URL, so "why is this prompt here?" always has an
answer on a page.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("gainark.buyer_stories")

# The parts of a buyer's situation a prompt is made of. `role` is who in the buying
# company speaks or signs - "As a controller..." changes what an engine searches for.
PARTS = {
    "company": "what kind of company the buyer is (industry, model, stage), not the vendor",
    "role": "the buyer's job title or function, as the page names it",
    "before": "what the buyer used or did before: tools, vendors, manual processes",
    "trigger": "what changed that made the old way stop working (growth, new market, new deal type)",
    "pain": "what was going wrong: the cost, delay, risk or error the buyer lived with",
    "need": "what the buyer needed a new system to do",
}

# Stages of a buyer's conversation with an assistant, earliest first.
STAGES = {
    "explore": "describes the situation and asks how others handle it, without asking for a product",
    "shortlist": "describes the situation and asks which software or kind of software to look at",
    "switch": "names what they use today and asks what to move to and what to watch for",
    "compare": "weighs the options the situation itself names (staying on what they use "
               "against moving to a dedicated system) and asks which fits their case",
}

# Words that start with a capital in a prompt without being a claim: sentence openers,
# pronouns, and the category's own common acronyms.
_ALWAYS_ALLOWED = {
    "i", "we", "our", "us", "my", "what", "which", "how", "should", "is", "are", "can", "any",
    "do", "does", "has", "have", "who", "why", "when", "where", "if", "and", "or", "but", "the",
    "a", "an", "as", "for", "in", "on", "at", "to", "with", "from", "it", "they", "right",
    "now", "also", "currently", "today", "looking", "need", "saas", "b2b", "b2c",
    "b2b2c", "crm", "erp", "ai", "api", "cfo", "ceo", "coo", "cto", "vp", "finance",
    # The tails of contractions: "We're" tokenises to "we" and "re".
    "re", "ve", "m", "s", "ll", "d", "t",
}

MAX_PAGE_CHARS = 30000

Call = Callable[..., Optional[str]]


@dataclass
class Part:
    text: str
    quote: str


@dataclass
class Story:
    source_url: str
    evidence: List[Dict[str, str]] = field(default_factory=list)   # profile values from this page
    parts: Dict[str, Part] = field(default_factory=dict)
    voice: str = "customer"            # "customer": a buyer's situation; "vendor": the vendor's framing
    dropped: List[Dict[str, str]] = field(default_factory=list)    # parts whose quote is not on the page


@dataclass
class StoryPrompt:
    text: str
    stage: str
    uses: List[str]
    source_url: str
    voice: str
    # "story": every fact is from this buyer's page. "profile": the prompt also names
    # competitors from the site's profile, which this buyer never said they weighed.
    grounding: str = "story"
    sources: Dict[str, Dict[str, str]] = field(default_factory=dict)   # part -> {text, quote}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Grouping the profile's evidence into stories
# ---------------------------------------------------------------------------

def evidence_by_page(profile: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    """The profile's evidenced values, grouped by the page each was quoted from.

    icp_evidence is field -> value -> {source_url, quote}. One page is one story: the
    values a case study supported belong to the same buyer, and splitting them by field is
    what turned "Gentreo moved off Recurly when it went B2B" into two unrelated templates.
    """
    pages: Dict[str, List[Dict[str, str]]] = {}
    for field_name, values in (profile.get("icp_evidence") or {}).items():
        if not isinstance(values, dict):
            continue
        for value, ev in values.items():
            url = (ev or {}).get("source_url") if isinstance(ev, dict) else None
            if url:
                pages.setdefault(url, []).append(
                    {"field": field_name, "value": value, "quote": ev.get("quote", "")})
    return pages


# ---------------------------------------------------------------------------
# Checks the model cannot talk its way past
# ---------------------------------------------------------------------------

def _tokens(text: str) -> List[str]:
    """Lowercase word tokens. Punctuation, curly quotes and the replacement characters a
    mis-decoded page leaves behind ("Ordway�reducing") all count as separators, so a
    quote matches the page it was copied from however either was encoded."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def quote_on_page(quote: str, page_text: str) -> bool:
    """Whether a quote's words appear, in order and contiguously, in the page's words."""
    q = _tokens(quote)
    if len(q) < 4:                       # too short to be a quote of anything
        return False
    return " %s " % " ".join(q) in " %s " % " ".join(_tokens(page_text))


_FILLER = {"with", "that", "this", "their", "they", "them", "more", "most", "need", "needed",
           "needs", "used", "using", "could", "would", "from", "into", "about", "both", "also",
           "were", "have", "been", "which", "when", "able", "than", "very", "much", "many"}


def supported(text: str, quote: str) -> float:
    """Share of a part's content words the quote also has (by a five-letter stem, so
    "prorations" meets "proration"). The quote check proves a quote is on the page; this
    proves the part says what its quote says - on 3 Oct 2026 a need of "usage-based
    billing" came with a quote about "a system that met the current needs"."""
    def stems(s):
        return {w[:5] for w in _tokens(s) if len(w) > 3 and w not in _FILLER}
    words = stems(text)
    return len(words & stems(quote)) / len(words) if words else 0.0


MIN_SUPPORT = 0.5

# Parts that describe the buyer before the purchase. A quote naming the vendor in one of
# them is describing life after it: "The best part of Ordway is that we finally have a
# system to do all the complex revenue math".
_BEFORE_PARTS = ("trigger", "pain", "need")


def _sentence_initial(text: str) -> set:
    """Positions of words that open a sentence, where a capital proves nothing."""
    starts, at_start = set(), True
    # A word may not carry a full stop: "company." would swallow the end of its sentence.
    for m in re.finditer(r"[A-Za-z0-9][\w'-]*|[.?!]", text):
        tok = m.group(0)
        if tok in ".?!":
            at_start = True
            continue
        if at_start:
            starts.add(m.start())
        at_start = False
    return starts


def unsupported(text: str, support: str) -> List[str]:
    """Numbers and names in a prompt that nothing in its story supports.

    A name is a capitalised word that does not open a sentence: "Recurly", "NetSuite",
    "ASC". A number is any digit run. Each must appear in the support text - the story's
    parts and quotes, and the competitors the profile evidences - or it is a fact the
    model added.
    """
    have = set(_tokens(support))
    starts = _sentence_initial(text)
    bad = []
    for m in re.finditer(r"[A-Za-z0-9][\w'-]*", text):
        word = m.group(0)
        if any(c.isdigit() for c in word):
            if not set(_tokens(word)) <= have:
                bad.append(word)
        elif word[0].isupper() and m.start() not in starts:
            toks = _tokens(word)
            if toks and not all(t in have or t in _ALWAYS_ALLOWED for t in toks):
                bad.append(word)
    return bad


def parts_in(text: str, story: Story) -> List[str]:
    """The parts whose words a prompt mostly repeats: half or more of a part's content
    words appear in it."""
    have = set(_tokens(text))
    found = []
    for name, part in story.parts.items():
        words = [t for t in _tokens(part.text) if len(t) > 3]
        if words and sum(t in have for t in words) * 2 >= len(words):
            found.append(name)
    return found


def check_prompt(text: str, uses: List[str], story: Story, brand: str,
                 competitors: List[str]) -> Tuple[Optional[str], str]:
    """(why a written prompt is unusable or None, its grounding).

    A prompt whose every name and number is in the story is grounded "story". One that
    also names competitors from the site's profile is kept as "profile": a plausible
    question, but this buyer never said they weighed those vendors, so it is not
    evidence about them and is kept apart. Anything else unsupported rejects it.
    """
    if not text or len(_tokens(text)) < 6:
        return "too short", ""
    if brand and re.search(r"\b%s\b" % re.escape(brand), text, re.IGNORECASE):
        return "names the vendor %r - a buyer asking for options does not know the answer" % brand, ""
    unknown = [u for u in uses if u not in story.parts]
    if unknown:
        return "uses parts the story does not have: %s" % ", ".join(unknown), ""
    if not uses:
        return "uses no part of the story", ""
    story_support = " ".join(p.text + " " + p.quote for p in story.parts.values())
    if not unsupported(text, story_support):
        return None, "story"
    bad = unsupported(text, " ".join([story_support] + competitors))
    if bad:
        return "adds facts the story does not support: %s" % ", ".join(sorted(set(bad))), ""
    return None, "profile"


# ---------------------------------------------------------------------------
# The two model steps
# ---------------------------------------------------------------------------

def _gemini(prompt: str, system: str) -> Optional[str]:
    import vertex_ai_client
    return vertex_ai_client._call_gemini(prompt=prompt, system_instruction=system,
                                         temperature=0.2, max_output_tokens=4096, timeout=90)


def _json(text: Optional[str]) -> Any:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except ValueError:
        logger.warning("[BuyerStories] Model returned unparseable JSON: %.300s", text)
        return None


def read_story(source_url: str, page_text: str, evidence: List[Dict[str, str]], brand: str,
               call: Call = _gemini) -> Story:
    """The buyer's situation on one page, each part with the sentence it came from."""
    story = Story(source_url=source_url, evidence=evidence)
    parts_spec = "\n".join("- %s: %s" % (k, v) for k, v in PARTS.items())
    known = "\n".join("- %s: %s" % (e["field"], e["value"]) for e in evidence)
    prompt = (
        "This page is from %(brand)s, a vendor. Read it as the story of one buyer: the "
        "customer company, not %(brand)s.\n\n"
        "Return JSON: {\"voice\": \"customer\" or \"vendor\", \"parts\": {part: {\"text\": "
        "..., \"quote\": ...}}}.\n"
        "voice is \"customer\" when the page tells a specific customer's situation (a case "
        "study, a quote from a customer), \"vendor\" when it is %(brand)s describing buyers "
        "in general or pitching itself.\n"
        "Parts, each only if the page states it:\n%(parts)s\n"
        "text: a short phrase in the buyer's terms, without the customer's company name or "
        "%(brand)s's name. quote: one sentence copied exactly from the page that states it. "
        "Leave a part out rather than infer it. Nothing about results after adopting "
        "%(brand)s - only the situation before: a sentence describing what %(brand)s did "
        "for them (\"we've automated everything\", \"the result is\") is not a need.\n"
        "text must say only what its quote says, in fewer words; add nothing the quote "
        "does not state.\n"
        "company must say what the business is (its industry, product or model) as a plain "
        "lowercase description - \"a background-check platform\", not a product or category "
        "name in capitals; leave it out if the page only says something vague like \"a "
        "growing company\".\n"
        "role is one person: the buyer who decided or ran the change. If the page quotes "
        "several people, take the most senior finance or operations one.\n\n"
        "Already found on this page:\n%(known)s\n\nPAGE:\n%(page)s"
    ) % {"brand": brand, "parts": parts_spec, "known": known or "- nothing",
         "page": page_text[:MAX_PAGE_CHARS]}
    data = _json(call(prompt, "You extract facts from a page with verbatim quotes. Output valid JSON only."))
    if not isinstance(data, dict):
        return story
    story.voice = "vendor" if data.get("voice") == "vendor" else "customer"
    for name, part in (data.get("parts") or {}).items():
        if name not in PARTS or not isinstance(part, dict):
            continue
        text, quote = (part.get("text") or "").strip(), (part.get("quote") or "").strip()
        if name == "role":
            # One buyer. "Director of Accounting, Revenue Operations Specialist" made the
            # prompts change speaker from one stage to the next.
            text = re.split(r",|;| and | & ", text)[0].strip()
        if not text:
            continue
        reason = None
        if not quote_on_page(quote, page_text):
            reason = "quote not on the page"
        elif name != "role" and supported(text, quote) < MIN_SUPPORT:
            reason = "text says more than its quote"
        elif name in _BEFORE_PARTS and brand and re.search(
                r"\b%s\b" % re.escape(brand), quote, re.IGNORECASE):
            reason = "quote names %s: an outcome, not the situation before" % brand
        if reason:
            story.dropped.append({"part": name, "text": text, "quote": quote, "reason": reason})
        else:
            story.parts[name] = Part(text=text, quote=quote)
    return story


def write_prompts(story: Story, brand: str, competitors: List[str],
                  call: Call = _gemini) -> Tuple[List[StoryPrompt], List[Dict[str, Any]]]:
    """First-person prompts from a story's parts. Returns (kept, rejected-with-reason)."""
    if not story.parts:
        return [], []
    parts = "\n".join("- %s: %s" % (k, p.text) for k, p in story.parts.items())
    stages = "\n".join("- %s: %s" % (k, v) for k, v in STAGES.items())
    prompt = (
        "Write what this buyer would type into an AI assistant such as ChatGPT, in the first "
        "person, as they would actually write it: plain, specific, one to three sentences, "
        "no marketing language.\n\n"
        "The buyer's situation - use only these facts, and no other numbers, names or "
        "details:\n%(parts)s\n\n"
        "Say each fact the way the buyer would say it to a colleague, in your own words. "
        "Do not copy the phrases above. (For example, in an unrelated business, \"order "
        "entry required significant manual re-keying\" might become \"we lose every Monday "
        "re-typing orders\".) Keep tool and product names as they are.\n"
        "Name no vendor the situation does not name, and never %(brand)s: the buyer is "
        "asking for options and does not know the answer.\n\n"
        "One prompt per stage that fits the situation; skip a stage that does not fit:\n"
        "%(stages)s\n\n"
        "Return JSON: [{\"stage\": ..., \"text\": ..., \"uses\": [...]}], where uses lists "
        "only part names from: %(names)s."
    ) % {"parts": parts, "brand": brand, "stages": stages, "names": ", ".join(story.parts)}
    data = _json(call(prompt, "You write realistic buyer questions from given facts only. Output valid JSON only."))
    kept, rejected = [], []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        text, stage = (item.get("text") or "").strip(), item.get("stage")
        uses = [u for u in item.get("uses") or [] if isinstance(u, str)]
        if not uses or any(u not in PARTS for u in uses):
            # The model sometimes lists phrases rather than part names. What a prompt
            # relies on is then read off the prompt itself; the fact check below is the
            # guard either way.
            uses = parts_in(text, story)
        if stage not in STAGES:
            reason, grounding = "unknown stage %r" % stage, ""
        else:
            reason, grounding = check_prompt(text, uses, story, brand, competitors)
        if reason:
            rejected.append({"text": text, "stage": stage, "reason": reason})
            continue
        kept.append(StoryPrompt(
            text=text, stage=stage, uses=uses, source_url=story.source_url, voice=story.voice,
            grounding=grounding,
            sources={u: {"text": story.parts[u].text, "quote": story.parts[u].quote} for u in uses}))
    return kept, rejected


# ---------------------------------------------------------------------------
# A site
# ---------------------------------------------------------------------------

def page_text(url: str) -> str:
    """A page's readable text, read the way discovery reads it."""
    from bs4 import BeautifulSoup
    from scraper import smart_fetch
    html = smart_fetch(url, lite="trimmed")
    return BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)


def prompts_for_site(domain: str, profile: Optional[Dict[str, Any]] = None,
                     call: Call = _gemini, fetch: Callable[[str], str] = page_text) -> Dict[str, Any]:
    """Every story on a site's evidence pages, and the prompts each one yields."""
    if profile is None:
        import buyer_profiles
        profile = buyer_profiles.load(domain) or {}
    brand = (profile.get("brand_name") or "").strip()
    competitors = [c for c in profile.get("known_competitors") or [] if c]
    out = {"domain": domain, "brand": brand, "stories": []}
    for url, evidence in evidence_by_page(profile).items():
        try:
            text = fetch(url)
        except Exception as err:
            logger.warning("[BuyerStories] Could not read %s: %s", url, err)
            out["stories"].append({"source_url": url, "error": str(err)})
            continue
        story = read_story(url, text, evidence, brand, call=call)
        kept, rejected = write_prompts(story, brand, competitors, call=call)
        out["stories"].append({
            "source_url": url,
            "voice": story.voice,
            "parts": {k: asdict(p) for k, p in story.parts.items()},
            "dropped_parts": story.dropped,
            "prompts": [p.to_dict() for p in kept],
            "rejected": rejected,
        })
    return out


def for_fanout(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The prompts worth measuring: a real customer's situation, every fact from their
    page. A vendor page describes the buyer it wants, and a "profile" prompt names
    vendors that buyer never weighed - both are kept in the result, and neither is
    evidence of what a buyer asks."""
    return [p for s in result.get("stories") or [] if s.get("voice") == "customer"
            for p in s.get("prompts") or [] if p.get("grounding") == "story"]


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(prompts_for_site(sys.argv[1]), indent=1, ensure_ascii=False))
