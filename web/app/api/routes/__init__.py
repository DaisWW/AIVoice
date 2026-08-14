from .admin_jobs import router as admin_jobs_router
from .jobs import router as jobs_router
from .pages import router as pages_router
from .scripts import router as scripts_router
from .system import router as system_router
from .voices import router as voices_router

__all__ = [
    "admin_jobs_router",
    "jobs_router",
    "pages_router",
    "scripts_router",
    "system_router",
    "voices_router",
]
