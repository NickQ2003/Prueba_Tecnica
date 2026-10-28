import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

DB_PATH = os.environ.get("CORE_MOCK_DB", "/tmp/core-mock.sqlite3")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS applied_operations ("
            "operation_id TEXT PRIMARY KEY, operation_type TEXT NOT NULL, "
            "state TEXT NOT NULL, result_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.commit()
    yield


app = FastAPI(title="CORE simulator", docs_url=None, redoc_url=None, lifespan=lifespan)


class CoreCommand(BaseModel):
    operation_id: str
    operation_type: str
    payload: dict
    scenario: Literal[
        "success", "reject", "unavailable", "slow", "timeout_after_apply", "mismatch"
    ] = "success"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def make_result(command: CoreCommand) -> dict:
    result = {
        "operation_id": command.operation_id,
        "status": "CONFIRMED",
        "core_reference": f"MOCK-{command.operation_id[:12].upper()}",
        "applied_values": None,
    }
    renewal = command.payload.get("renewal")
    if renewal:
        result["applied_values"] = renewal["risk_values"]
        if command.scenario == "mismatch":
            result["applied_values"] = []
            for row in renewal["risk_values"]:
                rent = Decimal(row["monthly_rent"]) + Decimal("0.01")
                months = int(renewal["duration_months"])
                result["applied_values"].append(
                    {
                        **row,
                        "monthly_rent": str(rent),
                        "premium": str((rent * months).quantize(Decimal("0.01"))),
                    }
                )
    return result


@app.get("/health/ready")
def health_ready() -> dict:
    with closing(connect()) as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ready"}


@app.post("/operations")
def apply_operation(
    command: CoreCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    if idempotency_key != command.operation_id:
        raise HTTPException(status_code=400, detail="operation identity mismatch")
    if command.scenario == "unavailable":
        raise HTTPException(status_code=503, detail="simulated CORE outage")
    if command.scenario == "slow":
        time.sleep(5)
    if command.scenario == "reject":
        raise HTTPException(status_code=422, detail="simulated business rejection")

    with closing(connect()) as connection:
        existing = connection.execute(
            "SELECT result_json FROM applied_operations WHERE operation_id = ?",
            (command.operation_id,),
        ).fetchone()
        if existing:
            return json.loads(existing["result_json"])
        result = make_result(command)
        connection.execute(
            "INSERT INTO applied_operations(operation_id, operation_type, state, result_json) "
            "VALUES (?, ?, ?, ?)",
            (command.operation_id, command.operation_type, result["status"], json.dumps(result)),
        )
        connection.commit()
    if command.scenario == "timeout_after_apply":
        raise HTTPException(status_code=504, detail="simulated response lost after apply")
    return result


@app.get("/operations/{operation_id}")
def lookup_operation(operation_id: str) -> dict:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT result_json FROM applied_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="operation not found")
    return json.loads(row["result_json"])
