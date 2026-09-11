"""
Project CRUD — the root entity everything else scopes under (project_id).
Phase 0 stub. Real implementation needs infra/db/repositories + schemas/.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/projects", tags=["projects"])

# TODO (Phase 1): POST/GET/PATCH endpoints backed by infra/db/repositories
