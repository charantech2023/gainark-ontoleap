"""
concept_roles.py - what sort of thing each term in a vertical is.

A concept's label alone does not say how it is used. "Contractor of Record" is hired,
"Applicant tracking system" is bought, "ACA compliance" is complied with, and "Payroll"
heads a branch of the tree. Two readers need the answer and must agree on it: the prompt
generator, which fills a different template for each, and claim extraction, which decides
what a sentence speaking for a site is claiming. They used to disagree, because only the
prompt generator read the concept layer; extraction matched every vertical against the
billing lists in constants.py, so an HR site's pages were searched for "Overage Pricing"
and never for payroll.

Everything here reads the vertical and nothing else: no model, no network.
"""

import re
from typing import Dict, List, Optional, Tuple

from models import IndustryConcept

ACRONYMISH = re.compile(r"^[A-Z0-9][A-Z0-9&/.-]*$")
# A capital anywhere but the front is a brand's own spelling: QuickBooks, NetSuite,
# SaaSOptics. Title Case alone is not - "Spreadsheets" is just a word at the start of a
# label - so only an inner capital protects a word from being lowered.
INNER_CAPITAL = re.compile(r".[A-Z]")


# A concept whose ancestry reaches compliance is a box to tick, not a capability to shop
# for: "best accounting standards software" is not a thing anyone types, while "ASC 606
# compliant revenue operations software" is. Found by walking broader, so it follows the
# tree rather than a list of words.
_COMPLIANCE_HINTS = ("compliance", "standard", "regulation", "privacy")


class ConceptTree:
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


def role(concept: IndustryConcept, tree: ConceptTree) -> str:
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
        return "standard" if names_a_rule(concept.pref_label) else "compliance_topic"
    if tree.is_grouping(concept):
        return "grouping"
    return capability_role(concept.definition)


# Words that make a label the name of something to comply with, not an activity or risk.
_RULE_WORDS = re.compile(r"\b(complian\w*|regulations?|laws?|acts?|standards?|rules?|"
                         r"frameworks?|principles|certifications?)\b", re.IGNORECASE)


def names_a_rule(label: str) -> bool:
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
    return all(ACRONYMISH.match(w) or INNER_CAPITAL.search(w) for w in words)


# Only the plural: "Labor law compliance" and "HIPAA Security Rule" name one rule.
_RULE_BODY = re.compile(r"\b(regulations|laws|rules|standards|requirements|guidelines)$",
                        re.IGNORECASE)


def is_rule_body(label: str) -> bool:
    """Whether a label names a body of rules rather than one rule.

    names_a_rule says yes to "IRS Regulations" and "State Labor Laws", because they are
    rules, and the standard templates made "State Labor Laws compliant HR software" of
    them. A plural is a class of rules, which the buyer is kept inside of, not certified
    against. The learned "labor laws" alt label on Labor law compliance was the same case.
    """
    return bool(_RULE_BODY.search((label or "").strip()))


# The head of a definition's first phrase says what sort of thing the concept is. Curated
# and learned definitions alike open that way: "Software that ...", "A third party that
# ...", "Recording the hours ...". Only the first few words are read, so a noun later in
# the sentence ("... to fund benefits for workers") cannot decide it.
_GENUS_WORDS = 6
_SOFTWARE_GENUS = re.compile(r"\b(software|systems?|platforms?|tools?|applications?|apps?)\b",
                             re.IGNORECASE)
_PROVIDER_GENUS = re.compile(r"\b(third[- ]party|organi[sz]ation|company|firm|provider|"
                             r"agency|service)\b", re.IGNORECASE)


def capability_role(definition: Optional[str]) -> str:
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


def variant(term: str) -> str:
    """One key for a term's inflections: each word cut to its first seven letters, the
    same folding vocabulary learning uses. "payroll processing" and "payroll processes"
    meet; so do "benefits administration" and "benefits administrators". A plural s is
    dropped first, which a seven-letter cut never reaches on a short word: "EORs" is
    "EOR", and "best EORs providers" was a second prompt for one term."""
    return " ".join((w[:-1] if len(w) > 2 and w.endswith("s") and not w.endswith("ss") else w)[:7]
                    for w in re.findall(r"[a-z0-9]+", (term or "").lower()))


# ---------------------------------------------------------------------------
# Seed strings
# ---------------------------------------------------------------------------
# Discovery writes known_compliance and known_integrations as descriptions of the thing,
# not its name. hr_payroll_benefits holds "FLSA (Fair Labor Standards Act)", "Accounting
# Software (e.g., QuickBooks, Xero)" and "HRIS Systems", and each went straight into a
# template: "FLSA (Fair Labor Standards Act) compliant HR software". A seed is read into
# the subjects a buyer would type before any template sees it.

_EXAMPLES = re.compile(r"\s*\(\s*(?:e\.?\s?g\.?|for example|such as|including|like)\s*[,:]?"
                       r"\s*([^()]*)\)", re.IGNORECASE)
# A space before the bracket, so "401(k) Providers" is not read as "401" expanded by "k".
_PAIRED = re.compile(r"^(.*\S)\s+\(([^()]+)\)$")
_SOFTWARE_CATEGORY = re.compile(r"\s+(software|systems|platforms|tools|applications|apps|"
                                r"solutions)$", re.IGNORECASE)
_PROVIDER_CATEGORY = re.compile(r"\s+(providers|brokers|vendors|carriers|partners|agencies|"
                                r"firms|services)$", re.IGNORECASE)


def _abbreviates(short: str, long: str) -> bool:
    """Whether short is an acronym of long: its letters are long's initials, in order.

    In order rather than equal, because an acronym skips the small words - HIPAA is
    Health Insurance Portability (and) Accountability Act. Tested on the letters rather
    than trusted from the brackets, since a bracket can hold anything: "Maxio
    (SaaSOptics)" is a former name, not an expansion.
    """
    if not all(ACRONYMISH.match(w) for w in short.split()):
        return False
    letters = re.sub(r"[^A-Za-z]", "", short).upper()
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", long)).upper()
    if len(letters) < 2 or not initials or letters[0] != initials[0]:
        return False
    remaining = iter(initials)
    return all(ch in remaining for ch in letters)


def _integration_subject(term: str) -> Tuple[str, str]:
    """(role, subject) for one integration seed: a named product, or a kind of thing.

    A category is written in the plural - "HRIS Systems", "Benefits Brokers", "401(k)
    Providers" - or as software, which has no plural; a product is a name in the
    singular, so "Adobe Experience Platform" stays a product. The category comes back in
    the singular, as the one the buyer already has ("your applicant tracking system"),
    and a genus word after an acronym is dropped, since the acronym already says it:
    "HRIS Systems" is "your HRIS".
    """
    for pattern, role in ((_SOFTWARE_CATEGORY, "integration_category"),
                          (_PROVIDER_CATEGORY, "integration_provider")):
        m = pattern.search(term)
        if not m or not term[:m.start()].strip():
            continue
        head, genus = term[:m.start()].strip(), m.group(1)
        if all(ACRONYMISH.match(w) for w in head.split()):
            return role, head
        genus = (genus[:-3] + "y" if genus.lower().endswith("ies")
                 else genus if genus.lower() == "software" else genus[:-1])
        return role, "%s %s" % (head, genus)
    return "integration", term


def _looks_like_name(term: str) -> bool:
    """Every word capitalised, the way a product is written: "When I Work", "Xero"."""
    words = term.split()
    return bool(words) and all(w[0].isupper() or w[0].isdigit() for w in words)


def _read_seed(value: str) -> Tuple[Optional[str], str, List[str]]:
    """(acronym or None, full name, examples) for one seed string.

    "FLSA (Fair Labor Standards Act)" gives ("FLSA", "Fair Labor Standards Act", []);
    "Accounting Software (e.g., QuickBooks, Xero)" gives (None, "Accounting Software",
    ["QuickBooks", "Xero"]); "401(k) Providers" is left whole.
    """
    value = (value or "").strip()
    examples: List[str] = []
    for m in _EXAMPLES.finditer(value):
        examples += [e.strip(" .") for e in re.split(r",|\s+(?:and|or)\s+", m.group(1))]
    head = _EXAMPLES.sub("", value).strip()
    m = _PAIRED.match(head)
    if m:
        for short, full in ((m.group(2), m.group(1)), (m.group(1), m.group(2))):
            if _abbreviates(short.strip(), full.strip()):
                return short.strip(), full.strip(), examples
    return None, head, [e for e in examples if e]


def seed_subjects(value: str, field_name: str,
                   known: set) -> List[Tuple[str, str, bool]]:
    """(subject, role, keep_case) for each thing a seed string names.

    Three shapes arrive, and each is reduced to what a buyer types:

    "FLSA (Fair Labor Standards Act)" is a name and its expansion. The acronym is what
    gets typed, so it always leads; the expansion follows only if nothing else already
    covers it - on the live HR tree "Affordable Care Act" is an alt label of ACA
    compliance, and "Applicant Tracking Systems" is the Applicant tracking system concept.

    "Accounting Software (e.g., QuickBooks, Xero)" is a category and some members of it.
    The list is not part of the name. The members that are names become subjects of
    their own, since "HR software that integrates with QuickBooks" is the sharpest
    integration query there is.

    "HRIS Systems" is a category, not a product, and takes the category templates.
    """
    short, long, examples = _read_seed(value)
    names = [short or long]
    if short and variant(long) not in known | {variant(short)}:
        names.append(long)

    out: List[Tuple[str, str, bool]] = []
    if field_name == "known_integrations":
        # The acronym is the same kind of thing as what it stands for: ATS is a category
        # because Applicant Tracking Systems is.
        role, _ = _integration_subject(long)
        for name in names:
            kind, subject = _integration_subject(name)
            role_for = kind if kind != "integration" else role
            out.append((subject, role_for, role_for == "integration"))
        for example in examples:
            kind, subject = _integration_subject(example)
            if kind != "integration" or _looks_like_name(example):
                out.append((subject, kind, kind == "integration"))
    else:
        out += [(name, "standard", True) for name in names]
        out += [(e, "standard", True) for e in examples if names_a_rule(e)]
    return [s for s in out if s[0]]


# ---------------------------------------------------------------------------
# What a sentence can claim
# ---------------------------------------------------------------------------
# A claim is "<site> predicate <concept>", and which predicate depends on what the concept
# is. The mapping follows role() so extraction and prompts read a concept the same way:
# what is sold (a feature, a capability, a service, a branch of the product) is had; a
# process is automated; a pricing model is supported; a rule is complied with. A
# compliance topic and a term are neither: "worker misclassification" in a sentence about
# Deel is a risk it keeps customers clear of, not a thing it has.
_CLAIM_PREDICATES = {
    "feature": "hasFeature", "capability": "hasFeature", "provider": "hasFeature",
    "grouping": "hasFeature", "process": "automates", "pricing": "supportsPricingModel",
    "standard": "compliesWith",
}


def claim_vocabulary(onto) -> Dict[str, List[Tuple[str, List[str]]]]:
    """{predicate: [(target label, [forms a page may write])]} for one vertical.

    Built from the concept layer, so a site is read for its own vertical's terms: the
    18 Sep 2026 crawls of gusto.com, rippling.com and deel.com (378 pages) were read for
    the billing lists in constants.py and came back with "Overage Pricing", "Automated
    Invoicing" and not one claim about payroll or benefits. The target is the concept's
    prefLabel, which the resolver turns into the concept's URI, so the same claim on two
    sites lands on the same resource.

    Forms are the prefLabel and alt labels, longest first. Left out:
      - a form two concepts share, since the page cannot say which one it meant;
      - the tree's root: every site in the vertical claims it, so it separates no one;
      - a plural heading as a rule: "Compliance Standards" is a shelf, not a
        certification, while "Labor law compliance" is a rule however much sits under it;
      - an alt label of a rule that names the work, not the rule ("COBRA administration").
    Integrations and compliance seeds are read into names first (seed_subjects), since
    "Accounting Software (e.g., QuickBooks, Xero)" is written on no page.

    An empty dict means the vertical has no concept layer; the caller keeps its fallback.
    """
    concepts = [c for c in (getattr(onto, "concepts", None) or []) if c.pref_label]
    if not concepts:
        return {}
    tree = ConceptTree(concepts)
    owners: Dict[str, set] = {}
    for c in concepts:
        for form in [c.pref_label] + list(c.alt_labels or []):
            owners.setdefault(variant(form), set()).add(c.id)

    out: Dict[str, List[Tuple[str, List[str]]]] = {}
    for c in concepts:
        kind = role(c, tree)
        predicate = _CLAIM_PREDICATES.get(kind)
        if predicate is None or not c.broader:
            continue
        if kind == "standard" and tree.is_grouping(c) and is_rule_body(c.pref_label):
            continue
        forms = []
        for form in [c.pref_label] + list(c.alt_labels or []):
            if len(owners.get(variant(form), ())) > 1 or form in forms:
                continue
            if kind == "standard" and form != c.pref_label and not names_a_rule(form):
                continue
            forms.append(form)
        if forms:
            out.setdefault(predicate, []).append(
                (c.pref_label, sorted(forms, key=len, reverse=True)))

    for field_name, predicate in (("known_integrations", "integratesWith"),
                                  ("known_compliance", "compliesWith")):
        for target, forms in _seed_claims(getattr(onto, field_name, None) or [], field_name):
            out.setdefault(predicate, []).append((target, forms))
    return out


def _seed_claims(values: List[str], field_name: str) -> List[Tuple[str, List[str]]]:
    """(target, forms) for each thing a list of seed strings names.

    One target per thing, however many ways the seed writes it: "FLSA (Fair Labor
    Standards Act)" is one law, claimed as FLSA whichever of the two a page uses, where
    the prompt generator wants both as separate things to type. An integration category
    is claimed under the singular the prompts use ("Benefits Broker") and matched in
    either number.
    """
    out: List[Tuple[str, List[str]]] = []
    for value in values:
        short, long, examples = _read_seed(value)
        forms = [f for f in (short, long) if f]
        target = short or long
        if field_name == "known_integrations":
            target = _integration_subject(target)[1]
            forms.append(target)
            examples = [e for e in examples
                        if _integration_subject(e)[0] != "integration" or _looks_like_name(e)]
        else:
            examples = [e for e in examples if names_a_rule(e)]
        if target:
            out.append((target, sorted(set(forms), key=len, reverse=True)))
        out += [(e, [e]) for e in examples]
    return out
