import os
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import Request, HTTPException
from app.db import connect

_password_hasher = PasswordHasher()

# Single-owner administrator allowlist. Role alone never grants admin access.
ADMIN_ALLOWED_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()


def is_authorized_admin(user) -> bool:
    return bool(user and user.get("role") == "admin" and
                (user.get("email") or "").strip().lower() == ADMIN_ALLOWED_EMAIL)

class AuthError(Exception):
    pass

def hash_password(password: str) -> str:
    return _password_hasher.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False

def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token

def check_csrf(request: Request, token: str):
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(expected, token or ""):
        raise HTTPException(status_code=403, detail="CSRF-проверка не пройдена. Обнови страницу и попробуй снова.")

def current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with connect() as con:
        row = con.execute("SELECT id,email,role,name,company,is_active,supplier_verification,risk_level,referral_code FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row["is_active"]:
        request.session.clear()
        return None
    return dict(row)

def require_user(request: Request, role: str | None = None):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Войди в аккаунт.")
    if role == "admin":
        if not is_authorized_admin(user):
            raise HTTPException(status_code=403, detail="Доступ к панели администратора запрещён.")
    elif role and user["role"] != role:
        raise HTTPException(status_code=403, detail="Недостаточно прав.")
    return user

def bootstrap_admin():
    configured_email = os.getenv("ADMIN_EMAIL", ADMIN_ALLOWED_EMAIL).strip().lower()
    if configured_email != ADMIN_ALLOWED_EMAIL:
        raise RuntimeError(f"ADMIN_EMAIL должен быть равен {ADMIN_ALLOWED_EMAIL}")
    email = ADMIN_ALLOWED_EMAIL
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password:
        return
    if len(password) < 12:
        raise RuntimeError("ADMIN_PASSWORD должен содержать не менее 12 символов")
    with connect() as con:
        existing = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            row = con.execute("SELECT role FROM users WHERE email=?", (email,)).fetchone()
            if row["role"] != "admin":
                raise RuntimeError("ADMIN_EMAIL уже занят обычным аккаунтом. Укажи отдельный email администратора.")
        else:
            con.execute("INSERT INTO users(email,password_hash,role,name) VALUES(?,?, 'admin','Администратор')",
                        (email, hash_password(password)))
        con.commit()
