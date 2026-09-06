"""
GainARK OntoLeap — W3C SHACL Knowledge Graph Validator
Validates RDF graphs against enterprise governance shapes using pySHACL.
"""

import os
import logging
from typing import List, Dict, Any, Optional, Union
from rdflib import Graph, URIRef, Literal, RDF
from rdflib.namespace import SH
import pyshacl
from pydantic import BaseModel, Field

logger = logging.getLogger("gainark.shacl_validator")

SHAPES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shapes")
DEFAULT_SHAPES_FILE = os.path.join(SHAPES_DIR, "governance_shapes.ttl")


class SHACLViolationItem(BaseModel):
    focus_node: str = Field(..., description="URI or node ID of the entity that failed validation")
    message: str = Field(..., description="Human-readable violation message")
    severity: str = Field(default="Violation", description="Violation, Warning, or Info")
    result_path: Optional[str] = Field(default=None, description="Property path that failed")
    source_shape: Optional[str] = Field(default=None, description="Shape that triggered the violation")


class SHACLValidationReport(BaseModel):
    conforms: bool = Field(..., description="True if graph conforms to all SHACL shapes")
    violations_count: int = Field(default=0, description="Total count of constraint violations")
    violations: List[SHACLViolationItem] = Field(default_factory=list, description="Detailed list of violations")
    report_text: str = Field(default="", description="Human-readable pySHACL text summary")
    evaluated_triples_count: int = Field(default=0, description="Number of triples in audited graph")


def validate_rdf_graph_shacl(
    data_graph_or_ttl: Union[Graph, str],
    shapes_graph_or_ttl: Optional[Union[Graph, str]] = None,
    inference: Optional[str] = "rdfs"
) -> SHACLValidationReport:
    """
    Validates an RDF knowledge graph against W3C SHACL governance shapes.

    Parameters:
        data_graph_or_ttl: RDFLib Graph instance or Turtle format string.
        shapes_graph_or_ttl: Optional custom shapes Graph or Turtle string. Defaults to shapes/governance_shapes.ttl.
        inference: 'rdfs', 'owlrl', or None. Defaults to 'rdfs'.

    Returns:
        SHACLValidationReport with conformance status and structured violations.
    """
    if isinstance(data_graph_or_ttl, str):
        data_graph = Graph()
        data_graph.parse(data=data_graph_or_ttl, format="turtle")
    else:
        data_graph = data_graph_or_ttl

    if shapes_graph_or_ttl is None:
        shapes_file = DEFAULT_SHAPES_FILE
        if not os.path.exists(shapes_file):
            raise FileNotFoundError(f"SHACL shapes file not found at {shapes_file}")
        shapes_graph = Graph()
        shapes_graph.parse(shapes_file, format="turtle")
    elif isinstance(shapes_graph_or_ttl, str):
        shapes_graph = Graph()
        shapes_graph.parse(data=shapes_graph_or_ttl, format="turtle")
    else:
        shapes_graph = shapes_graph_or_ttl

    # Execute pySHACL validation
    conforms, report_graph, report_text = pyshacl.validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference=inference,
        abort_on_first=False,
        meta_shacl=False,
        advanced=True
    )

    violations: List[SHACLViolationItem] = []

    # Extract structured violations from report_graph
    for res_node in report_graph.objects(None, SH.result):
        msg_val = report_graph.value(res_node, SH.resultMessage)
        focus_val = report_graph.value(res_node, SH.focusNode)
        sev_val = report_graph.value(res_node, SH.resultSeverity)
        path_val = report_graph.value(res_node, SH.resultPath)
        shape_val = report_graph.value(res_node, SH.sourceShape)

        sev_str = str(sev_val).split("#")[-1] if sev_val else "Violation"
        msg_str = str(msg_val) if msg_val else "SHACL constraint violation"
        focus_str = str(focus_val) if focus_val else "Unknown Node"
        path_str = str(path_val) if path_val else None
        shape_str = str(shape_val) if shape_val else None

        violations.append(SHACLViolationItem(
            focus_node=focus_str,
            message=msg_str,
            severity=sev_str,
            result_path=path_str,
            source_shape=shape_str
        ))

    return SHACLValidationReport(
        conforms=conforms,
        violations_count=len(violations),
        violations=violations,
        report_text=str(report_text),
        evaluated_triples_count=len(data_graph)
    )
