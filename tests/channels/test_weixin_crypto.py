from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from hypo_agent.channels.weixin.crypto import decrypt_media, encrypt_media, generate_aes_key


def test_weixin_crypto_round_trip() -> None:
    key = generate_aes_key()
    payload = b"hello weixin media"

    encrypted = encrypt_media(payload, key)
    decrypted = decrypt_media(encrypted, key)

    assert encrypted != payload
    assert decrypted == payload


def test_weixin_crypto_supports_empty_payload() -> None:
    key = generate_aes_key()

    encrypted = encrypt_media(b"", key)
    decrypted = decrypt_media(encrypted, key)

    assert decrypted == b""


def test_weixin_crypto_rejects_invalid_key_length() -> None:
    with pytest.raises(ValueError):
        encrypt_media(b"payload", b"short")

    with pytest.raises(ValueError):
        decrypt_media(b"payload", b"short")


def test_weixin_crypto_decrypts_raw_ecb_payload_without_pkcs7_padding() -> None:
    key = generate_aes_key()
    payload = b"0123456789abcdef" * 2
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(payload) + encryptor.finalize()

    decrypted = decrypt_media(encrypted, key)

    assert decrypted == payload
