"""Авторизация: пользователи с логином/паролем и ролями.

Роли:
  owner — владелец: всё, включая управление пользователями и токенами ботов
  staff — сотрудник: работа с ботами/воронками/чатами (можно ограничить списком ботов)
"""
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_bearer = HTTPBearer(auto_error=False)
TOKEN_TTL = 30 * 24 * 3600  # 30 дней


# ---------- пароли ----------

def hash_password(password: str) -> str:
    """PBKDF2-SHA256 со случайной солью."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 120_000)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:  # noqa: BLE001
        return False


# ---------- токены сессии ----------

def _sign(payload_b64: str) -> str:
    return hmac.new(settings.secret_key.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def make_token(user) -> str:
    payload = {"uid": user.id, "role": user.role, "exp": int(time.time()) + TOKEN_TTL}
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{p}.{_sign(p)}"


def parse_token(token: str) -> dict | None:
    try:
        p, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(p)):
            return None
        data = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:  # noqa: BLE001
        return None


# ---------- зависимости FastAPI ----------

async def current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    from .db import SessionLocal
    from .models import User

    if cred is None:
        raise HTTPException(401, "Не авторизован")
    data = parse_token(cred.credentials)
    if not data:
        raise HTTPException(401, "Сессия истекла — войдите заново")
    async with SessionLocal() as session:
        user = await session.get(User, data["uid"])
        if user is None or not user.is_active:
            raise HTTPException(401, "Доступ отключён")
        return user


async def require_auth(user=Depends(current_user)):
    """Любой активный пользователь."""
    return user


async def require_owner(user=Depends(current_user)):
    """Только владелец: управление людьми, токенами ботов, ключами AI."""
    if user.role != "owner":
        raise HTTPException(403, "Доступно только владельцу")
    return user


def user_can_bot(user, bot_id: int) -> bool:
    """Есть ли у пользователя доступ к конкретному боту."""
    if user.role == "owner" or not user.bot_ids:
        return True
    return int(bot_id) in [int(x) for x in user.bot_ids]


async def ensure_bot_access(user, bot_id: int):
    if not user_can_bot(user, bot_id):
        raise HTTPException(403, "Нет доступа к этому боту")


# ---------- первичная настройка ----------

async def ensure_owner_exists():
    """Создаёт владельца из ADMIN_PASSWORD при первом запуске
    (миграция со старой схемы «один пароль»)."""
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import User

    async with SessionLocal() as session:
        exists = (await session.execute(select(User.id))).first()
        if exists:
            return
        login = os.getenv("ADMIN_LOGIN", "admin")
        user = User(
            login=login,
            name="Владелец",
            password_hash=hash_password(settings.admin_password),
            role="owner",
            is_active=True,
            bot_ids=[],
        )
        session.add(user)
        await session.commit()
