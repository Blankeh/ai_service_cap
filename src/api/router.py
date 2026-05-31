"""
api/router.py
─────────────
Single import point for all versioned routers.
Add future versions here (e.g. v2) without touching main.py.
"""

from fastapi import APIRouter

from .v1 import routes as v1_routes

api_router = APIRouter()

api_router.include_router(v1_routes.router, prefix="/v1")
