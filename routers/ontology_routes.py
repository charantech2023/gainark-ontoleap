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


class OntologySparqlRequest(BaseModel):
    query: str = Field(..., min_length=5, max_length=10000, description="W3C SPARQL 1.1 SELECT query")
    vertical_id: str = Field(default=DEFAULT_VERTICAL_ID, description="Target vertical identifier")


@router.get("/export", summary="Export Ontology Knowledge Graph in W3C Standards")
def get_ontology_export(
    vertical_id: str = Query(DEFAULT_VERTICAL_ID, description="Target vertical identifier"),
    format: str = Query("turtle", description="Serialization format: turtle, xml, nt, json-ld"),
    download: bool = Query(False, description="Whether to trigger file download"),
):
    """
    Exports the complete ontology knowledge graph in standard W3C Semantic Web formats:
    - turtle (text/turtle)
    - xml / owl (application/rdf+xml)
    - nt / ntriples (application/n-triples)
    - json-ld (application/ld+json)
    """
    config_path = _vertical_config_path(vertical_id)
    if not config_path or not os.path.isfile(config_path):
        raise HTTPException(status_code=404, detail=f"Vertical '{vertical_id}' not found.")

    fmt_map = {
        "turtle": ("turtle", "text/turtle", "ttl"),
        "ttl": ("turtle", "text/turtle", "ttl"),
        "xml": ("xml", "application/rdf+xml", "owl"),
        "owl": ("xml", "application/rdf+xml", "owl"),
        "nt": ("nt", "application/n-triples", "nt"),
        "ntriples": ("nt", "application/n-triples", "nt"),
        "json-ld": ("json-ld", "application/ld+json", "jsonld"),
        "jsonld": ("json-ld", "application/ld+json", "jsonld"),
    }
    target = fmt_map.get(format.lower().strip())
    if not target:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{format}'. Supported: turtle, xml, nt, json-ld")

    rdflib_fmt, media_type, ext = target

    try:
        from linking import build_rdf_graph
        g = build_rdf_graph(domain="gainark.com", triples=[], hubs={}, entities=[], vertical_id=vertical_id)
        serialized = g.serialize(format=rdflib_fmt)
    except Exception as e:
        logger.error("Error exporting ontology in %s: %s", format, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to serialize ontology: {e}")

    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="ontology_{vertical_id}.{ext}"'

    from fastapi.responses import Response
    return Response(content=serialized, media_type=media_type, headers=headers)


@router.post("/sparql", summary="Execute SPARQL 1.1 Query on Vertical Ontology Knowledge Graph")
def post_ontology_sparql(req: OntologySparqlRequest):
    """
    Executes a W3C SPARQL 1.1 query against the compiled vertical ontology RDF graph.
    Supports queries across SKOS concepts, definitions, broader hierarchy, and governed standards.
    """
    config_path = _vertical_config_path(req.vertical_id)
    if not config_path or not os.path.isfile(config_path):
        raise HTTPException(status_code=404, detail=f"Vertical '{req.vertical_id}' not found.")

    try:
        from knowledge_graph import export_to_rdf_turtle, execute_sparql_query_on_ttl
        ttl = export_to_rdf_turtle(domain="gainark.com", triples=[], hubs={}, entities=[], vertical_id=req.vertical_id)
        res = execute_sparql_query_on_ttl(ttl, req.query)
        return {
            "query": req.query,
            "vertical_id": req.vertical_id,
            "columns": res.get("columns", []),
            "rows": res.get("rows", []),
            "row_count": res.get("row_count", 0),
            "execution_status": "success",
        }
    except ValueError as val_err:
        return {
            "query": req.query,
            "vertical_id": req.vertical_id,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_status": "failed",
            "error": str(val_err),
        }
    except Exception as e:
        logger.error("Ontology SPARQL query failed: %s", e, exc_info=True)
        return {
            "query": req.query,
            "vertical_id": req.vertical_id,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_status": "failed",
            "error": f"Query execution failed: {e}",
        }


@router.get("/tri-alignment", summary="Get Tri-Ontology Alignment Matrix (Product vs Standards vs Wikidata)")
def get_tri_ontology_alignment(
    vertical_id: str = Query(DEFAULT_VERTICAL_ID, description="Target vertical identifier"),
):
    """
    Returns the complete Tri-Ontology alignment matrix across all concepts in the vertical:
    - Layer 1: Product Capability Ontology (features, processes, pricing, evidence requirements)
    - Layer 2: Industry & Regulatory Governance (ASC 606, SOC 2, US GAAP, governing links and steps)
    - Layer 3: External Knowledge Graph (Wikidata Q-IDs, URIs, and Schema.org semantic types)
    """
    config_path = _vertical_config_path(vertical_id)
    if not config_path or not os.path.isfile(config_path):
        raise HTTPException(status_code=404, detail=f"Vertical '{vertical_id}' not found.")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read vertical config: {e}")

    concepts = data.get("concepts", [])

    # Map standard governance links
    governance_map: Dict[str, List[str]] = {}
    for c in concepts:
        for g in c.get("governs", []):
            governance_map.setdefault(g.strip().lower(), []).append(c["prefLabel"])

    # Map framework step requirements
    framework_steps_map: Dict[str, List[Dict[str, Any]]] = {}
    for fw_key, fw in comp_onto.COMPLIANCE_FRAMEWORKS.items():
        for step in fw.get("steps", []):
            step_name = step.get("name", f"Step {step.get('step_number')}")
            step_num = step.get("step_number")
            for cid in step.get("concept_ids", []):
                framework_steps_map.setdefault(cid.strip().lower(), []).append({
                    "standard": fw.get("standard"),
                    "step": step_num,
                    "name": step_name,
                    "label": f"{fw.get('standard')} (Step {step_num}): {step_name}"
                })

    from entity_grounding import ground_url, ground_id

    matrix = []
    fully_aligned_count = 0
    regulatory_count = 0
    wikidata_count = 0

    predicate_defaults = {
        "feature": ["hasFeature"],
        "process": ["automates"],
        "pricing": ["hasPricingModel"],
        "standard": ["compliesWith"],
        "domain": ["hasFeature", "automates"]
    }

    schema_type_map = {
        "feature": "SoftwareApplication",
        "process": "Action",
        "pricing": "UnitPriceSpecification",
        "standard": "LegalRule",
        "domain": "ComputerScience"
    }

    for c in concepts:
        cid = c.get("id", "").strip().lower()
        pref = c.get("prefLabel", "")
        kind = c.get("kind", "feature")

        # Layer 1: Product Capability
        preds = predicate_defaults.get(kind, ["hasFeature"])
        requires_evidence = kind in ("feature", "process", "standard")

        # Layer 2: Regulatory Governance
        gov_by = governance_map.get(cid, []) or governance_map.get(pref.lower(), [])
        steps = framework_steps_map.get(cid, []) or framework_steps_map.get(pref.lower(), [])
        has_regulatory = bool(gov_by or steps or kind == "standard")
        if has_regulatory:
            regulatory_count += 1

        # Layer 3: External Knowledge Graph Grounding
        w_url = ground_url(pref) or ground_url(cid)
        w_id = ground_id(pref) or ground_id(cid) if w_url else None
        has_wikidata = bool(w_url)
        if has_wikidata:
            wikidata_count += 1

        # Calculate Tri-Ontology Tier
        is_tier_1 = has_regulatory and has_wikidata
        if is_tier_1:
            fully_aligned_count += 1
            tier_label = "Tier 1: Fully Grounded Across All 3 Ontologies"
            tier_badge = "bg-emerald-100 text-emerald-800 border-emerald-300"
        elif has_regulatory:
            tier_label = "Tier 2: Regulatory & Capability Grounded"
            tier_badge = "bg-purple-100 text-purple-800 border-purple-300"
        elif has_wikidata:
            tier_label = "Tier 2: External KG & Capability Grounded"
            tier_badge = "bg-blue-100 text-blue-800 border-blue-300"
        else:
            tier_label = "Tier 3: Domain Capability (Internal)"
            tier_badge = "bg-slate-100 text-slate-700 border-slate-300"

        matrix.append({
            "id": c.get("id"),
            "prefLabel": pref,
            "kind": kind,
            "definition": c.get("definition", ""),
            "broader": c.get("broader"),
            "alt_labels_count": len(c.get("altLabels", [])),
            "layer_1_capability": {
                "predicates": preds,
                "evidence_required": requires_evidence,
                "skos_uri": f"https://gainark.com/ontoleap/ontology/{vertical_id}/concept/{c.get('id')}"
            },
            "layer_2_governance": {
                "is_governed": has_regulatory,
                "governed_by": sorted(list(set(gov_by))),
                "regulatory_steps": [s["label"] for s in steps],
                "governs": c.get("governs", []) if kind == "standard" else []
            },
            "layer_3_external_kg": {
                "is_grounded": has_wikidata,
                "wikidata_url": w_url,
                "wikidata_qid": w_id,
                "schema_org_type": schema_type_map.get(kind, "Thing")
            },
            "alignment": {
                "tier": tier_label,
                "tier_badge": tier_badge,
                "is_fully_aligned": is_tier_1,
                "score": 100 if is_tier_1 else (75 if (has_regulatory or has_wikidata) else 50)
            }
        })

    return {
        "vertical_id": vertical_id,
        "total_concepts": len(concepts),
        "fully_aligned_count": fully_aligned_count,
        "regulatory_grounded_count": regulatory_count,
        "wikidata_grounded_count": wikidata_count,
        "matrix": matrix
    }


