"""
prompt_generator.py - the prompts an ICP buyer would type, built from the ontology.

A buyer does not search for a product. They search for the thing that is going wrong
("how to move off spreadsheets"), for the capability that would fix it ("usage-based
billing software"), for who else sells it ("Zuora alternatives"), or for the box they
have to tick ("ASC 606 compliant billing"). Each is a different stage of the same search,
and the ontology already holds the material for all four: what customers are leaving
behind, what the category does, who the competitors are, which standards apply.

Nothing here calls a model or the network. A prompt is a template filled from one named
piece of the ontology or of the site's buyer profile, and it carries that source with it,
so a caller can always answer "why is this prompt here?". The buyer fields go one step
further: a value discovery *proved* against a page it read arrives with the quote and the
URL, and a value that was only proposed is labelled as such. Ranking puts proven material
first, because a prompt built on an unproven claim is a guess about a guess.

Depth varies enormously between verticals - two of eleven are curated, with definitions
and a concept tree, and the rest hold a dozen seed terms and nothing else. That gap is not
smoothed over: the generator consumes whatever depth exists, and coverage["missing"] says
in plain words what curation would add. The length of that list is the onboarding cost for
a new customer.
"""

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import buyer_profiles
from concept_roles import (ACRONYMISH as _ACRONYMISH, INNER_CAPITAL as _INNER_CAPITAL,
                           ConceptTree as _Tree, is_rule_body as _is_rule_body,
                           names_a_rule as _names_a_rule, role as _role,
                           seed_subjects as _seed_subjects, variant as _variant)
from industry_ontology import load_industry_ontology
from models import IndustryOntologyModel

logger = logging.getLogger("gainark.prompt_generator")

# Four stages of a buyer's search, most committed first. Someone typing "how to move off
# spreadsheets" has admitted a problem; someone typing "billing software" may be reading
# around. Ordering by stage first puts the high-intent prompts at the top, which is the
# order the material was worth gathering in.
STAGES = ("problem", "comparison", "validation", "solution")

# Where a prompt's subject came from, best first. "evidenced" means discovery quoted a
# page it had read to support this exact value; "proposed" means a model offered it and
# nothing confirmed it; "ontology" means the vertical's own vocabulary, which is curated
# or seeded rather than observed on this site.
GROUNDINGS = ("evidenced", "proposed", "ontology")

# A qualifier only works when it is the name of a buyer ("SaaS", "Digital estate planning
# SaaS"), not discovery's sentence describing one. Four words is where the ordwaylabs.com
# segments stop being names and start being descriptions.
_QUALIFIER_MAX_WORDS = 4
# A qualifier belongs on a prompt that is shopping for something. Someone looking a term
# up has not decided to buy anything, so naming their own segment makes no sense - "what
# is monetization model for Digital estate planning SaaS" and "monetization model
# explained for SaaS" are not queries. Tested by looking for the thing being shopped for
# rather than by ruling question words out, which "{x} explained" walked straight past.
# Only words _TEMPLATES itself contributes are listed: the test runs on the finished text,
# so a word that can also appear in a subject would qualify a prompt that is not shopping
# at all - "platform" let "what is platform capabilities for Construction" through.
_IS_SHOPPING = re.compile(r"\b(software|tools?|alternatives?|pricing|integration)\b",
                          re.IGNORECASE)
# Enough to show the shape of a qualified prompt without letting one segment fill a stage.
_QUALIFIED_PER_VALUE = 2



def _phrase(term: str, keep_case: bool = False) -> str:
    """A term as it would sit inside a typed query.

    Title Case reads wrong mid-sentence ("software with Approval Workflows"), but
    lowercasing wholesale destroys the terms buyers actually type: ASC 606, ARR, SOC 2,
    PCI-DSS, and the brands they are leaving - known_replaces on ordwaylabs.com holds
    "QuickBooks invoicing", which must not become "quickbooks invoicing". A word that is
    all capitals, carries a digit, or has a capital inside it keeps its case; the rest are
    lowered. Brand-only fields pass keep_case and skip the question entirely.
    """
    term = (term or "").strip()
    if not term or keep_case:
        return term
    return " ".join(
        w if (_ACRONYMISH.match(w) or _INNER_CAPITAL.search(w)) else w.lower()
        for w in term.split())


@dataclass
class Prompt:
    """One prompt, and the single piece of the ontology it was built from."""
    text: str
    stage: str
    source_field: str
    source_value: str
    grounding: str
    source_url: str = ""
    quote: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PromptSet:
    vertical_id: str
    display_name: str
    domain: str
    prompts: List[Prompt] = field(default_factory=list)
    coverage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vertical_id": self.vertical_id,
            "display_name": self.display_name,
            "domain": self.domain,
            "prompts": [p.to_dict() for p in self.prompts],
            "coverage": self.coverage,
        }

    def by_stage(self, stage: str) -> List["Prompt"]:
        return [p for p in self.prompts if p.stage == stage]


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
# Grouped by the role a subject plays rather than the field it came from, because one
# field feeds several roles: a process concept is both something to automate (problem) and
# something to shop for (solution). Every template has to survive the shapes these values
# really take - known_replaces on ordwaylabs.com held "Spreadsheets", "QuickBooks
# invoicing" and "20-year-old homegrown billing system" - so no template puts an article
# in front of its subject.

_TEMPLATES: Dict[str, List[Tuple[str, str]]] = {
    "replaced": [
        ("problem", "how to move off {x}"),
        ("problem", "problems with {x}"),
        ("problem", "replacing {x}"),
        ("problem", "why {x} stops working as you grow"),
        ("problem", "{x} alternatives"),
    ],
    "process": [
        ("problem", "how to automate {x}"),
        ("problem", "why is {x} still manual"),
        ("problem", "{x} takes too long"),
        ("solution", "{x} automation software"),
    ],
    "feature": [
        ("solution", "software with {x}"),
        ("solution", "{x} software"),
        ("solution", "how does {x} work"),
    ],
    "capability": [
        ("solution", "best {x} software"),
        ("solution", "{x} tools"),
        ("solution", "what is {x}"),
    ],
    # Bought from a firm, not run in software: "best contractor of record software" is not
    # a query, "contractor of record services" is.
    "provider": [
        ("solution", "best {x} providers"),
        ("solution", "{x} services"),
        ("solution", "what is {x}"),
    ],
    # A thing the category handles without being sold as one - a plan, a tax, a record:
    # "best unemployment insurance software" is not a query, "how does ... work" is.
    "term": [
        ("solution", "what is {x}"),
        ("solution", "how does {x} work"),
    ],
    # Sits under compliance but names no rule: nobody complies with worker
    # misclassification, they ask how their software keeps them clear of it.
    "compliance_topic": [
        ("validation", "how does {category} software handle {x}"),
        ("solution", "what is {x}"),
    ],
    "grouping": [
        ("solution", "what is {x}"),
        ("solution", "{x} explained"),
    ],
    "standard": [
        ("validation", "{x} compliant {category} software"),
        ("validation", "how to comply with {x}"),
        ("validation", "does {category} software need {x}"),
    ],
    "pricing": [
        ("validation", "billing software that supports {x}"),
        ("validation", "how to charge with {x}"),
    ],
    # A body of rules rather than one rule: nobody's software is "State Labor Laws
    # compliant", and "does HR software need IRS Regulations" asks the wrong question. A
    # buyer asks how the software keeps them inside the rules.
    "rule_body": [
        ("validation", "how does {category} software handle {x}"),
        ("validation", "how to comply with {x}"),
    ],
    "integration": [
        ("validation", "{category} software that integrates with {x}"),
        ("validation", "{x} integration"),
    ],
    # A kind of software the buyer already runs, not a product with a connector: "HR
    # software that integrates with HRIS Systems" is not a query, "HR software that
    # integrates with your HRIS" is.
    "integration_category": [
        ("validation", "{category} software that integrates with your {x}"),
        ("validation", "{x} integration"),
    ],
    # A firm the buyer already works with. Nothing integrates with a benefits broker, but
    # the software has to work with the one they have.
    "integration_provider": [
        ("validation", "{category} software that works with your {x}"),
        ("validation", "{x} integration"),
    ],
    "competitor": [
        ("comparison", "{x} alternatives"),
        ("comparison", "best alternative to {x}"),
        ("comparison", "switching from {x}"),
        ("comparison", "{x} pricing"),
    ],
}



_PRODUCT_WORD = re.compile(r"\s+(software|platforms?|tools?|solutions?|systems?)$", re.IGNORECASE)


def _category_noun(onto: IndustryOntologyModel, tree: _Tree) -> str:
    """The noun standing for the whole category inside a template."""
    root = tree.root_label()
    if root:
        # Every template that takes the noun says "software" after it, so a root named for
        # the product ("HR software") read "does HR software software need ...".
        return _phrase(_PRODUCT_WORD.sub("", root).strip() or root)
    # No concept layer at all, so the display name is all that is left, and its first half
    # is the topic: "Subscription Billing & Revenue Automation" -> "subscription billing".
    # A market marker is not part of the noun.
    head = (onto.display_name or "").split("&")[0].strip()
    head = re.sub(r"^(B2B|B2C|Enterprise|Cloud)\s+", "", head, flags=re.IGNORECASE)
    return _phrase(head) or "software"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _evidence_for(profile: Dict[str, Any], field_name: str,
                  value: str) -> Tuple[str, str, str]:
    """(grounding, source_url, quote) for one buyer value.

    A value present in icp_evidence was quoted from a page discovery actually read; one
    that is not was proposed and never confirmed. Segments carry a weaker guarantee than
    the other fields by design (_FIELD_SUPPORT_RATIO in industry_profiler), which is why
    the distinction is reported rather than hidden.
    """
    proof = ((profile.get("icp_evidence") or {}).get(field_name) or {}).get(value) or {}
    if proof.get("source_url"):
        return "evidenced", proof.get("source_url", ""), proof.get("quote", "")
    return "proposed", "", ""


_STOPWORDS = {"how", "to", "is", "the", "why", "does", "what", "with", "for", "still",
              "best", "as", "you", "off", "on", "and", "of", "it", "need", "stops",
              "takes", "too", "long", "grow", "move", "from", "that", "vs"}


def _collides(template: str, subject: str) -> bool:
    """Whether a template would say the subject's own word back to it.

    An alt label often already contains the word the template was going to add: "ASC 606
    Compliance" through "{x} compliant ..." reads "ASC 606 compliance compliant ...", and
    "AR Automation" through "{x} automation software" reads "AR automation automation
    software". Compared on a six-character stem, so compliance/compliant and
    automate/automation collide without needing a list of word pairs. A final y is read as
    the i it becomes before a suffix, so comply meets compliance: "how to comply with HR
    compliance" was the one pair the stem alone let through.

    "{category} software" is the category's name, not a word added to the subject, so it
    is left out: "HR software that integrates with your accounting software" says
    software twice and means it, and reading it as a collision had dropped every
    integration prompt for Accounting Software and Expense Management Software.
    """
    subject_stems = {_stem(w) for w in re.findall(r"[A-Za-z]+", subject)}
    template = template.replace("{category} software", " ")
    for word in re.findall(r"[a-z]+", re.sub(r"\{\w+\}", " ", template)):
        if word not in _STOPWORDS and _stem(word) in subject_stems:
            return True
    return False


def _stem(word: str) -> str:
    word = word.lower()
    return (word[:-1] + "i" if word.endswith("y") else word)[:6]




def _names_one_of(value: str, names: List[str]) -> bool:
    """Whether a value names one of these, as a whole word."""
    low = (value or "").lower()
    return any(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(n.lower()), low)
               for n in names if n and n.strip())




def _emit(out: List[Prompt], role: str, subject: str, source_field: str, grounding: str,
          category: str, keep_case: bool = False, source_url: str = "",
          quote: str = "", source_value: str = "") -> None:
    """Fill every template for a role with one subject."""
    if role == "standard" and _is_rule_body(subject):
        # Decided here rather than per caller, because a seed string and a learned alt
        # label reach it alike. A class of rules is a common noun, so it is lowered too:
        # "state labor laws", but still "IRS regulations".
        role, keep_case = "rule_body", False
    text_subject = _phrase(subject, keep_case=keep_case)
    if not text_subject:
        return
    for stage, template in _TEMPLATES.get(role, []):
        if "{category}" in template and not category:
            continue
        if _collides(template, subject):
            continue
        out.append(Prompt(
            text=template.format(x=text_subject, category=category),
            stage=stage,
            source_field=source_field,
            source_value=source_value or subject,
            grounding=grounding,
            source_url=source_url,
            quote=quote,
        ))


def _rank(p: Prompt) -> Tuple[int, int]:
    return (STAGES.index(p.stage) if p.stage in STAGES else len(STAGES),
            GROUNDINGS.index(p.grounding) if p.grounding in GROUNDINGS else len(GROUNDINGS))


def _dedupe(prompts: List[Prompt]) -> List[Prompt]:
    """One prompt per wording, keeping the best-grounded copy of it.

    Two sources reach the same words often: "Zuora alternatives" is a competitor prompt,
    and on a site that also names Zuora in known_replaces it is a problem prompt. The copy
    carrying a quote is the one worth keeping.
    """
    best: Dict[str, Prompt] = {}
    for p in prompts:
        key = re.sub(r"\s+", " ", p.text.strip().lower())
        current = best.get(key)
        if current is None or _rank(p) < _rank(current):
            best[key] = p
    return list(best.values())


def _spread(prompts: List[Prompt], limit: int) -> List[Prompt]:
    """Take the best of each stage in turn, rather than the best overall.

    Sorting by stage and cutting at the limit returned one stage and nothing else: a
    curated vertical generates around 960 prompts, of which the first 40 were all
    problem-stage, so the comparison, validation and solution halves of the buyer's search
    never appeared. Going round the stages in order keeps the set spanning the journey and
    still leads with the most committed prompt, and a stage that runs out gives its turns
    back to the others.
    """
    # No limit means every prompt, still spread. Ordering is a property of the set, not
    # of whether a caller asked for a slice of it, and returning an unspread list when
    # limit was 0 made a single-stage request open with four wordings of one competitor.
    limit = limit if limit and limit > 0 else len(prompts)
    queues = {s: _by_subject(p for p in prompts if p.stage == s) for s in STAGES}
    kept: List[Prompt] = []
    while len(kept) < limit and any(queues.values()):
        for stage in STAGES:
            if queues[stage] and len(kept) < limit:
                kept.append(queues[stage].pop(0))
    return kept


def _by_subject(prompts) -> List[Prompt]:
    """Reorder so consecutive prompts are about different things.

    Within a stage every prompt ranks the same, so the alphabetical tie-break decided the
    cut: the comparison stage came back as nine wordings of "Maxio (SaaSOptics)" and the
    solution stage as nine features beginning with A. Taking one prompt per subject in
    turn shows the breadth that was generated instead of the first letter of it.
    """
    order: List[str] = []
    grouped: Dict[str, List[Prompt]] = {}
    for p in prompts:
        if p.source_value not in grouped:
            grouped[p.source_value] = []
            order.append(p.source_value)
        grouped[p.source_value].append(p)
    out: List[Prompt] = []
    while any(grouped.values()):
        for value in order:
            if grouped[value]:
                out.append(grouped[value].pop(0))
    return out


def generate_prompts(vertical_id: str, domain: Optional[str] = None, limit: int = 60,
                     root: Optional[str] = None, stage: Optional[str] = None) -> PromptSet:
    """Prompts an ICP buyer of this vertical would type, ranked by intent then by proof.

    `domain` names a site whose buyer profile has been discovered; without one the buyer
    half is simply absent and the set is built from the vertical's vocabulary alone, which
    coverage["missing"] says out loud. Reads local files only.

    `stage` narrows to one stage of the search. It is applied before the limit, and
    before the subjects are spread, so asking for one stage returns as many prompts as
    were asked for and they are still about different things.
    """
    onto = load_industry_ontology(vertical_id)
    tree = _Tree(onto.concepts or [])
    category = _category_noun(onto, tree)

    profile = (buyer_profiles.load(domain, root=root) if domain else None) or {}
    out: List[Prompt] = []

    # The buyer half: what this company's customers were doing before, and instead of
    # whom. This is the material worth having - it is specific to one company, and each
    # value either carries the page it was quoted from or is marked unproven.
    competitors = [c for c in (profile.get("known_competitors") or []) if c]
    for value in profile.get("known_replaces") or []:
        # A competitor landing here is the comparison stage's subject, not a problem: on
        # bamboohr.com discovery filed a jab at Hibob as something buyers replace, and it
        # came out as "how to move off 2010 software disguised as culture (referring to
        # hibob)" - evidenced, because the jab really is on the page.
        if _names_one_of(value, competitors):
            continue
        grounding, url, quote = _evidence_for(profile, "known_replaces", value)
        _emit(out, "replaced", value, "known_replaces", grounding, category,
              source_url=url, quote=quote)

    for value in competitors:
        grounding, url, quote = _evidence_for(profile, "known_competitors", value)
        _emit(out, "competitor", value, "known_competitors", grounding, category,
              keep_case=True, source_url=url, quote=quote)
    # A head-to-head is a real query, and it only exists between two named competitors.
    for left, right in zip(competitors, competitors[1:]):
        grounding, url, quote = _evidence_for(profile, "known_competitors", left)
        out.append(Prompt(text="%s vs %s" % (left, right), stage="comparison",
                          source_field="known_competitors", source_value=left,
                          grounding=grounding, source_url=url, quote=quote))

    # The vertical's own vocabulary. A curated vertical reaches here with a typed, defined
    # concept tree; an auto-discovered one with a handful of seed strings.
    for concept in onto.concepts or []:
        role = _role(concept, tree)
        source_field = "concept:%s" % (concept.kind or "concept")
        _emit(out, role, concept.pref_label, source_field, "ontology", category)
        # An alt label is how a buyer writes the term when they do not use the house word.
        # Nobody types "ARR" and "Annual Recurring Revenue" interchangeably.
        # An inflection is not another way of writing the term: learned alt labels keep
        # every form a site wrote, and "what is payroll processes" is not a query.
        used = {_variant(concept.pref_label)}
        for alt in concept.alt_labels or []:
            if len(used) > 2:
                break
            if _variant(alt) in used:
                continue
            # A merged alt label is often the work around a rule rather than another name
            # for it: COBRA compliance holds "COBRA administration".
            if role == "standard" and not _names_a_rule(alt):
                continue
            used.add(_variant(alt))
            _emit(out, role, alt, source_field, "ontology", category)

    named = {(c.pref_label or "").lower() for c in (onto.concepts or [])}
    # Every label the concept layer answers to, for telling whether a seed's expansion
    # would only repeat it.
    known = {_variant(label) for c in (onto.concepts or [])
             for label in [c.pref_label] + list(c.alt_labels or []) if label}
    for value in onto.core_seed_concepts or []:
        # A seed term the concept layer already defines is covered above, with a kind.
        if (value or "").lower() in named:
            continue
        _emit(out, "capability", value, "core_seed_concepts", "ontology", category)
    for source_field, values in (("known_compliance", onto.known_compliance),
                                 ("known_integrations", onto.known_integrations)):
        for value in values or []:
            if (value or "").lower() in named:
                continue
            for subject, role, keep_case in _seed_subjects(value, source_field, known):
                _emit(out, role, subject, source_field, "ontology", category,
                      keep_case=keep_case, source_value=value)

    # Qualify the strongest prompts by who is buying. "billing software" is a category;
    # "billing software for equipment rental companies" is a buyer.
    qualifiers = []
    for field_name in ("known_segments", "known_industries"):
        for value in profile.get(field_name) or []:
            # A segment is often written as a description rather than a name - "Equipment
            # lifecycle software company in the construction industry" is how discovery
            # found it, and "Maxio alternatives for equipment lifecycle software company
            # in the construction industry" is not a query anyone types. Short ones are
            # the names ("SaaS", "Fintech", "Digital estate planning SaaS").
            if 1 <= len(value.split()) <= _QUALIFIER_MAX_WORDS:
                grounding, url, quote = _evidence_for(profile, field_name, value)
                qualifiers.append((field_name, value, grounding, url, quote))
    if qualifiers:
        # Only solution and comparison prompts take a qualifier well. "how to move off
        # spreadsheets for SaaS companies" is not a sentence anyone types.
        base = _by_subject(sorted((p for p in _dedupe(out)
                                   if p.stage in ("solution", "comparison")
                                   and _IS_SHOPPING.search(p.text)),
                                  key=_rank))
        # One qualifier per base prompt, walked in step, so the qualified set varies in
        # both halves instead of repeating one competitor against every segment.
        for index, (field_name, value, grounding, url, quote) in enumerate(qualifiers):
            for p in base[index::len(qualifiers)][:_QUALIFIED_PER_VALUE]:
                out.append(Prompt(
                    text="%s for %s" % (p.text, _phrase(value, keep_case=True)),
                    stage=p.stage,
                    source_field="%s+%s" % (p.source_field, field_name),
                    source_value=value,
                    grounding=grounding,
                    source_url=url,
                    quote=quote,
                ))

    # Sorted on rank alone, never on the text. Python's sort is stable, so within one
    # rank the emission order survives - and that is template order, which _TEMPLATES
    # declares best first. Adding the text as a tie-break sorted alphabetically instead,
    # so every process concept surfaced through "{x} takes too long" purely because it
    # starts with a capital letter, while "how to automate {x}" never appeared.
    prompts = sorted(_dedupe(out), key=_rank)
    if stage:
        prompts = [p for p in prompts if p.stage == stage]
    kept = _spread(prompts, limit)

    return PromptSet(
        vertical_id=onto.vertical_id,
        display_name=onto.display_name,
        domain=buyer_profiles.domain_key(domain) if domain else "",
        prompts=kept,
        coverage=_coverage(onto, tree, profile, domain, prompts, kept),
    )


def _coverage(onto: IndustryOntologyModel, tree: _Tree, profile: Dict[str, Any],
              domain: Optional[str], all_prompts: List[Prompt],
              kept: List[Prompt]) -> Dict[str, Any]:
    """What the generator had to work with, and what it did not.

    "missing" is the point of this: the difference between a curated vertical and a cold
    one, written as the work that would close it. Two of eleven verticals carry a concept
    layer, so for most domains this list is the honest answer to "why is the output thin?".
    """
    concepts = onto.concepts or []
    defined = [c for c in concepts if (c.definition or "").strip()]
    aliased = sum(len(c.alt_labels or []) for c in concepts)

    buyer_fields: Dict[str, Any] = {}
    for field_name in buyer_profiles.BUYER_FIELDS:
        values = profile.get(field_name) or []
        proven = (profile.get("icp_evidence") or {}).get(field_name) or {}
        buyer_fields[field_name] = {
            "values": len(values),
            "evidenced": sum(1 for v in values if v in proven),
        }

    missing: List[str] = []
    if not domain:
        missing.append(
            "No domain given, so no buyer profile was read. The problem and comparison "
            "stages come entirely from a site's own buyer evidence.")
    elif not profile:
        missing.append(
            "No buyer profile stored for %s. Run discovery on it to get problem-stage "
            "prompts (what its customers are leaving behind) and competitor prompts."
            % buyer_profiles.domain_key(domain))
    if not concepts:
        missing.append(
            "This vertical has no concept layer, so prompts fall back to %d seed terms "
            "with no definitions, no kinds and no hierarchy. Curating concepts is what "
            "separates a specific prompt from a generic one."
            % len(onto.core_seed_concepts or []))
    else:
        if len(defined) < len(concepts):
            missing.append("%d of %d concepts carry no definition."
                           % (len(concepts) - len(defined), len(concepts)))
        if not aliased:
            missing.append(
                "No alt labels, so only the house word for each concept is generated - a "
                "buyer typing the industry's other name for it is not covered.")
    if not (profile.get("known_competitors") or []):
        missing.append(
            "No competitors known, so the comparison stage is empty. Competitor names "
            "come from comparison pages, which many sites do not publish.")

    return {
        "ontology_depth": {
            "concepts": len(concepts),
            "with_definition": len(defined),
            "alt_labels": aliased,
            "leaf_concepts": sum(1 for c in concepts if not tree.is_grouping(c)),
            "kinds": {k: sum(1 for c in concepts if c.kind == k)
                      for k in sorted({c.kind for c in concepts if c.kind})},
            "seed_terms": len(onto.core_seed_concepts or []),
        },
        "buyer_profile": {
            "present": bool(profile),
            "fields": buyer_fields,
        },
        "generated": len(all_prompts),
        "returned": len(kept),
        "by_stage": {s: sum(1 for p in kept if p.stage == s) for s in STAGES},
        "by_grounding": {g: sum(1 for p in kept if p.grounding == g) for g in GROUNDINGS},
        "missing": missing,
    }
