"""
Notifications Router - /api/v1/notifications
Wires sms_notifier.py and email_service.py into the real backend.
Mode detection: GROQ_API_KEY presence = remote (Vercel), absence = local native.
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from src.auth.rbac import get_current_user
from src.database.models import User

logger = logging.getLogger("pukar.notifications")
router = APIRouter()

def _get_sms_notifier():
    try:
        from src.notifications.sms_notifier import SMSNotifier
        return SMSNotifier()
    except Exception as e:
        logger.warning(f"SMS notifier unavailable: {e}")
        return None

def _get_email_service():
    try:
        from src.notifications.email_service import EmailService
        return EmailService()
    except Exception as e:
        logger.warning(f"Email service unavailable: {e}")
        return None

class SMSAlertRequest(BaseModel):
    phone_number: str
    message: str
    cyclone_id: Optional[str] = None
    zone: Optional[str] = None

class EmailAlertRequest(BaseModel):
    recipient_email: str
    subject: str
    body: str
    cyclone_id: Optional[str] = None
    zone: Optional[str] = None

@router.post("/sms", tags=["Notifications"])
async def send_sms_alert(request: SMSAlertRequest, current_user: User = Depends(get_current_user)):
    """Send SMS cyclone/SOS alert. Requires admin or system role."""
    if current_user.role not in ["admin", "system"]:
        raise HTTPException(status_code=403, detail="Only admin/system can send alerts")
    notifier = _get_sms_notifier()
    if not notifier:
        raise HTTPException(status_code=503, detail="SMS service not configured")
    try:
        result = await notifier.send(phone_number=request.phone_number, message=request.message)
        logger.info(f"SMS sent to {request.phone_number} cyclone={request.cyclone_id}")
        return {"status": "sent", "result": result}
    except Exception as e:
        logger.error(f"SMS send failed: {e}")
        raise HTTPException(status_code=500, detail=f"SMS delivery failed: {str(e)}")

@router.post("/email", tags=["Notifications"])
async def send_email_alert(request: EmailAlertRequest, current_user: User = Depends(get_current_user)):
    """Send email cyclone/SOS alert. Requires admin or system role."""
    if current_user.role not in ["admin", "system"]:
        raise HTTPException(status_code=403, detail="Only admin/system can send alerts")
    svc = _get_email_service()
    if not svc:
        raise HTTPException(status_code=503, detail="Email service not configured")
    try:
        result = await svc.send(to=request.recipient_email, subject=request.subject, body=request.body)
        logger.info(f"Email sent to {request.recipient_email} cyclone={request.cyclone_id}")
        return {"status": "sent", "result": result}
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        raise HTTPException(status_code=500, detail=f"Email delivery failed: {str(e)}")

@router.get("/health", tags=["Notifications"])
async def notifications_health():
    """Returns availability of SMS and Email services + current mode (remote/local)."""
    groq_mode = bool(os.getenv("GROQ_API_KEY"))
    sms_ok = _get_sms_notifier() is not None
    email_ok = _get_email_service() is not None
    return {
        "mode": "remote_groq" if groq_mode else "local_native",
        "sms": "available" if sms_ok else "unavailable",
        "email": "available" if email_ok else "unavailable",
    }
