"""
GainARK OntoLeap — API Routers Package
"""

from routers.system_routes import router as system_router
from routers.governance_routes import router as governance_router
from routers.kg_routes import router as kg_router
from routers.seo_routes import router as seo_router
from routers.audit_routes import router as audit_router

__all__ = [
    "system_router",
    "governance_router",
    "kg_router",
    "seo_router",
    "audit_router",
]
