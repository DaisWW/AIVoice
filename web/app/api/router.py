from fastapi import APIRouter

from .routes import (
    admin_jobs_router,
    admin_system_router,
    auth_router,
    jobs_router,
    pages_router,
    providers_router,
    projects_router,
    scripts_router,
    system_router,
    voices_router,
)
from .text_generation_runs import router as text_generation_runs_router


router = APIRouter()
router.include_router(pages_router)
router.include_router(auth_router)
router.include_router(system_router)
router.include_router(projects_router)
router.include_router(voices_router)
router.include_router(scripts_router)
router.include_router(jobs_router)
router.include_router(admin_jobs_router)
router.include_router(admin_system_router)
router.include_router(providers_router)
router.include_router(text_generation_runs_router)
