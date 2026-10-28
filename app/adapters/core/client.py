from typing import Any

import httpx

from app.config import settings


class CoreUncertain(Exception):
    """The remote system may or may not have applied the request."""


class CoreRejected(Exception):
    """The remote system explicitly rejected the business operation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CoreClient:
    def __init__(self) -> None:
        self.base_url = settings.core_base_url.rstrip("/")
        self.timeout = settings.core_timeout_seconds

    def submit(self, operation_id: str, operation_type: str, payload: dict, scenario: str) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}/operations",
                json={
                    "operation_id": operation_id,
                    "operation_type": operation_type,
                    "payload": payload,
                    "scenario": scenario,
                },
                headers={"Idempotency-Key": operation_id},
                timeout=self.timeout,
                trust_env=False,
            )
        except httpx.HTTPError as exc:
            raise CoreUncertain("CORE request outcome is unknown") from exc
        if response.status_code in (400, 409, 422):
            try:
                detail = response.json().get("detail", "CORE rejected the operation")
            except ValueError:
                detail = "CORE rejected the operation"
            raise CoreRejected("CORE_REJECTED", str(detail))
        if response.status_code >= 500:
            raise CoreUncertain(f"CORE returned HTTP {response.status_code}")
        if response.is_error:
            raise CoreRejected("CORE_REJECTED", f"CORE returned HTTP {response.status_code}")
        return response.json()

    def lookup(self, operation_id: str) -> dict | None:
        try:
            response = httpx.get(
                f"{self.base_url}/operations/{operation_id}",
                timeout=self.timeout,
                trust_env=False,
            )
        except httpx.HTTPError as exc:
            raise CoreUncertain("CORE reconciliation request failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            raise CoreUncertain(f"CORE reconciliation returned HTTP {response.status_code}")
        response.raise_for_status()
        value: Any = response.json()
        return value
