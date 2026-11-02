"""Password hashing, JWT, secret encryption and log redaction."""

import json
import re
import secrets
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings
from .db import utcnow

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _load_or_create_key(name: str, factory) -> bytes:
    """Keys live in data/keys and are generated once; they are never logged or sent to clients."""
    keys_dir: Path = get_settings().keys_dir
    keys_dir.mkdir(parents=True, exist_ok=True)
    path = keys_dir / name
    if path.exists():
        return path.read_bytes().strip()
    key = factory()
    path.write_bytes(key)
    return key


@lru_cache
def _jwt_secret() -> bytes:
    return _load_or_create_key("jwt.key", lambda: secrets.token_urlsafe(48).encode())


@lru_cache
def _fernet() -> Fernet:
    return Fernet(_load_or_create_key("secrets.fernet", Fernet.generate_key))


def create_access_token(user_id: int, role: str) -> str:
    exp = utcnow() + timedelta(minutes=get_settings().jwt_expire_minutes)
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp}, _jwt_secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def encrypt_secrets(data: dict[str, str]) -> str | None:
    clean = {k: v for k, v in data.items() if v}
    if not clean:
        return None
    return _fernet().encrypt(json.dumps(clean).encode()).decode()


def decrypt_secrets(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return {}


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


def redact(text: str | None, known_secrets: list[str] | None = None, limit: int = 2000) -> str | None:
    """Mask secrets in text before it is persisted or sent to the UI, then truncate."""
    if text is None:
        return None
    for s in known_secrets or []:
        if s and len(s) >= 4:
            text = text.replace(s, "******")
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "******", text)
    if len(text) > limit:
        text = text[:limit] + f"…[truncated {len(text) - limit} chars]"
    return text
