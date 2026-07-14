"""Auth minimale ma reale per un'app mono-utente esposta su internet.

Il token HMAC firmato viaggia in TRE modi (in ordine di priorità):
1. header `Authorization: Bearer <token>`  — usato dalla SPA (localStorage),
   immune ai blocchi dei cookie di terze parti (iframe di Hugging Face);
2. cookie HttpOnly `ev_session` — comodo su URL diretto;
3. query param `?t=<token>` — per le risorse media (<video src>, <img>, download)
   che non possono inviare header.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request, Response

from .config import get_settings
from .schemas import LoginIn
from .security import client_ip, get_login_rate_limiter

COOKIE_NAME = "ev_session"


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(secret: str, days: int) -> str:
    exp = str(int(time.time()) + days * 86400)
    return f"{exp}.{_sign(secret, exp)}"


def verify_token(secret: str, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(secret, exp), sig):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie
    return request.query_params.get("t")


def require_auth(request: Request) -> None:
    settings = get_settings()
    if not verify_token(settings.resolved_secret(), _extract_token(request)):
        raise HTTPException(status_code=401, detail="Non autenticato")


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    settings = get_settings()
    limiter = get_login_rate_limiter()
    ip = client_ip(request)
    if limiter.is_blocked(ip):
        raise HTTPException(
            status_code=429,
            detail="Troppi tentativi di login: riprova tra qualche minuto.",
            headers={"Retry-After": str(limiter.retry_after(ip))},
        )
    # confronto su bytes: compare_digest su str solleva TypeError con caratteri
    # non-ASCII (password con accenti) -> 500 e fallimento non conteggiato.
    if not hmac.compare_digest(body.password.encode("utf-8"),
                               settings.admin_password.encode("utf-8")):
        limiter.record(ip)  # conta solo i fallimenti
        raise HTTPException(status_code=401, detail="Password errata")
    limiter.reset(ip)  # login riuscito: nessuna penalità per gli utenti legittimi
    token = make_token(settings.resolved_secret(), settings.session_days)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=settings.session_days * 86400,
        httponly=True, samesite="lax", path="/",
    )
    return {"ok": True, "token": token}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    settings = get_settings()
    ok = verify_token(settings.resolved_secret(), _extract_token(request))
    return {"authenticated": ok}
