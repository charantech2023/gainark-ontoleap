"""
GainARK OntoLeap — API Shared Dependencies & Pipeline Registry
"""

import os
import logging
from typing import Optional, Set
from fastapi import HTTPException

from pipeline import OntologyPipeline
from security import BoundedRegistry, is_valid_vertical_id

logger = logging.getLogger("ontoleap.api.deps")

# Directory holding vertical ontology profiles, resolved absolutely so behaviour does
# not depend on the process working directory.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERTICALS_DIR = os.path.join(BASE_DIR, "verticals")
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")
DEFAULT_CONFIG = os.path.join(BASE_DIR, "vertical_config.json")

DEFAULT_VERTICAL_ID = "b2b_saas_fintech"

# Every cached pipeline holds its own lazily-loaded GLiNER model reference, and the key
# is caller-supplied, so an unbounded dict is a memory-exhaustion primitive. The cap is
# generous relative to the number of verticals a deployment realistically serves.
MAX_CACHED_PIPELINES = int(os.environ.get("MAX_CACHED_PIPELINES", "16") or 16)

pipeline_cache: BoundedRegistry = BoundedRegistry(max_entries=MAX_CACHED_PIPELINES)

SUPPORTED_VERTICALS: Set[str] = {
    "b2b_saas_fintech",
    "cybersecurity",
    "healthtech",
    "developer_tools"
}


def _vertical_config_path(vertical_id: str) -> Optional[str]:
    """
    Resolve a validated vertical ID to a config file inside the verticals/ or configs/
    directory, or None if no profile exists.

    The ID is validated as a bare slug before it reaches here, and the resolved path is
    confirmed to stay inside the intended directory, so a request body cannot cause an
    arbitrary JSON file on disk to be loaded as a pipeline config.
    """
    for directory in (VERTICALS_DIR, CONFIGS_DIR):
        candidate = os.path.normpath(os.path.join(directory, f"{vertical_id}.json"))
        if not candidate.startswith(os.path.join(directory, "")):
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


def get_pipeline(vertical_id: Optional[str] = None) -> OntologyPipeline:
    """
    Retrieves or lazily instantiates the OntologyPipeline for the requested vertical.
    Validates vertical_id and returns HTTP 400 if malformed or unsupported.
    """
    target_id = vertical_id or DEFAULT_VERTICAL_ID

    # Reject anything that is not a bare slug before it is used to build a path.
    if not is_valid_vertical_id(target_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid vertical_id. Must be 1-64 characters of letters, digits or "
                "underscores."
            ),
        )

    config_file = _vertical_config_path(target_id)

    if target_id not in SUPPORTED_VERTICALS:
        if config_file:
            SUPPORTED_VERTICALS.add(target_id)
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported vertical_id '{target_id}'. "
                    f"Supported verticals: {sorted(SUPPORTED_VERTICALS)}"
                ),
            )

    def _build() -> OntologyPipeline:
        path = config_file or DEFAULT_CONFIG
        logger.info("Instantiating OntologyPipeline for vertical '%s' using %s...", target_id, path)
        return OntologyPipeline(config_path=path)

    return pipeline_cache.get_or_create(target_id, _build)


def get_default_pipeline() -> OntologyPipeline:
    """Alias for get_pipeline() with default vertical."""
    return get_pipeline()
