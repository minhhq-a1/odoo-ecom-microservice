"""Envelope encryption for credentials at rest. Multi-key Fernet."""
from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet

from src.core.config import settings
from src.core.exceptions import ConfigError


class CredentialCipher:
    """
    Multi-key Fernet for key rotation.
    settings.CREDENTIAL_KEYS comma-separated: first key = active (encrypt + decrypt),
    rest = legacy (decrypt only). Rotate by prepending new key, then run
    scripts/rotate_credentials.py.
    """

    def __init__(self, keys: list[str] | None = None) -> None:
        keys = keys if keys is not None else settings.credential_keys_list
        if not keys:
            raise ConfigError("CREDENTIAL_KEYS env required (comma-separated Fernet keys)")
        try:
            self._fernet = MultiFernet([Fernet(k.encode()) for k in keys])
        except Exception as e:
            raise ConfigError(f"Invalid Fernet key in CREDENTIAL_KEYS: {e}") from e

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

    def rotate(self, token: str) -> str:
        return self._fernet.rotate(token.encode()).decode()


_cipher: CredentialCipher | None = None


def get_cipher() -> CredentialCipher:
    global _cipher
    if _cipher is None:
        _cipher = CredentialCipher()
    return _cipher
