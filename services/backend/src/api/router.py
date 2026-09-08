from fastapi import APIRouter

from src.api.v1 import audit, auth, incidents, keys, notifications, sos, users, ws, zones

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(sos.router, prefix="/sos", tags=["SOS Ingestion"])
api_router.include_router(keys.router, prefix="/keys", tags=["Key Management"])
api_router.include_router(incidents.router, prefix="/incidents", tags=["Incidents"])
api_router.include_router(ws.router, prefix="/ws", tags=["Realtime WebSockets"])
api_router.include_router(zones.router, prefix="/zones", tags=["Zones"])
api_router.include_router(users.router, prefix="/users", tags=["User Management"])
api_router.include_router(audit.router, prefix="/audit", tags=["Audit Log"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
