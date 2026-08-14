from fastapi import APIRouter

from .routes import (
    admin_jobs_router,
    jobs_router,
    pages_router,
    scripts_router,
    system_router,
    voices_router,
)


router = APIRouter()
router.include_router(pages_router)
router.include_router(system_router)
router.include_router(voices_router)
router.include_router(scripts_router)
router.include_router(jobs_router)
router.include_router(admin_jobs_router)
