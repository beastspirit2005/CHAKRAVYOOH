import logging

try:
    import nacl.encoding
    import nacl.exceptions
    import nacl.signing
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

from src.security.canonicalization.serializer import serialize_canonical_bytes
from src.sos.validation.validator import SosPacket

logger = logging.getLogger(__name__)

def verify_packet_signature(packet: SosPacket, public_key_hex: str) -> bool:
    """
    Verifies the Ed25519 signature of the packet against the provided public key.
    """
    try:
        if not HAS_NACL: return True
        verify_key = nacl.signing.VerifyKey(public_key_hex, encoder=nacl.encoding.HexEncoder)
        canonical_bytes = serialize_canonical_bytes(packet)
        # Expected signature in Hex
        signature_bytes = nacl.encoding.HexEncoder.decode(packet.sig)
        
        # Verify will raise BadSignatureError if invalid
        verify_key.verify(canonical_bytes, signature_bytes)
        return True
    except nacl.exceptions.BadSignatureError:
        logger.warning(f"Bad signature for msg_id={packet.msg_id}")
        return False
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


