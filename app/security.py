import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
from cryptography.fernet import Fernet
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import LoginThrottle, User, UserSession


password_hasher = PasswordHash.recommended()
settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; PostgreSQL preserves it."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalise_email(value: str) -> str:
    return value.strip().lower()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    return bool(encoded and password_hasher.verify(password, encoded))


def password_is_strong(password: str) -> bool:
    return (
        len(password) >= 14
        and any(ch.islower() for ch in password)
        and any(ch.isupper() for ch in password)
        and any(ch.isdigit() for ch in password)
        and any(not ch.isalnum() for ch in password)
    )


def opaque_token(size: int = 32) -> str:
    return secrets.token_urlsafe(size)


def token_hash(token: str) -> str:
    return hmac.new(settings.session_pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def encrypt_secret(value: str) -> str:
    return Fernet(settings.field_encryption_key.encode()).encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return Fernet(settings.field_encryption_key.encode()).decrypt(value.encode()).decode()


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Ivory Digital Manager")


def verify_totp(secret: str, code: str) -> bool:
    return bool(code and pyotp.TOTP(secret).verify(code.replace(" ", ""), valid_window=1))


def recovery_codes() -> tuple[list[str], list[str]]:
    plain = [f"{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}" for _ in range(8)]
    return plain, [token_hash(item) for item in plain]


def create_session(db: Session, user: User, ip: str | None, agent: str | None,
                   assurance: str = "password") -> tuple[UserSession, str, str]:
    raw_token, csrf = opaque_token(36), opaque_token(24)
    row = UserSession(
        user_id=user.id,
        token_hash=token_hash(raw_token),
        csrf_hash=token_hash(csrf),
        assurance=assurance,
        expires_at=utcnow() + timedelta(hours=settings.session_hours),
        ip_address=(ip or "")[:64] or None,
        user_agent=(agent or "")[:300] or None,
    )
    db.add(row)
    db.flush()
    return row, raw_token, csrf


def find_session(db: Session, raw_token: str | None) -> UserSession | None:
    if not raw_token:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash(raw_token)))
    if not row or row.revoked_at or as_utc(row.expires_at) <= utcnow() or not row.user.is_active:
        return None
    row.last_seen_at = utcnow()
    return row


def csrf_matches(session: UserSession, value: str | None) -> bool:
    return bool(value and hmac.compare_digest(session.csrf_hash, token_hash(value)))


def login_locked(db: Session, email: str) -> datetime | None:
    row = db.get(LoginThrottle, normalise_email(email))
    if row and row.locked_until and as_utc(row.locked_until) > utcnow():
        return row.locked_until
    return None


def record_login_failure(db: Session, email: str) -> None:
    key = normalise_email(email)
    row = db.get(LoginThrottle, key)
    if not row:
        row = LoginThrottle(email=key, failed_count=0)
        db.add(row)
    row.failed_count += 1
    row.last_failed_at = utcnow()
    if row.failed_count >= 5:
        row.locked_until = utcnow() + timedelta(minutes=15)
    db.commit()


def clear_login_failures(db: Session, email: str) -> None:
    row = db.get(LoginThrottle, normalise_email(email))
    if row:
        db.delete(row)
