"""Unit tests for crypto module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.crypto import CredentialCipher, get_cipher
from src.core.exceptions import ConfigError


def test_credential_cipher_encrypt_decrypt() -> None:
    """CredentialCipher should encrypt and decrypt successfully."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    cipher = CredentialCipher(keys=[key])

    original = "test_secret_token"
    encrypted = cipher.encrypt(original)
    decrypted = cipher.decrypt(encrypted)

    assert decrypted == original
    assert encrypted != original


def test_credential_cipher_empty_keys() -> None:
    """CredentialCipher with empty keys should raise ConfigError."""
    with pytest.raises(ConfigError):
        CredentialCipher(keys=[])


def test_credential_cipher_invalid_key() -> None:
    """CredentialCipher with invalid key should raise ConfigError."""
    with pytest.raises(ConfigError):
        CredentialCipher(keys=["invalid_key"])


def test_get_cipher() -> None:
    """get_cipher should return a CredentialCipher instance."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    with patch("src.core.crypto.settings") as mock_settings:
        mock_settings.credential_keys_list = [key]
        cipher = get_cipher()
        assert cipher is not None
        assert isinstance(cipher, CredentialCipher)


def test_credential_cipher_multi_key() -> None:
    """CredentialCipher should support multiple keys for rotation."""
    from cryptography.fernet import Fernet

    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    cipher = CredentialCipher(keys=[key1, key2])

    original = "test_token"
    encrypted = cipher.encrypt(original)
    decrypted = cipher.decrypt(encrypted)

    assert decrypted == original
