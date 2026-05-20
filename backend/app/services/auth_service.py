from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.schemas import UserCreate

PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 210_000
PASSWORD_SALT_BYTES = 16
TOKEN_BYTES = 32


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = password_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except Exception:
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def get_user_by_email(db: Session, email: str) -> User | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    return db.scalar(select(User).where(User.email == normalized))


def get_user_by_token(db: Session, token: str) -> User | None:
    clean_token = str(token or "").strip()
    if not clean_token:
        return None
    return db.scalar(select(User).where(User.auth_token == clean_token))


def create_user(db: Session, payload: UserCreate) -> tuple[User, str]:
    email = normalize_email(payload.email)
    if not email:
        raise ValueError("Email is required.")
    if get_user_by_email(db, email):
        raise ValueError("Email already registered.")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        auth_token=generate_token(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, str(user.auth_token or "")


def login_user(db: Session, payload: UserCreate) -> tuple[User, str]:
    email = normalize_email(payload.email)
    user = get_user_by_email(db, email)
    if not user or not verify_password(payload.password, user.password_hash):
        raise ValueError("Invalid email or password.")

    user.auth_token = generate_token()
    db.commit()
    db.refresh(user)
    return user, str(user.auth_token or "")
