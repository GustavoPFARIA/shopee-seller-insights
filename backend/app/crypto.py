"""Symmetric encryption for third-party credentials stored in the database."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class TokenCryptoError(Exception):
    pass


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if key is None:
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.get_secret_value().encode())
    except ValueError as exc:
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY must be a Fernet key") from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenCryptoError("Stored credential cannot be decrypted (key changed?)") from exc
