import base64
import hashlib
import hmac
import secrets


_TOKEN_PREFIX = "rnr_"
_TOKEN_ENTROPY_BYTES = 24
_SALT_BYTES = 16
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_DIGEST = "sha256"


def generate_runner_token() -> str:
    return f"{_TOKEN_PREFIX}{secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)}"


def generate_salt() -> bytes:
    return secrets.token_bytes(_SALT_BYTES)


def hash_token(token: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        _PBKDF2_DIGEST,
        token.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(digest).decode("ascii")


def verify_token(token: str, salt: bytes, expected_hash: str) -> bool:
    computed_hash = hash_token(token, salt)
    return hmac.compare_digest(computed_hash, expected_hash)
