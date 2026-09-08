import re

try:
    import nacl.encoding
    import nacl.public
    HAS_NACL = True
except ImportError:
    HAS_NACL = False
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.rbac import get_current_user
from src.config import get_settings
from src.database.models import User
from src.dependencies import get_db_session
from src.keys.registry import register_device_keys

router = APIRouter()

HEX_64_BYTE = re.compile(r"^[0-9a-fA-F]{128}$")   # 64 bytes = 128 hex chars


class KeyRegistrationRequest(BaseModel):
    origin_id: str
    origin_key_id: str
    ed25519_public_key: str
    x25519_public_key: str

    @field_validator("ed25519_public_key", "x25519_public_key")
    @classmethod
    def validate_hex_key(cls, v: str) -> str:
        """Rejects malformed keys before they poison the DB."""
        if not HEX_64_BYTE.match(v):
            raise ValueError("Public key must be a 128-character hex string (64 bytes).")
        return v.lower()


class KeyRegistrationResponse(BaseModel):
    status: str
    origin_key_id: str


@router.post("/register", response_model=KeyRegistrationResponse)
async def register_keys(
    request: KeyRegistrationRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),   # Fix #4: require authenticated user
):
    """
    Registers a device's public keypair during onboarding.
    Requires a valid JWT ??? prevents anonymous key poisoning of the trust anchor.
    The `origin_key_id` becomes the trust anchor for verifying SOS packets.
    """
    await register_device_keys(
        db,
        origin_key_id=request.origin_key_id,
        origin_id=request.origin_id,
        ed25519_pub=request.ed25519_public_key,
        x25519_pub=request.x25519_public_key
    )
    return KeyRegistrationResponse(status="registered", origin_key_id=request.origin_key_id)


@router.get("/backend-pubkey")
async def get_backend_pubkey():
    """
    Returns the backend's X25519 public key.
    Android clients use this to encrypt (SealedBox) the SOS payload before transmission.
    """
    settings = get_settings()
    priv_key_hex = settings.BACKEND_X25519_PRIVATE_KEY
    if not priv_key_hex:
        raise HTTPException(status_code=500, detail="Backend encryption key not configured")

    private_key = nacl.public.PrivateKey(priv_key_hex, encoder=nacl.encoding.HexEncoder)
    pub_key_hex = private_key.public_key.encode(encoder=nacl.encoding.HexEncoder).decode('utf-8')

    return {"backend_x25519_public_key": pub_key_hex}


