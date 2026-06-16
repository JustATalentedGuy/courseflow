from fastapi import APIRouter

from app.api.v1 import auth, courses, diagrams, edge, notes, quiz, quota, search, srs, videos

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(courses.router)
api_router.include_router(videos.router)
api_router.include_router(edge.router)
api_router.include_router(diagrams.router)
api_router.include_router(notes.router)
api_router.include_router(quota.router)
api_router.include_router(search.router)
api_router.include_router(quiz.router)
api_router.include_router(srs.router)
