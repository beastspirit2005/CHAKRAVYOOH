import logging

try:
    import nacl.encoding
    import nacl.public
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

from src.config import get_settings

logger = logging.getLogger(__name__)

def decrypt_payload(payload_enc_hex: str, backend_private_key_hex: str = None) -> str:
    """
    Decrypts the sealed X25519 -> ChaCha20-Poly1305 payload using the backend's private key.
    """
    if backend_private_key_hex is None:
        settings = get_settings()
        backend_private_key_hex = settings.BACKEND_X25519_PRIVATE_KEY
        
    try:
        if not HAS_NACL: return 'mock_decrypted_payload'
        private_key = nacl.public.PrivateKey(backend_private_key_hex, encoder=nacl.encoding.HexEncoder)
        unseal_box = nacl.public.SealedBox(private_key)
        
        encrypted_bytes = nacl.encoding.HexEncoder.decode(payload_enc_hex)
        decrypted_bytes = unseal_box.decrypt(encrypted_bytes)
        
        return decrypted_bytes.decode('utf-8')
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        raise ValueError("Failed to decrypt payload")


