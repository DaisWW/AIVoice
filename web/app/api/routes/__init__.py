from .admin_system import router as admin_system_router
from .admin_jobs import router as admin_jobs_router
from .auth import router as auth_router
from .jobs import router as jobs_router
from .pages import router as pages_router
from .providers import router as providers_router
from .projects import router as projects_router
from .scripts import router as scripts_router
from .system import router as system_router
from .voices import router as voices_router

__all__ = [
    "admin_system_router",
    "admin_jobs_router",
    "auth_router",
    "jobs_router",
    "pages_router",
    "providers_router",
    "projects_router",
    "scripts_router",
    "system_router",
    "voices_router",
]
