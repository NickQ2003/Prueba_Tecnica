from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from app.config import settings


@dataclass(frozen=True)
class Actor:
    subject: str
    role: str


def get_actor(authorization: str | None = Header(default=None)) -> Actor:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    tokens = {
        settings.api_read_token: Actor("demo-reader", "read"),
        settings.api_operator_token: Actor("demo-operator", "operator"),
        settings.api_reviewer_token: Actor("demo-reviewer", "reviewer"),
    }
    actor = tokens.get(token)
    if actor is None:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    return actor


def require_role(*roles: str) -> Callable:
    def dependency(actor: Actor = Depends(get_actor)) -> Actor:
        allowed = set(roles)
        if actor.role not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient permission")
        return actor

    return dependency
