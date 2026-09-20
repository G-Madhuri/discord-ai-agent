from fastapi import APIRouter

from app.api.routes import assignments, members, projects, tasks

api_router = APIRouter()
api_router.include_router(projects.router)
api_router.include_router(members.router)
api_router.include_router(tasks.router)
api_router.include_router(assignments.router)
