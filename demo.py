import sys
import json
from pipeline import OntologyPipeline

def main():
    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.ordwaylabs.com"
    
    print(f"==================================================")
    print(f"Running Ontology Pipeline against: {target_url}")
    print(f"==================================================")

    pipeline = OntologyPipeline(config_path="vertical_config.json")
    result = pipeline.process(url=target_url)

    print("\n" + "="*60)
    print(f"EXTRACTION SUMMARY: {result.vertical_id}")
    print(f"Target URL : {result.url}")
    print(f"Page Title : {result.title}")
    print("="*60)

    # 1. Final Extracted Entities
    print(f"\n[1] FINAL EXTRACTED ENTITIES ({len(result.entities)} found):")
    print("-" * 60)
    if result.entities:
        for ent in sorted(result.entities, key=lambda x: (x.label, -x.score)):
            print(f"  - [{ent.label:<20}] '{ent.text}' (confidence: {ent.score:.4f})")
    else:
        print("  None detected.")

    # 2. Detected Schema.org Types
    print(f"\n[2] DETECTED SCHEMA.ORG TYPES ({len(result.schema_org)} nodes found):")
    print("-" * 60)
    unique_types = {}
    for s in result.schema_org:
        if s.schema_type not in unique_types:
            unique_types[s.schema_type] = s.is_mandatory
    
    for stype, is_mand in sorted(unique_types.items()):
        mandatory_tag = " [MANDATORY TYPE]" if is_mand else ""
        print(f"  - {stype:<25}{mandatory_tag}")

    print("\nMandatory Schema Compliance:")
    for m_type, status in result.mandatory_schema_status.items():
        status_text = "PASSED" if status else "MISSING"
        print(f"  - {m_type:<25}: {status_text}")

    # 3. Core Seed Concepts
    print(f"\n[3] CORE SEED CONCEPTS ({len(result.seed_concepts)} found):")
    print("-" * 60)
    for c in result.seed_concepts:
        print(f"  - {c.concept:<25}: matched {c.count} time(s)")

    # 4. Ontology Readiness Score
    print("\n" + "="*60)
    print(f"ONTOLOGY READINESS SCORE: {result.readiness_score:.2f} / 100.00")
    print("="*60)
    if result.readiness_breakdown:
        b = result.readiness_breakdown
        print(f"  * Mandatory Schema Coverage : {b.schema_score:.2f} / 40.0 pts  ({b.details.get('mandatory_schemas_detected', '')})")
        print(f"  * Seed Concepts Coverage    : {b.concept_score:.2f} / 30.0 pts  ({b.details.get('seed_concepts_detected', '')})")
        print(f"  * Entity Diversity & Richness: {b.entity_score:.2f} / 30.0 pts  ({b.details.get('gliner_labels_covered', '')} labels, {b.details.get('entities_extracted', 0)} entities, avg conf: {b.details.get('average_entity_confidence', 0.0)})")

    # 5. Recommended Remediation Patch
    if result.recommended_patch:
        patch = result.recommended_patch
        print("\n" + "="*60)
        print("RECOMMENDED REMEDIATION PATCH (Schema.org JSON-LD):")
        print("="*60)
        print(f"Schemas Addressed : {', '.join(patch.get('schemas_added', []))}")
        print(f"Features Mapped   : {', '.join(patch.get('features_mapped', []))}")
        print(f"Resolved Provider : {patch.get('resolved_provider', {}).get('name')} ({patch.get('resolved_provider', {}).get('@id')})")
        print("\nFormatted JSON-LD Patch Snippet:")
        print("-" * 60)
        print(patch.get("json_ld", ""))
        print("-" * 60)

    output_filename = "ordway_extraction.json" if "ordway" in target_url else "extraction_output.json"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))
    print(f"\nSaved full extraction artifact to {output_filename}")

if __name__ == "__main__":
    main()
