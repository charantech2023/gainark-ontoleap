"""
GainARK OntoLeap — Knowledge Graph Link Prediction & Entity Completion Engine

Rule-based relation inference over hand-curated ontological priors. Given the triples,
entities and topic hubs already extracted from a site, this module:
1. Predict high-probability missing ontological relationships (e.g., compliesWith, integratesWith, supportsPricingModel).
2. Ground predicted entities against canonical Wikidata Q-IDs via schema:sameAs.
3. Quantify Knowledge Graph Completeness and identify high-value semantic content gaps.
4. Recommend concrete internal linking and Schema.org remediation actions to maximize AI search attribution.
"""

from typing import List, Dict, Any, Optional, Set, Tuple
import re
from urllib.parse import urlparse

from models import (
    SemanticTriple,
    PredictedLink,
    LinkPredictionRequest,
    LinkPredictionResponse
)

from entity_grounding import ground_id, ground_url, prefetch as prefetch_grounding

# Domain link prediction heuristics & relational priors
PREDICTION_TEMPLATES: List[Dict[str, Any]] = [
    # FinTech / Revenue & Billing
    {
        "trigger_keywords": ["revenue recognition", "asc 606", "rev rec", "deferred revenue", "contract asset"],
        "predictions": [
            {
                "predicate": "compliesWith",
                "object": "ASC 606",
                "base_confidence": 0.96,
                "reasoning": "Platforms automating Revenue Recognition universally require ASC 606 compliance for audit-ready financial statements.",
                "action": "Add SoftwareApplication.complianceStandard = 'ASC 606' to Schema.org and ground with Q2819869."
            },
            {
                "predicate": "compliesWith",
                "object": "IFRS 15",
                "base_confidence": 0.92,
                "reasoning": "Dual-reporting financial platforms automate IFRS 15 alongside ASC 606 for multinational contracts.",
                "action": "Include IFRS 15 in accounting compliance schemas with Q16996614."
            },
            {
                "predicate": "integratesWith",
                "object": "Salesforce",
                "base_confidence": 0.94,
                "reasoning": "Closed-Won Opportunities in Salesforce CRM are the primary upstream triggers for automated revenue schedules.",
                "action": "Define isRelatedTo linking Salesforce with Wikidata Q760814."
            },
            {
                "predicate": "integratesWith",
                "object": "NetSuite",
                "base_confidence": 0.91,
                "reasoning": "NetSuite General Ledger synchronization is essential for revenue recognition journal entry exports.",
                "action": "Document NetSuite ERP connector in platform schema with Q1978731."
            },
            {
                "predicate": "supportsCapability",
                "object": "Deferred Revenue Waterfall",
                "base_confidence": 0.90,
                "reasoning": "Automated rev rec engines generate monthly deferred revenue waterfall balance sheet schedules.",
                "action": "Create dedicated Topic Authority Hub for 'Deferred Revenue Waterfall'."
            }
        ]
    },
    # Subscriptions & Billing
    {
        "trigger_keywords": ["billing", "subscription", "recurring", "invoicing", "payments", "metering"],
        "predictions": [
            {
                "predicate": "supportsPricingModel",
                "object": "Usage-Based Pricing",
                "base_confidence": 0.92,
                "reasoning": "Modern recurring billing systems offer consumption and usage metering alongside fixed subscriptions.",
                "action": "Add Offer.priceSpecification detailing usage-based pricing models."
            },
            {
                "predicate": "supportsPricingModel",
                "object": "Tiered Pricing",
                "base_confidence": 0.89,
                "reasoning": "SaaS billing catalogs universally implement tiered seat and feature gating matrices.",
                "action": "Enrich pricing page schema with tiered price tiers and feature entitlement."
            },
            {
                "predicate": "integratesWith",
                "object": "Stripe",
                "base_confidence": 0.91,
                "reasoning": "Stripe is the dominant payment infrastructure for recurring credit card tokenization and processing.",
                "action": "Add Stripe integration node grounded to Wikidata Q7624119."
            },
            {
                "predicate": "integratesWith",
                "object": "QuickBooks",
                "base_confidence": 0.88,
                "reasoning": "SME and mid-market billing solutions sync accounts receivable balances with QuickBooks Online.",
                "action": "Add QuickBooks accounting integration triple grounded to Q7271981."
            },
            {
                "predicate": "compliesWith",
                "object": "PCI-DSS",
                "base_confidence": 0.90,
                "reasoning": "Credit card processing and payment information handling mandates PCI-DSS security compliance.",
                "action": "Ground PCI-DSS payment compliance in security and trust schema."
            }
        ]
    },
    # General Enterprise SaaS Infrastructure & Security
    {
        "trigger_keywords": ["platform", "enterprise", "saas", "software", "api", "cloud", "security"],
        "predictions": [
            {
                "predicate": "compliesWith",
                "object": "SOC 2 Type II",
                "base_confidence": 0.93,
                "reasoning": "Enterprise B2B SaaS buyers require verified SOC 2 Type II security audit compliance.",
                "action": "Highlight SOC 2 Type II in Organization and Application trust schemas (Q105822363)."
            },
            {
                "predicate": "compliesWith",
                "object": "GDPR",
                "base_confidence": 0.88,
                "reasoning": "SaaS architectures operating globally adhere to GDPR personal data privacy requirements.",
                "action": "Declare GDPR compliance in legal privacy and security ontologies."
            },
            {
                "predicate": "compliesWith",
                "object": "ISO 27001",
                "base_confidence": 0.85,
                "reasoning": "ISO 27001 information security management certification provides global enterprise credibility.",
                "action": "Add ISO 27001 certification under Organization.hasCredential."
            },
            {
                "predicate": "integratesWith",
                "object": "Workday",
                "base_confidence": 0.84,
                "reasoning": "Enterprise cloud ecosystems integrate with Workday for corporate financial and headcount synchronization.",
                "action": "Link Workday integration entity (Wikidata Q2592881)."
            },
            {
                "predicate": "integratesWith",
                "object": "Slack",
                "base_confidence": 0.82,
                "reasoning": "Modern software platforms deliver automated webhooks and real-time operational notifications to Slack.",
                "action": "Document Slack notification connector in schema."
            },
            {
                "predicate": "integratesWith",
                "object": "Snowflake",
                "base_confidence": 0.83,
                "reasoning": "Data-rich enterprise software pipes operational data into Snowflake data warehouses for executive BI.",
                "action": "Publish Snowflake analytics connector in integrations catalog."
            }
        ]
    }
]


def predict_kg_links(
    domain: str,
    triples: List[SemanticTriple],
    entities: List[str],
    topic_hubs: Dict[str, str]
) -> LinkPredictionResponse:
    """
    Algorithmic knowledge graph link prediction engine.
    Infers missing high-confidence relational triples based on observed ontological priors.
    """
    # Clean domain brand name
    brand = domain.replace("https://", "").replace("http://", "").split("/")[0].replace("www.", "").split(".")[0].capitalize()
    if not brand or brand.lower() == "example":
        brand = "Platform"

    # Normalize existing knowledge graph state
    existing_pairs: Set[Tuple[str, str]] = set()
    for t in triples:
        pred_norm = t.predicate.lower().replace("_", "").replace("-", "")
        obj_norm = t.object.lower().strip()
        existing_pairs.add((pred_norm, obj_norm))

    # Aggregate contextual text corpus from entities, hubs, and triple evidence
    context_tokens: Set[str] = set()
    for e in entities:
        context_tokens.update(re.findall(r'\b[a-zA-Z0-9_-]+\b', e.lower()))
    for hub_concept in topic_hubs.keys():
        context_tokens.update(re.findall(r'\b[a-zA-Z0-9_-]+\b', hub_concept.lower()))
    for t in triples:
        context_tokens.update(re.findall(r'\b[a-zA-Z0-9_-]+\b', t.subject.lower()))
        context_tokens.update(re.findall(r'\b[a-zA-Z0-9_-]+\b', t.object.lower()))
        evidence = getattr(t, "evidence_sentence", None) or getattr(t, "evidence", None)
        if evidence:
            context_tokens.update(re.findall(r'\b[a-zA-Z0-9_-]+\b', str(evidence).lower()))

    context_str = " ".join(context_tokens)

    predicted_items: List[PredictedLink] = []
    seen_predictions: Set[Tuple[str, str]] = set()

    # The objects a template can predict are a fixed, bounded set, so warming them once
    # grounds predicted links that fall outside the 40 curated Q-IDs at the cost of a
    # single batch - and after the first run of a process it is all cache hits.
    prefetch_grounding([p_spec["object"] for tmpl in PREDICTION_TEMPLATES
                        for p_spec in tmpl["predictions"]])

    for tmpl in PREDICTION_TEMPLATES:
        triggers = tmpl["trigger_keywords"]
        # Measure trigger relevance score
        matched_triggers = [kw for kw in triggers if kw in context_str or any(kw in e.lower() for e in entities)]
        if not matched_triggers:
            continue

        relevance_multiplier = min(1.0, 0.75 + (0.05 * len(matched_triggers)))

        for p_spec in tmpl["predictions"]:
            pred = p_spec["predicate"]
            obj = p_spec["object"]
            pred_norm = pred.lower().replace("_", "").replace("-", "")
            obj_norm = obj.lower().strip()

            # Skip if already exists in graph
            if (pred_norm, obj_norm) in existing_pairs or (obj_norm, pred_norm) in existing_pairs:
                continue

            # Skip if already predicted in this run
            if (pred_norm, obj_norm) in seen_predictions:
                continue

            seen_predictions.add((pred_norm, obj_norm))

            # Calibrate confidence score
            conf = round(p_spec["base_confidence"] * relevance_multiplier, 3)

            # Ground with Wikidata
            wiki_url = ground_url(obj_norm)
            wiki_id = ground_id(obj_norm)

            predicted_items.append(PredictedLink(
                subject=brand,
                predicate=pred,
                object=obj,
                confidence=conf,
                reasoning=p_spec["reasoning"],
                wikidata_id=wiki_id,
                wikidata_url=wiki_url,
                recommended_action=p_spec["action"]
            ))

    # Sort predicted links by confidence descending
    predicted_items.sort(key=lambda x: x.confidence, reverse=True)

    # Calculate overall knowledge graph completeness score
    # FIX Task 3: Non-circular completeness score.
    # Rather than existing / (existing + predicted) which inverted meaning by rewarding
    # sites that matched fewer trigger keywords, completeness is evaluated against a fixed
    # benchmark denominator of expected canonical relations for the vertical (16).
    EXPECTED_VERTICAL_RELATIONS_BENCHMARK = 16
    existing_count = len(triples)
    predicted_count = len(predicted_items)

    completeness = min(100.0, round((existing_count / EXPECTED_VERTICAL_RELATIONS_BENCHMARK) * 100.0, 1))

    return LinkPredictionResponse(
        domain=domain,
        existing_triples_count=existing_count,
        predicted_links_count=predicted_count,
        predicted_links=predicted_items,
        graph_completeness_score=completeness,
        model_name="Rule-Based Relation Inference (Ontological Priors)"
    )
