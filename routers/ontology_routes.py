"""
GainARK OntoLeap — Dedicated Ontology & Knowledge Graph Governance Routes
==========================================================================
Provides REST APIs for:
1. Ontology schema inspection & namespace definition
2. Concept taxonomy querying, hierarchy, definitions, and altLabels
3. Multi-step compliance frameworks (ASC 606 5 steps, SOC 2 5 criteria)
4. Synonym candidate review & approval into the curated vertical ontology
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import ontology_schema as schema
import compliance_ontology as comp_onto
import sector_ontology as sector_onto
from models import Concept, SemanticTriple
from routers.deps import _vertical_config_path, DEFAULT_VERTICAL_ID, VERTICALS_DIR

logger = logging.getLogger("ontoleap.api.ontology")

router = APIRouter(prefix="/api/ontology", tags=["Ontology & Knowledge Graph"])


# ---------------------------------------------------------------------------
# Request & Response Models
# ---------------------------------------------------------------------------

class ApproveSynonymRequest(BaseModel):
    surface_form: str = Field(..., min_length=1, max_length=200, description="Marketing phrase / surface form")
    canonical_concept: str = Field(..., min_length=1, max_length=200, description="Canonical concept prefLabel in the ontology")
    vertical_id: str = Field(default=DEFAULT_VERTICAL_ID, description="Target vertical ontology profile")


class EvaluateComplianceRequest(BaseModel):
    standard: Optional[str] = Field(default=None, description="Optional single standard to evaluate (e.g. 'ASC 606')")
    technical_triples: List[SemanticTriple] = Field(default_factory=list, description="Extracted technical triples")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/schema", summary="Get Ontology Schema & Relation Definitions")
def get_ontology_schema():
    """
    Returns the core ontology schema specification, relation declarations,
    canonical namespaces, and entity type mappings.
    """
    relations = []
    for rel in schema.RELATIONS:
        relations.append({
            "predicate": rel.predicate,
            "subject_type": schema.PRODUCT,
            "object_type": rel.object_type,
            "vocab_field": rel.vocab_field,
            "description": rel.description,
            "gliner_labels": list(rel.gliner_labels),
            "evidence_required": rel.evidence_required,
        })
    return {
        "ontology_base_uri": schema.ONTOLOGY_BASE,
        "relations_count": len(relations),
        "relations": relations,
        "entity_types": [
            schema.PRODUCT,
            schema.INTEGRATION,
            schema.COMPLIANCE_STANDARD,
            schema.PRICING_MODEL,
            schema.AUTOMATION_CAPABILITY,
            schema.FEATURE,
            schema.SEGMENT,
            schema.INDUSTRY,
            schema.DEPLOYMENT_MODEL,
            schema.CERTIFICATION,
            schema.API_STANDARD,
            schema.LOCALE,
            schema.SLA,
        ],
    }


@router.get("/concepts", summary="Get Concepts and Hierarchy for a Vertical")
def get_ontology_concepts(
    vertical_id: str = Query(DEFAULT_VERTICAL_ID, description="Target vertical identifier"),
    kind: Optional[str] = Query(None, description="Filter by kind (feature, process, pricing, standard, domain)"),
):
    """
    Returns all defined concepts for the requested vertical, including their
    canonical prefLabel, unique URI, definitions, altLabels, broader hierarchy link,
    and governed standards.
    """
    config_path = _vertical_config_path(vertical_id)
    if not config_path or not os.path.isfile(config_path):
        raise HTTPException(status_code=404, detail=f"Vertical '{vertical_id}' not found.")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read vertical config: {e}")

    raw_concepts = data.get("concepts", [])
    if kind:
        raw_concepts = [c for c in raw_concepts if c.get("kind") == kind]

    by_kind: Dict[str, int] = {}
    for c in data.get("concepts", []):
        k = c.get("kind", "unknown")
        by_kind[k] = by_kind.get(k, 0) + 1

    return {
        "vertical_id": vertical_id,
        "display_name": data.get("display_name", vertical_id),
        "total_concepts": len(data.get("concepts", [])),
        "filtered_count": len(raw_concepts),
        "breakdown_by_kind": by_kind,
        "concepts": raw_concepts,
    }


@router.get("/compliance-frameworks", summary="List Registered Multi-Step Compliance Frameworks")
def get_compliance_frameworks():
    """
    Returns definitions, descriptions, and step-by-step requirements for all
    registered compliance frameworks (ASC 606 5 steps, SOC 2 5 criteria, etc.).
    """
    return {
        "frameworks_count": len(comp_onto.COMPLIANCE_FRAMEWORKS),
        "frameworks": comp_onto.list_compliance_frameworks(),
    }


@router.get("/compliance/{standard}", summary="Get Detailed Compliance Framework Specification")
def get_compliance_framework_detail(standard: str):
    """
    Returns the full step-by-step breakdown and governing requirements
    for a specific regulatory standard (e.g. 'ASC 606', 'SOC 2').
    """
    framework = comp_onto.COMPLIANCE_FRAMEWORKS.get(standard.strip())
    if not framework:
        for k, v in comp_onto.COMPLIANCE_FRAMEWORKS.items():
            if k.lower() == standard.strip().lower():
                framework = v
                break
    if not framework:
        raise HTTPException(
            status_code=404,
            detail=f"Compliance standard '{standard}' not found. Available: {list(comp_onto.COMPLIANCE_FRAMEWORKS.keys())}",
        )
    return framework


@router.post("/evaluate-compliance", summary="Evaluate Technical Triples Against Compliance Frameworks")
def post_evaluate_compliance(req: EvaluateComplianceRequest):
    """
    Evaluates a set of technical triples against multi-step compliance frameworks,
    returning exact step satisfaction, evidence matches, and compliance status.
    """
    if req.standard:
        res = comp_onto.evaluate_compliance_framework(req.technical_triples, req.standard)
        if not res:
            raise HTTPException(status_code=404, detail=f"Framework '{req.standard}' not found.")
        return {"evaluations": [res]}
    
    return {"evaluations": comp_onto.evaluate_all_compliance(req.technical_triples)}


@router.get("/candidates", summary="Get Pending Synonym Candidates Awaiting Review")
def get_synonym_candidates():
    """
    Returns the queue of candidate alt_labels / surface forms proposed by audits,
    awaiting reviewer approval into the curated vertical ontology.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates_path = os.path.join(base_dir, "truth_ledger", "alt_label_candidates.json")
    if not os.path.isfile(candidates_path):
        return {"total_candidates": 0, "pending_count": 0, "candidates": []}

    try:
        with open(candidates_path, "r", encoding="utf-8") as f:
            queue = json.load(f)
    except Exception as e:
        logger.warning("Could not read candidate queue: %s", e)
        return {"total_candidates": 0, "pending_count": 0, "candidates": []}

    items = []
    pending_count = 0
    for key, entry in queue.items():
        if entry.get("status") == "pending":
            pending_count += 1
        items.append({"key": key, **entry})

    return {
        "total_candidates": len(items),
        "pending_count": pending_count,
        "candidates": items,
    }


@router.post("/approve-synonym", summary="Approve a Candidate Synonym into the Curated Vertical Ontology")
def post_approve_synonym(req: ApproveSynonymRequest):
    """
    Promotes a surface form into the vertical's curated altLabels.
    Once approved, the surface form is canonicalized across extraction and
    stored directly in the vertical profile.
    """
    config_path = _vertical_config_path(req.vertical_id)
    if not config_path or not os.path.isfile(config_path):
        raise HTTPException(status_code=404, detail=f"Vertical '{req.vertical_id}' not found.")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    queue_path = os.path.join(base_dir, "truth_ledger", "alt_label_candidates.json")

    success = sector_onto.record_reviewer_synonym(
        marketing_term=req.surface_form,
        canonical_label=req.canonical_concept,
        vertical_path=config_path,
        queue_path=queue_path,
    )

    if not success:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Could not approve '{req.surface_form}' as an alternate for '{req.canonical_concept}'. "
                "Ensure the concept exists and the surface form is not already assigned."
            ),
        )

    return {
        "status": "approved",
        "surface_form": req.surface_form,
        "canonical_concept": req.canonical_concept,
        "vertical_id": req.vertical_id,
        "message": f"Successfully mapped '{req.surface_form}' -> '{req.canonical_concept}' in {os.path.basename(config_path)}.",
    }
