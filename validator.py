from typing import Dict, Any, List
from models import SchemaValidationReport


def validate_schema_patch(schema_dict: Dict[str, Any]) -> SchemaValidationReport:
    """
    Validates a Schema.org JSON-LD payload against Google Rich Results & Knowledge Graph guidelines.
    Specifically checks SoftwareApplication, Organization, and Offer specifications.
    """
    if not isinstance(schema_dict, dict):
        return SchemaValidationReport(
            is_valid=False,
            google_rich_results_eligible=False,
            validated_types=[],
            errors=["Payload is not a valid JSON object."],
            warnings=[],
            compliance_percentage=0.0
        )

    context = schema_dict.get("@context", "")
    if "schema.org" not in str(context).lower():
        return SchemaValidationReport(
            is_valid=False,
            google_rich_results_eligible=False,
            validated_types=[],
            errors=["Missing or invalid @context. Must point to https://schema.org."],
            warnings=[],
            compliance_percentage=20.0
        )

    nodes = schema_dict.get("@graph", [schema_dict])
    if not isinstance(nodes, list):
        nodes = [nodes]

    validated_types: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        stype = node.get("@type", "")
        if isinstance(stype, list):
            stype = ", ".join(stype)

        validated_types.append(stype)

        # SoftwareApplication rules
        if "SoftwareApplication" in stype:
            if not node.get("name"):
                errors.append("SoftwareApplication missing required property 'name'.")
            if not node.get("applicationCategory") and not node.get("operatingSystem"):
                warnings.append("SoftwareApplication recommended property 'applicationCategory' or 'operatingSystem' missing.")
            if not node.get("description"):
                warnings.append("SoftwareApplication recommended property 'description' missing.")
            if not node.get("offers"):
                warnings.append("SoftwareApplication recommended property 'offers' missing.")

        # Organization rules
        if "Organization" in stype:
            if not node.get("name"):
                errors.append("Organization missing required property 'name'.")
            if not node.get("url"):
                errors.append("Organization missing required property 'url'.")
            if not node.get("sameAs"):
                warnings.append("Organization recommended property 'sameAs' (Wikidata/social profiles) missing.")

        # Offer rules
        if "Offer" in stype:
            if not node.get("priceCurrency") and not node.get("price") and not node.get("description"):
                warnings.append("Offer recommended property 'price' or 'description' missing.")

    penalties = (len(errors) * 30) + (len(warnings) * 5)
    compliance = max(0.0, min(100.0, 100.0 - penalties))
    is_valid = len(errors) == 0
    rich_results = is_valid and ("SoftwareApplication" in "".join(validated_types) or "Organization" in "".join(validated_types))

    return SchemaValidationReport(
        is_valid=is_valid,
        google_rich_results_eligible=rich_results,
        validated_types=list(set(validated_types)),
        errors=errors,
        warnings=warnings,
        compliance_percentage=compliance
    )
