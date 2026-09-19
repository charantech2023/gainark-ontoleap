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
from industry_ontology import load_industry_ontology
from models import IndustryConcept, IndustryOntologyModel

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

_ACRONYMISH = re.compile(r"^[A-Z0-9][A-Z0-9&/.-]*$")
# A capital anywhere but the front is a brand's own spelling: QuickBooks, NetSuite,
# SaaSOptics. Title Case alone is not - "Spreadsheets" is just a word at the start of a
# label - so only an inner capital protects a word from being lowered.
_INNER_CAPITAL = re.compile(r".[A-Z]")


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
    "integration": [
        ("validation", "{category} software that integrates with {x}"),
        ("validation", "{x} integration"),
    ],
    "competitor": [
        ("comparison", "{x} alternatives"),
        ("comparison", "best alternative to {x}"),
        ("comparison", "switching from {x}"),
        ("comparison", "{x} pricing"),
    ],
}

# A concept whose ancestry reaches compliance is a box to tick, not a capability to shop
# for: "best accounting standards software" is not a thing anyone types, while "ASC 606
# compliant revenue operations software" is. Found by walking broader, so it follows the
# tree rather than a list of words.
_COMPLIANCE_HINTS = ("compliance", "standard", "regulation", "privacy")


class _Tree:
    """The concept hierarchy, read once."""

    def __init__(self, concepts: List[IndustryConcept]):
        self.by_id = {c.id: c for c in concepts if c.id}
        self.children: Dict[str, List[str]] = {}
        for c in concepts:
            if c.broader:
                self.children.setdefault(c.broader, []).append(c.id)

    def is_grouping(self, concept: IndustryConcept) -> bool:
        """A concept that others sit under. Its name is a heading, not a product."""
        return bool(self.children.get(concept.id))

    def ancestry(self, concept: IndustryConcept) -> List[str]:
        seen: set = set()
        out: List[str] = []
        node: Optional[IndustryConcept] = concept
        while node is not None and node.id not in seen:
            seen.add(node.id)
            out.append(node.id)
            node = self.by_id.get(node.broader or "")
        return out

    def under_compliance(self, concept: IndustryConcept) -> bool:
        chain = " ".join(self.ancestry(concept))
        return any(hint in chain for hint in _COMPLIANCE_HINTS)

    def root_label(self) -> str:
        """The widest concept in the tree, used as the category noun in templates.

        Taken from the ontology rather than guessed off the display name, which is
        marketing copy: "B2B SaaS & Financial Software" yields no usable noun, while that
        vertical's tree roots at "Revenue Operations".

        A compliance heading is never the category, however many standards sit under it: a
        learned HR tree with no single root put five under HR compliance, and every
        standard prompt read "does HR compliance software need payroll compliance".
        """
        roots = [c for c in self.by_id.values() if not c.broader]
        roots = [c for c in roots if not self.under_compliance(c)] or roots
        if not roots:
            return ""
        roots.sort(key=lambda c: -len(self.children.get(c.id, [])))
        return roots[0].pref_label


def _role(concept: IndustryConcept, tree: _Tree) -> str:
    """Which family of templates a concept can fill.

    A curator's kind wins over where the concept sits: cybersecurity files Risk Assessment
    (a process) and Compliance Automation (a feature) under GRC, and "how to comply with
    risk assessment" is not a query. Ancestry only decides for a concept with no kind of
    its own, which is every learned one.
    """
    if concept.kind == "standard":
        return "standard"
    if concept.kind in ("pricing", "process", "feature"):
        return concept.kind
    if tree.under_compliance(concept):
        return "standard" if _names_a_rule(concept.pref_label) else "compliance_topic"
    if tree.is_grouping(concept):
        return "grouping"
    return _capability_role(concept.definition)


# Words that make a label the name of something to comply with, not an activity or risk.
_RULE_WORDS = re.compile(r"\b(complian\w*|regulations?|laws?|acts?|standards?|rules?|"
                         r"frameworks?|principles|certifications?)\b", re.IGNORECASE)


def _names_a_rule(label: str) -> bool:
    """Whether a label names a rule a buyer must comply with.

    Either it says so ("Labor law compliance", "HIPAA Security Rule"), carries a number
    the way standards do ("SOC 2", "CMMC 2.0"), or is a name and nothing else ("COBRA",
    "FedRAMP"). "COBRA administration" is none of these: it is the work, not the law, and
    "how to comply with COBRA administration" is not a query.
    """
    words = (label or "").split()
    if not words:
        return False
    if _RULE_WORDS.search(label) or any(re.search(r"\d", w) for w in words):
        return True
    return all(_ACRONYMISH.match(w) or _INNER_CAPITAL.search(w) for w in words)


# The head of a definition's first phrase says what sort of thing the concept is. Curated
# and learned definitions alike open that way: "Software that ...", "A third party that
# ...", "Recording the hours ...". Only the first few words are read, so a noun later in
# the sentence ("... to fund benefits for workers") cannot decide it.
_GENUS_WORDS = 6
_SOFTWARE_GENUS = re.compile(r"\b(software|systems?|platforms?|tools?|applications?|apps?)\b",
                             re.IGNORECASE)
_PROVIDER_GENUS = re.compile(r"\b(third[- ]party|organi[sz]ation|company|firm|provider|"
                             r"agency|service)\b", re.IGNORECASE)


def _capability_role(definition: Optional[str]) -> str:
    """Whether a leaf concept is shopped for as software, hired as a service, or looked up.

    Read off the definition, because the label alone cannot say: "Contractor of Record"
    and "Applicant tracking system" are both three capitalised words. With no definition
    there is nothing to go on, and the concept keeps the software templates it always had.
    """
    head = re.split(r"[:;,.]|\s(?:that|who|which)\s", (definition or "").strip(), maxsplit=1)[0]
    words = head.split()[:_GENUS_WORDS]
    if not words:
        return "capability"
    head = " ".join(words)
    if _SOFTWARE_GENUS.search(head) or words[0].lower().endswith("ing"):
        return "capability"
    if _PROVIDER_GENUS.search(head):
        return "provider"
    return "term"


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
    """
    subject_stems = {_stem(w) for w in re.findall(r"[A-Za-z]+", subject)}
    for word in re.findall(r"[a-z]+", re.sub(r"\{\w+\}", " ", template)):
        if word not in _STOPWORDS and _stem(word) in subject_stems:
            return True
    return False


def _stem(word: str) -> str:
    word = word.lower()
    return (word[:-1] + "i" if word.endswith("y") else word)[:6]


def _variant(term: str) -> str:
    """One key for a term's inflections: each word cut to its first seven letters, the
    same folding vocabulary learning uses. "payroll processing" and "payroll processes"
    meet; so do "benefits administration" and "benefits administrators". A plural s is
    dropped first, which a seven-letter cut never reaches on a short word: "EORs" is
    "EOR", and "best EORs providers" was a second prompt for one term."""
    return " ".join((w[:-1] if len(w) > 2 and w.endswith("s") and not w.endswith("ss") else w)[:7]
                    for w in re.findall(r"[a-z0-9]+", (term or "").lower()))


def _names_one_of(value: str, names: List[str]) -> bool:
    """Whether a value names one of these, as a whole word."""
    low = (value or "").lower()
    return any(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(n.lower()), low)
               for n in names if n and n.strip())


def _emit(out: List[Prompt], role: str, subject: str, source_field: str, grounding: str,
          category: str, keep_case: bool = False, source_url: str = "",
          quote: str = "") -> None:
    """Fill every template for a role with one subject."""
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
            source_value=subject,
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
    for role, values, source_field, keep_case in (
        ("capability", onto.core_seed_concepts, "core_seed_concepts", False),
        ("standard", onto.known_compliance, "known_compliance", True),
        ("integration", onto.known_integrations, "known_integrations", True),
    ):
        for value in values or []:
            # A seed term the concept layer already defines is covered above, with a kind.
            if (value or "").lower() in named:
                continue
            _emit(out, role, value, source_field, "ontology", category, keep_case=keep_case)

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
