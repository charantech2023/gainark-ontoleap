"""
GainARK OntoLeap - Generic Ontology Schema
==========================================
One schema, many products, many categories.

The schema (entity types and the relations between them) is generic and applies to
any B2B SaaS product. The *vocabulary* that fills those types is category-specific
and lives in the per-vertical JSON profiles under verticals/, loaded into
VerticalConfig. Ordway and Chargebee share this schema and the billing vocabulary;
a CRM product shares the schema and swaps the vocabulary.

Why this module exists
----------------------
Every relation was previously defined in three places that nothing kept in sync:

  1. constants.py         - the global fallback list (KNOWN_INTEGRATIONS, ...)
  2. VerticalConfig       - the per-vertical override field (known_integrations, ...)
  3. pipeline.py          - a hardcoded cue-rule block, and separately a hardcoded
                            GLiNER-label elif chain

Adding a relation meant editing all three and remembering a fourth thing: adding the
GLiNER label to every vertical JSON, or the entity branch silently never fires. That
failure is invisible at runtime - it looks like a product simply has no integrations.

RELATIONS below is the single declaration those three sites should agree with.
It does not change extraction behaviour on import; it makes the contract checkable
(see check_vertical_coverage) and gives drift/scoring code a way to ask what a
predicate means instead of hardcoding predicate strings.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Naming authority
#
# Concepts belong to the vertical ontology, NOT to whoever is being audited. They
# were previously minted as https://{client-domain}/concept/{SluggedLabel}, which made
# "Revenue Schedules" in an Ordway graph a different resource from "Revenue Schedules"
# in a Chargebee graph. Two things broke as a result: the shared schema existed only as
# a private copy per client, and no question could ever span companies - "which vendors
# cover revenue schedules" has no subject to ask about.
#
# So the namespace splits by what the thing actually is:
#   ontology concepts -> here, shared across every client in the vertical
#   instance data     -> stays under the client's own domain (their product, their
#                        claims, the pages those claims came from)
#
# Concepts are vertical-scoped for now. Some of them ("Single Sign-On", "Audit Trail")
# are generic enough to belong to a shared core above the verticals; splitting that out
# is a later decision and not required for concepts to join across clients.
# ---------------------------------------------------------------------------

ONTOLOGY_BASE = "https://gainark.com/ontoleap/ontology"


def scheme_uri(vertical_id: str) -> str:
    """URI of the vertical's SKOS ConceptScheme."""
    return "%s/%s" % (ONTOLOGY_BASE, vertical_id)


def concept_uri(vertical_id: str, concept_id: str) -> str:
    """URI of one concept, built from its frozen id rather than its current label."""
    return "%s/%s/concept/%s" % (ONTOLOGY_BASE, vertical_id, concept_id)


def slug_for_label(label: str) -> str:
    """Derive an id-shaped slug from a label.

    Only for minting a NEW concept id, or as a last resort for a label that has no
    concept record yet. Never call it to look up an existing concept: that is what
    made identity depend on spelling. Hyphens are preserved as separators so "Auto-Pay"
    and "Auto Pay" both give "auto-pay" instead of colliding on "autopay".
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower())
    return slug.strip("-") or "concept"

# ---------------------------------------------------------------------------
# Entity types
#
# SUBJECT is always the product under audit. Every relation points from it to one
# of the object types below. Keeping these as plain strings (not an Enum) matches
# how SemanticTriple already stores subject/object and avoids a serialisation layer.
# ---------------------------------------------------------------------------

PRODUCT = "Product"

INTEGRATION = "Integration"
COMPLIANCE_STANDARD = "ComplianceStandard"
PRICING_MODEL = "PricingModel"
AUTOMATION_CAPABILITY = "AutomationCapability"
FEATURE = "Feature"
SEGMENT = "Segment"
INDUSTRY = "Industry"
DEPLOYMENT_MODEL = "DeploymentModel"
CERTIFICATION = "Certification"
API_STANDARD = "APIStandard"
LOCALE = "Locale"
SLA = "SLA"
LEGACY_WORKFLOW = "LegacyWorkflow"
COMPETITOR = "Competitor"
CUSTOMER = "Customer"

OBJECT_TYPES = [
    INTEGRATION, COMPLIANCE_STANDARD, PRICING_MODEL, AUTOMATION_CAPABILITY,
    FEATURE, SEGMENT, INDUSTRY, DEPLOYMENT_MODEL, CERTIFICATION, API_STANDARD,
    LOCALE, SLA, LEGACY_WORKFLOW, COMPETITOR, CUSTOMER,
]


@dataclass(frozen=True)
class RelationSpec:
    """One relation in the schema, and everything the extractor needs to find it.

    predicate        : the predicate string written onto SemanticTriple.
    object_type      : which entity type the object of this relation is.
    vocab_field      : the VerticalConfig field holding this vertical's vocabulary.
    global_constant  : the constants.py fallback used when vocab_field is empty.
    gliner_labels    : NER labels that yield this predicate. A vertical whose
                       gliner_labels omit all of these gets no NER contribution for
                       this relation - rule-based cues only.
    evidence_required: True when a claim under this predicate should not be treated
                       as drift unless a page that could carry the evidence was
                       actually crawled. Compliance is the motivating case: flagging
                       "ASC 606 missing" is wrong if no /trust or /compliance page
                       was ever fetched.
    """
    predicate: str
    object_type: str
    vocab_field: str
    global_constant: str
    gliner_labels: Tuple[str, ...] = ()
    evidence_required: bool = False
    description: str = ""


# ---------------------------------------------------------------------------
# The 14 relations, as pipeline.py actually emits them today.
#
# This mirrors current behaviour deliberately. It is a description of the system,
# not a redesign of it - so it can be asserted against the pipeline in tests.
# ---------------------------------------------------------------------------

RELATIONS: List[RelationSpec] = [
    RelationSpec(
        predicate="automates",
        object_type=AUTOMATION_CAPABILITY,
        vocab_field="known_automation",
        global_constant="KNOWN_AUTOMATION",
        gliner_labels=("Billing Feature", "Automation Workflow"),
        description="A workflow the product performs without human intervention.",
    ),
    RelationSpec(
        predicate="integratesWith",
        object_type=INTEGRATION,
        vocab_field="known_integrations",
        global_constant="KNOWN_INTEGRATIONS",
        gliner_labels=(
            "Integration Partner", "Ecosystem Integration",
            "Healthcare Integration", "HR Integration",
        ),
        description="A third-party system the product connects to.",
    ),
    RelationSpec(
        predicate="compliesWith",
        object_type=COMPLIANCE_STANDARD,
        vocab_field="known_compliance",
        global_constant="KNOWN_COMPLIANCE",
        gliner_labels=(
            "Accounting Standard", "Security Standard", "Compliance Regulation",
            "Financial Standard", "Medical Standard", "HR Compliance",
        ),
        evidence_required=True,
        description="A regulation or accounting standard the product conforms to.",
    ),
    RelationSpec(
        predicate="supportsPricingModel",
        object_type=PRICING_MODEL,
        vocab_field="known_pricing",
        global_constant="KNOWN_PRICING",
        gliner_labels=("Pricing Model", "Pricing Structure", "Billing Model"),
        description="A monetisation model the product can bill under.",
    ),
    RelationSpec(
        predicate="hasFeature",
        object_type=FEATURE,
        vocab_field="known_features",
        global_constant="KNOWN_FEATURES",
        gliner_labels=(
            "Product Feature", "Benefits Administration", "Employee Benefit",
            "Payroll Service", "Tax Filing", "Tax Filing Service",
            "Time Tracking Feature", "Time Tracking Solution",
            "Vulnerability Management", "Threat Intelligence",
        ),
        description="A discrete capability the product ships.",
    ),
    RelationSpec(
        predicate="replacesWorkflow",
        object_type=LEGACY_WORKFLOW,
        vocab_field="known_replaces",
        global_constant="KNOWN_REPLACES",
        gliner_labels=("Legacy Workflow",),
        description="A manual process the product eliminates.",
    ),
    RelationSpec(
        predicate="targetsSegment",
        object_type=SEGMENT,
        vocab_field="known_segments",
        global_constant="KNOWN_SEGMENTS",
        gliner_labels=("Customer Segment",),
        description="A buyer size or type the product is sold to.",
    ),
    RelationSpec(
        predicate="servesIndustry",
        object_type=INDUSTRY,
        vocab_field="known_industries",
        global_constant="KNOWN_INDUSTRIES",
        gliner_labels=("Industry Vertical",),
        description="An industry the product is positioned for.",
    ),
    RelationSpec(
        predicate="deployedAs",
        object_type=DEPLOYMENT_MODEL,
        vocab_field="known_deployment",
        global_constant="KNOWN_DEPLOYMENT",
        gliner_labels=("Deployment Model",),
        description="How the product is hosted and delivered.",
    ),
    RelationSpec(
        predicate="certifiedBy",
        object_type=CERTIFICATION,
        vocab_field="known_certifications",
        global_constant="KNOWN_CERTIFICATIONS",
        gliner_labels=("Trust Certification",),
        evidence_required=True,
        description="An audited attestation the product holds.",
    ),
    RelationSpec(
        predicate="hasAPI",
        object_type=API_STANDARD,
        vocab_field="known_api_types",
        global_constant="KNOWN_API_TYPES",
        gliner_labels=("API Standard",),
        description="An integration protocol the product exposes.",
    ),
    RelationSpec(
        predicate="supportsLocale",
        object_type=LOCALE,
        vocab_field="known_locales",
        global_constant="KNOWN_LOCALES",
        gliner_labels=("Geographic Market",),
        description="A region or market the product operates in.",
    ),
    RelationSpec(
        predicate="guarantees",
        object_type=SLA,
        vocab_field="known_sla",
        global_constant="KNOWN_SLA",
        gliner_labels=("SLA Commitment",),
        evidence_required=True,
        description="A contractual reliability commitment.",
    ),
    RelationSpec(
        predicate="competesAgainst",
        object_type=COMPETITOR,
        vocab_field="known_competitors",
        global_constant="KNOWN_COMPETITORS",
        gliner_labels=("Competitor",),
        description="A rival the product is positioned against.",
    ),
    RelationSpec(
        predicate="hasCustomer",
        object_type=CUSTOMER,
        vocab_field="known_customers",
        global_constant="KNOWN_CUSTOMERS",
        gliner_labels=("Customer",),
        description=(
            "A named customer of the product, typically from a testimonial, logo wall "
            "or case study. Not a capability claim: it needs no documentation evidence "
            "and must never be treated as drift."
        ),
    ),
]

# GLiNER labels that intentionally map to no relation, because they name the product
# itself. The product is the subject of every triple, never an object, so an entity
# carrying one of these must not become a triple. Declared so coverage checks can tell
# the difference between "deliberately unmapped" and "someone forgot".
UNMAPPED_GLINER_LABELS = frozenset({
    "Software Platform",
    "AI Security Platform",
    "AI-Native Application",
    "Clinical Platform",
    "Developer Platform",
    "Employee Management Platform",
    "Payroll Software",
    "Security Platform",
})

# Labels shipped in vertical profiles whose correct relation is a genuine domain
# judgement nobody has made yet. "Revenue Stream" could be a feature or a reporting
# metric; "Threat Vector" describes what a product defends against, which is not a
# relation this schema has. They are tracked rather than guessed: mapping one wrongly
# is worse than not mapping it, because a wrong predicate becomes a confident and
# false drift alert.
#
# This set is a backlog, not a resting place. Emptying it needs someone who knows the
# vertical, and each decision either adds the label to a RelationSpec above, moves it
# to UNMAPPED_GLINER_LABELS, or removes it from the vertical profile that declares it.
TRIAGE_GLINER_LABELS = frozenset({
    "AI Agent Governance",
    "AI Agent Security",
    "AI Application Security",
    "AI Application Vulnerability",
    "AI-Generated Code",
    "Application Security",
    "Architecture Pattern",
    "Care Delivery Model",
    "Code Security",
    "Development Agent",
    "Development Workflow",
    "Infrastructure Tool",
    "Programming Language",
    "Reporting Metric",
    "Revenue Stream",
    "Security Architecture",
    "Security Posture",
    "Threat Vector",
})


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

BY_PREDICATE: Dict[str, RelationSpec] = {r.predicate: r for r in RELATIONS}
BY_VOCAB_FIELD: Dict[str, RelationSpec] = {r.vocab_field: r for r in RELATIONS}

_BY_GLINER_LABEL: Dict[str, RelationSpec] = {}
for _r in RELATIONS:
    for _label in _r.gliner_labels:
        _BY_GLINER_LABEL[_label] = _r


def all_predicates() -> List[str]:
    """Every predicate the schema defines, in declaration order."""
    return [r.predicate for r in RELATIONS]


def relation_for_predicate(predicate: str) -> Optional[RelationSpec]:
    return BY_PREDICATE.get(predicate)


def relation_for_vocab_field(vocab_field: str) -> Optional[RelationSpec]:
    return BY_VOCAB_FIELD.get(vocab_field)


def relation_for_gliner_label(label: str) -> Optional[RelationSpec]:
    """The relation an NER label produces, or None if the label maps to nothing."""
    return _BY_GLINER_LABEL.get(label)


def evidence_required_predicates() -> List[str]:
    """Predicates whose absence must not be reported as drift on an unfetched page."""
    return [r.predicate for r in RELATIONS if r.evidence_required]


# ---------------------------------------------------------------------------
# Coverage check
#
# The failure this catches: a vertical declares a vocabulary list but omits the
# matching GLiNER label (or vice versa), so a relation is only half-wired and the
# gap looks like a product that simply has none of that thing.
# ---------------------------------------------------------------------------

def check_vertical_coverage(config) -> Dict[str, List[str]]:
    """Report how completely one VerticalConfig wires up the schema.

    Returns a dict with three lists of predicate names:
      no_vocabulary   - falls back to the constants.py global for this relation,
                        which is billing-flavoured and wrong outside fintech.
      no_gliner_label - none of the relation's NER labels are in gliner_labels, so
                        the entity branch can never fire for this vertical.
      fully_wired     - has both.
    """
    no_vocabulary: List[str] = []
    no_gliner_label: List[str] = []
    fully_wired: List[str] = []

    declared_labels = set(getattr(config, "gliner_labels", []) or [])

    for spec in RELATIONS:
        has_vocab = bool(getattr(config, spec.vocab_field, None))
        has_label = bool(declared_labels.intersection(spec.gliner_labels))

        if not has_vocab:
            no_vocabulary.append(spec.predicate)
        if not has_label:
            no_gliner_label.append(spec.predicate)
        if has_vocab and has_label:
            fully_wired.append(spec.predicate)

    return {
        "no_vocabulary": no_vocabulary,
        "no_gliner_label": no_gliner_label,
        "fully_wired": fully_wired,
    }
