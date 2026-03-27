"""Weixin iLink crypto helpers."""

from __future__ import annotations

import os
import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_BLOCK_SIZE_BITS = 128
_BLOCK_SIZE_BYTES = _BLOCK_SIZE_BITS // 8


def encrypt_media(data: bytes, aes_key: bytes) -> bytes:
    """Encrypt iLink media payloads with AES-128-ECB + PKCS7 padding."""
    key = _validate_key(aes_key)
    payload = bytes(data)
    padder = padding.PKCS7(_BLOCK_SIZE_BITS).padder()
    padded = padder.update(payload) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_media(data: bytes, aes_key: bytes) -> bytes:
    """Decrypt iLink media payloads with AES-128-ECB.

    iLink media observed in the wild is not fully consistent: some payloads are
    PKCS7 padded, while others are already block-aligned raw plaintext after ECB
    decryption. Prefer PKCS7 unpadding when it validates, otherwise keep the raw
    decrypted bytes.
    """
    key = _validate_key(aes_key)
    payload = bytes(data)
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(payload) + decryptor.finalize()
    if not decrypted:
        return b""
    unpadder = padding.PKCS7(_BLOCK_SIZE_BITS).unpadder()
    try:
        return unpadder.update(decrypted) + unpadder.finalize()
    except ValueError:
        return decrypted


def generate_aes_key() -> bytes:
    """Generate a random 16-byte AES key."""
    return os.urandom(_BLOCK_SIZE_BYTES)


def encrypted_media_size(raw_size: int) -> int:
    """Return AES-128-ECB ciphertext size after PKCS7 padding."""
    size = max(0, int(raw_size))
    return ((size + 1 + (_BLOCK_SIZE_BYTES - 1)) // _BLOCK_SIZE_BYTES) * _BLOCK_SIZE_BYTES


def encode_aes_key_hex(aes_key: bytes) -> str:
    key = _validate_key(aes_key)
    return key.hex()


def encode_aes_key_base64hex(aes_key: bytes) -> str:
    key = _validate_key(aes_key)
    return base64.b64encode(key.hex().encode("utf-8")).decode("ascii")


def _validate_key(aes_key: bytes) -> bytes:
    key = bytes(aes_key)
    if len(key) != _BLOCK_SIZE_BYTES:
        raise ValueError("aes_key must be exactly 16 bytes for AES-128-ECB")
    return key
