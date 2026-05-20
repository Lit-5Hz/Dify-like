from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _credential_key_path() -> Path:
    # 这里保存的是“服务端加密主钥”，不是用户填写的模型 API key。
    # storage/ 已经被 .gitignore 忽略，适合作为本地 MVP 的私有运行数据目录。
    return Path(get_settings().storage_dir) / "model_credentials.key"


@lru_cache
def _fernet() -> Fernet:
    key_path = _credential_key_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        key = key_path.read_bytes().strip()
        if not key:
            raise ValueError(f"Credential encryption key file is empty: {key_path}")
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt stored model credential.") from exc


def mask_secret(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    prefix = cleaned[:2]
    suffix = cleaned[-4:]
    return f"{prefix}{'*' * max(len(cleaned) - len(prefix) - len(suffix), 4)}{suffix}"
