"""
GainARK OntoLeap — API Shared Dependencies & Pipeline Registry
"""

import os
import logging
from typing import Optional, Dict, Set
from fastapi import HTTPException

from pipeline import OntologyPipeline

logger = logging.getLogger("ontoleap.api.deps")

pipeline_cache: Dict[str, OntologyPipeline] = {}
SUPPORTED_VERTICALS: Set[str] = {
    "b2b_saas_fintech",
    "cybersecurity",
    "healthtech",
    "developer_tools"
}


def get_pipeline(vertical_id: Optional[str] = None) -> OntologyPipeline:
    """
    Retrieves or lazily instantiates the OntologyPipeline for the requested vertical.
    Validates vertical_id and returns HTTP 400 if unsupported.
    """
    target_id = vertical_id or "b2b_saas_fintech"
    if target_id not in SUPPORTED_VERTICALS:
        if os.path.exists(f"verticals/{target_id}.json"):
            SUPPORTED_VERTICALS.add(target_id)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported vertical_id '{vertical_id}'. Supported verticals: {sorted(list(SUPPORTED_VERTICALS))}"
            )
    if target_id not in pipeline_cache:
        config_file = f"verticals/{target_id}.json"
        if not os.path.exists(config_file):
            config_file = f"configs/{target_id}.json"
        if not os.path.exists(config_file):
            config_file = "vertical_config.json"

        logger.info("Instantiating OntologyPipeline for vertical '%s' using %s...", target_id, config_file)
        pipeline_cache[target_id] = OntologyPipeline(config_path=config_file)
    return pipeline_cache[target_id]


def get_default_pipeline() -> OntologyPipeline:
    """Alias for get_pipeline() with default vertical."""
    return get_pipeline()

