import sys
import json
from typing import List, Dict, Any
from pipeline import OntologyPipeline


def run_benchmark(urls: List[str] = None):
    if not urls:
        urls = [
            "https://www.chargebee.com",
            "https://www.maxio.com",
            "https://stripe.com/billing"
        ]

    print("=" * 80)
    print("STARTING ONTOLOGY PIPELINE COMPETITIVE BENCHMARK")
    print("=" * 80)

    pipeline = OntologyPipeline(config_path="vertical_config.json")
    all_seed_concepts = pipeline.config.core_seed_concepts

    results = []

    for url in urls:
        print(f"\n[+] Fetching & Processing: {url} ...")
        try:
            result = pipeline.process(url=url)
            
            # Mandatory Schema Compliance
            mand_status = result.mandatory_schema_status
            num_passed = sum(1 for v in mand_status.values() if v)
            total_mand = len(mand_status)
            passed_schemas = [k for k, v in mand_status.items() if v]
            missing_schemas = [k for k, v in mand_status.items() if not v]
            compliance_str = f"{num_passed}/{total_mand}"

            # Detected Concepts & Missing Concepts
            detected_concepts = [c.concept for c in result.seed_concepts if c.count > 0]
            detected_concept_set = set(detected_concepts)
            missing_concepts = [c for c in all_seed_concepts if c not in detected_concept_set]

            # Entities Count
            entity_count = len(result.entities)
            readiness_score = result.readiness_score

            results.append({
                "url": url,
                "title": result.title or "N/A",
                "readiness_score": readiness_score,
                "compliance_str": compliance_str,
                "passed_schemas": passed_schemas,
                "missing_schemas": missing_schemas,
                "entity_count": entity_count,
                "detected_concepts": detected_concepts,
                "missing_concepts": missing_concepts,
                "entities": result.entities,
                "readiness_breakdown": result.readiness_breakdown.model_dump() if result.readiness_breakdown else {},
                "recommended_patch": result.recommended_patch,
                "raw_result": result
            })
            print(f"    Finished. Score: {readiness_score:.2f}/100 | Schemas: {compliance_str} | Entities: {entity_count}")

        except Exception as e:
            print(f"    Error processing {url}: {e}")
            results.append({
                "url": url,
                "title": "Error",
                "readiness_score": 0.0,
                "compliance_str": "0/3",
                "passed_schemas": [],
                "missing_schemas": list(pipeline.config.mandatory_schema_types),
                "entity_count": 0,
                "detected_concepts": [],
                "missing_concepts": list(all_seed_concepts),
                "entities": [],
                "readiness_breakdown": {},
                "recommended_patch": None,
                "error": str(e)
            })

    # Generate Markdown Table
    md_lines = []
    md_lines.append("| Target URL | Structured Data Readiness (Single-Page) | Mandatory Schema Compliance | Number of Detected Entities | Top Missing Concepts |")
    md_lines.append("| :--- | :---: | :---: | :---: | :--- |")

    for r in results:
        missing_preview = ", ".join(r["missing_concepts"][:3])
        if len(r["missing_concepts"]) > 3:
            missing_preview += f" (+{len(r['missing_concepts']) - 3} more)"
        elif not r["missing_concepts"]:
            missing_preview = "None (100% Coverage)"
        
        md_lines.append(
            f"| `{r['url']}` | **{r['readiness_score']:.2f} / 100** | {r['compliance_str']} | {r['entity_count']} | {missing_preview} |"
        )

    md_table = "\n".join(md_lines)

    print("\n" + "=" * 80)
    print("COMPARATIVE BENCHMARK FINDINGS")
    print("=" * 80 + "\n")
    print(md_table)
    print("\n" + "=" * 80)

    # Save to benchmark_report.md
    with open("benchmark_report.md", "w", encoding="utf-8") as f:
        f.write("# Ontology Pipeline Benchmark: Competitive Structured Data Readiness Analysis\n\n")
        f.write("> **Executive Note on Brand Authority vs. Single-Page Schema Readiness:**\n")
        f.write("> This audit measures **Single-Page Structured Data & Schema Implementation** (Mandatory Schema 40%, Seed Concept Coverage 30%, Entity Richness 30%) on specific landing pages. High brand citation authority across AI answer engines (such as Stripe) can coexist with low single-page structured data scores when individual product landing pages omit static `SoftwareApplication`, `Organization`, or `Offer` JSON-LD nodes. For comprehensive domain-level generative search authority, evaluate using the **Site-Wide AI Citation Readiness Index**. This single-page score is deliberately not calibrated against AI citation frequency; `test_calibration.py` records the evidence behind that decision.\n\n")
        f.write(md_table + "\n\n")
        f.write("## Detailed Competitor Profiles\n\n")
        for r in results:
            f.write(f"### {r['url']}\n")
            f.write(f"- **Page Title**: {r['title']}\n")
            f.write(f"- **Structured Data Readiness Score**: {r['readiness_score']:.2f} / 100.0\n")
            f.write(f"- **Mandatory Schemas Detected**: {', '.join(r['passed_schemas']) if r['passed_schemas'] else 'None'} ({r['compliance_str']})\n")
            f.write(f"- **Missing Schemas**: {', '.join(r['missing_schemas']) if r['missing_schemas'] else 'None'}\n")
            f.write(f"- **Total Entities Extracted**: {r['entity_count']}\n")
            f.write(f"- **Detected Seed Concepts**: {', '.join(r['detected_concepts']) if r['detected_concepts'] else 'None'}\n")
            f.write(f"- **Missing Seed Concepts**: {', '.join(r['missing_concepts']) if r['missing_concepts'] else 'None'}\n\n")
            if r.get("entities"):
                f.write("**Top Extracted Entities:**\n")
                for ent in sorted(r["entities"], key=lambda x: -x.score)[:8]:
                    f.write(f"- `[{ent.label}]` {ent.text} (conf: {ent.score:.4f})\n")
                f.write("\n")

    # Save to JSON
    json_export = []
    for r in results:
        item = {k: v for k, v in r.items() if k not in ["raw_result", "entities"]}
        if r.get("raw_result"):
            item["full_result"] = r["raw_result"].model_dump()
        json_export.append(item)

    with open("benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(json_export, f, indent=2)

    print("Saved benchmark report to benchmark_report.md and benchmark_results.json")
    return results


if __name__ == "__main__":
    cli_urls = sys.argv[1:] if len(sys.argv) > 1 else None
    run_benchmark(cli_urls)
