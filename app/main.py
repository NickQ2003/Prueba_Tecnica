import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.adapters.persistence.models import (
    IpcRate,
    Operation,
    OperationStatus,
    Party,
    Policy,
    PolicyPeriod,
    Risk,
)
from app.adapters.persistence.session import SessionLocal, get_session
from app.application import policy_operations as use_cases
from app.config import settings
from app.domain.errors import ConflictError, DomainError, NotFoundError, ValidationError
from app.entrypoints.api.schemas import (
    IpcRateCreate,
    IpcRateRead,
    OperationRead,
    PaginatedPolicies,
    PartyCreate,
    PartyRead,
    PeriodRead,
    PolicyCreate,
    PolicyDetail,
    PolicyPatch,
    PolicyRead,
    RenewalCreate,
    ResumeOperation,
    RiskCreate,
    RiskRead,
)
from app.entrypoints.api.security import Actor, require_role

logging.basicConfig(level=settings.log_level.upper(), format="%(message)s")
logger = logging.getLogger("policy_api")
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP request duration", ["method"])
OPERATION_COUNT = Gauge("business_operations", "Persisted operations by state", ["status"])
OPERATION_OLDEST_AGE = Gauge(
    "business_operation_oldest_age_seconds",
    "Oldest operation age by state",
    ["status"],
)
OPERATION_ATTEMPTS = Gauge(
    "business_operation_attempts", "Persisted CORE attempts by operation state", ["status"]
)

app = FastAPI(
    title="Rental Policy Management API",
    version="1.0.0",
    description=(
        "Demo for individual and collective rental policies. Contractual changes "
        "are accepted as durable operations and confirmed asynchronously by a simulated CORE."
    ),
    docs_url="/docs",
    openapi_url="/openapi.json",
)
SessionDependency = Annotated[Session, Depends(get_session)]
ReadActor = Annotated[Actor, Depends(require_role("read", "operator", "reviewer"))]
OperatorActor = Annotated[Actor, Depends(require_role("operator"))]
ReviewerActor = Annotated[Actor, Depends(require_role("reviewer"))]


@app.middleware("http")
async def request_context(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))[:80]
    request.state.correlation_id = correlation_id
    started = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - started
    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    HTTP_REQUESTS.labels(request.method, str(response.status_code)).inc()
    HTTP_LATENCY.labels(request.method).observe(duration)
    response.headers["X-Correlation-ID"] = correlation_id
    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "method": request.method,
                "route": path,
                "status": response.status_code,
                "duration_seconds": round(duration, 6),
                "correlation_id": correlation_id,
            }
        )
    )
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(_request: Request, exc: DomainError) -> Response:
    if isinstance(exc, NotFoundError):
        code = 404
    elif isinstance(exc, ConflictError):
        code = 409
    elif isinstance(exc, ValidationError):
        code = 422
    else:
        code = 400
    return Response(
        content=json.dumps({"detail": str(exc)}),
        status_code=code,
        media_type="application/json",
    )


def _correlation(request: Request) -> str:
    return request.state.correlation_id


def _scenario(header_value: str | None) -> str:
    if header_value is None:
        return "success"
    if not settings.demo_controls_enabled:
        raise HTTPException(status_code=403, detail="CORE demo scenarios are disabled")
    if header_value not in {
        "success",
        "reject",
        "unavailable",
        "slow",
        "timeout_after_apply",
        "mismatch",
    }:
        raise HTTPException(status_code=422, detail="unsupported CORE demo scenario")
    return header_value


def _accepted(operation: Operation, response: Response) -> OperationRead:
    response.status_code = status.HTTP_202_ACCEPTED
    response.headers["Location"] = f"/api/v1/operations/{operation.id}"
    return OperationRead.model_validate(operation)


@app.get("/health/live", tags=["health"])
def health_live() -> dict:
    return {"status": "live"}


@app.get("/health/ready", tags=["health"])
def health_ready(session: SessionDependency) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    for operation_status in OperationStatus:
        OPERATION_COUNT.labels(operation_status.value).set(0)
        OPERATION_ATTEMPTS.labels(operation_status.value).set(0)
        OPERATION_OLDEST_AGE.labels(operation_status.value).set(0)
    with SessionLocal() as session:
        stats = session.execute(
            select(
                Operation.status,
                func.count(),
                func.min(Operation.created_at),
                func.sum(Operation.attempts),
            ).group_by(Operation.status)
        ).all()
    for operation_status, count, oldest_created_at, attempts in stats:
        OPERATION_COUNT.labels(operation_status).set(count)
        OPERATION_ATTEMPTS.labels(operation_status).set(attempts or 0)
        age = (
            max(0.0, (datetime.now(UTC) - oldest_created_at).total_seconds())
            if oldest_created_at
            else 0.0
        )
        OPERATION_OLDEST_AGE.labels(operation_status).set(age)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/parties", response_model=PartyRead, status_code=201, tags=["parties"])
def create_party(body: PartyCreate, session: SessionDependency, actor: OperatorActor) -> Party:
    return use_cases.create_party(session, body.model_dump())


@app.post(
    "/api/v1/policies",
    response_model=OperationRead,
    status_code=202,
    tags=["policies"],
    responses={202: {"description": "Operation durably accepted; use Location to poll its status"}},
)
def create_policy(
    body: PolicyCreate,
    request: Request,
    response: Response,
    session: SessionDependency,
    actor: OperatorActor,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    demo_scenario: Annotated[str | None, Header(alias="X-Demo-Core-Scenario")] = None,
) -> OperationRead:
    operation = use_cases.request_policy_creation(
        session,
        payload=body.model_dump(mode="json"),
        consumer_scope=actor.subject,
        idempotency_key=idempotency_key,
        correlation_id=_correlation(request),
        actor=actor.subject,
        demo_scenario=_scenario(demo_scenario),
    )
    return _accepted(operation, response)


@app.get("/api/v1/policies", response_model=PaginatedPolicies, tags=["policies"])
def list_policies(
    session: SessionDependency,
    actor: ReadActor,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    policy_type: str | None = Query(default=None, pattern="^(INDIVIDUAL|COLLECTIVE)$"),
    policy_status: str | None = Query(default=None, alias="status", pattern="^(ACTIVE|CANCELLED)$"),
) -> PaginatedPolicies:
    filters = []
    if policy_type:
        filters.append(Policy.policy_type == policy_type)
    if policy_status:
        filters.append(Policy.status == policy_status)
    total = session.scalar(select(func.count()).select_from(Policy).where(*filters)) or 0
    items = session.scalars(
        select(Policy)
        .where(*filters)
        .order_by(Policy.created_at.desc(), Policy.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PaginatedPolicies(items=items, limit=limit, offset=offset, total=total)


@app.get("/api/v1/policies/{policy_id}", response_model=PolicyDetail, tags=["policies"])
def get_policy(policy_id: uuid.UUID, session: SessionDependency, actor: ReadActor) -> PolicyDetail:
    policy = session.get(Policy, policy_id)
    if policy is None:
        raise NotFoundError("policy not found")
    risks = session.scalars(
        select(Risk).where(Risk.policy_id == policy_id).order_by(Risk.risk_number)
    ).all()
    values = PolicyRead.model_validate(policy).model_dump()
    return PolicyDetail(**values, risks=risks)


@app.patch("/api/v1/policies/{policy_id}", response_model=PolicyRead, tags=["policies"])
def patch_policy(
    policy_id: uuid.UUID,
    body: PolicyPatch,
    session: SessionDependency,
    actor: OperatorActor,
) -> Policy:
    policy = session.scalar(select(Policy).where(Policy.id == policy_id).with_for_update())
    if policy is None:
        raise NotFoundError("policy not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(policy, field, value)
    session.commit()
    session.refresh(policy)
    return policy


@app.post(
    "/api/v1/policies/{policy_id}/risks",
    response_model=OperationRead,
    status_code=202,
    tags=["risks"],
)
def add_risk(
    policy_id: uuid.UUID,
    body: RiskCreate,
    request: Request,
    response: Response,
    session: SessionDependency,
    actor: OperatorActor,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    demo_scenario: Annotated[str | None, Header(alias="X-Demo-Core-Scenario")] = None,
) -> OperationRead:
    operation = use_cases.request_risk_addition(
        session,
        policy_id=policy_id,
        payload=body.model_dump(mode="json"),
        consumer_scope=actor.subject,
        idempotency_key=idempotency_key,
        correlation_id=_correlation(request),
        actor=actor.subject,
        demo_scenario=_scenario(demo_scenario),
    )
    return _accepted(operation, response)


@app.get("/api/v1/policies/{policy_id}/risks", response_model=list[RiskRead], tags=["risks"])
def list_risks(policy_id: uuid.UUID, session: SessionDependency, actor: ReadActor) -> list[Risk]:
    if session.get(Policy, policy_id) is None:
        raise NotFoundError("policy not found")
    risks = session.scalars(
        select(Risk).where(Risk.policy_id == policy_id).order_by(Risk.risk_number)
    ).all()
    return risks


@app.post(
    "/api/v1/policies/{policy_id}/risks/{risk_id}/cancellations",
    response_model=OperationRead,
    status_code=202,
    tags=["risks"],
)
def cancel_risk(
    policy_id: uuid.UUID,
    risk_id: uuid.UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    actor: OperatorActor,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    demo_scenario: Annotated[str | None, Header(alias="X-Demo-Core-Scenario")] = None,
) -> OperationRead:
    operation = use_cases.request_risk_cancellation(
        session,
        policy_id=policy_id,
        risk_id=risk_id,
        consumer_scope=actor.subject,
        idempotency_key=idempotency_key,
        correlation_id=_correlation(request),
        actor=actor.subject,
        demo_scenario=_scenario(demo_scenario),
    )
    return _accepted(operation, response)


@app.post(
    "/api/v1/policies/{policy_id}/renewals",
    response_model=OperationRead,
    status_code=202,
    tags=["renewals"],
)
def renew_policy(
    policy_id: uuid.UUID,
    body: RenewalCreate,
    request: Request,
    response: Response,
    session: SessionDependency,
    actor: OperatorActor,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    demo_scenario: Annotated[str | None, Header(alias="X-Demo-Core-Scenario")] = None,
) -> OperationRead:
    operation = use_cases.request_renewal(
        session,
        policy_id=policy_id,
        ipc_rate_id=body.ipc_rate_id,
        consumer_scope=actor.subject,
        idempotency_key=idempotency_key,
        correlation_id=_correlation(request),
        actor=actor.subject,
        demo_scenario=_scenario(demo_scenario),
    )
    return _accepted(operation, response)


@app.get("/api/v1/policies/{policy_id}/periods", response_model=list[PeriodRead], tags=["renewals"])
def list_periods(
    policy_id: uuid.UUID, session: SessionDependency, actor: ReadActor
) -> list[PolicyPeriod]:
    if session.get(Policy, policy_id) is None:
        raise NotFoundError("policy not found")
    return session.scalars(
        select(PolicyPeriod)
        .where(PolicyPeriod.policy_id == policy_id)
        .order_by(PolicyPeriod.sequence)
    ).all()


@app.get("/api/v1/operations/{operation_id}", response_model=OperationRead, tags=["operations"])
def get_operation(
    operation_id: uuid.UUID, session: SessionDependency, actor: ReadActor
) -> Operation:
    operation = session.get(Operation, operation_id)
    if operation is None:
        raise NotFoundError("operation not found")
    return operation


@app.post(
    "/api/v1/operations/{operation_id}/resume", response_model=OperationRead, tags=["operations"]
)
def resume_operation(
    operation_id: uuid.UUID,
    body: ResumeOperation,
    request: Request,
    session: SessionDependency,
    actor: ReviewerActor,
) -> Operation:
    return use_cases.resume_renewal(
        session,
        operation_id=operation_id,
        ipc_rate_id=body.ipc_rate_id,
        actor=actor.subject,
        correlation_id=_correlation(request),
    )


@app.post("/api/v1/operations/{operation_id}/reconcile", response_model=dict, tags=["operations"])
def reconcile(operation_id: uuid.UUID, actor: ReviewerActor) -> dict:
    from app.application.operation_processor import reconcile_operation

    return reconcile_operation(operation_id)


@app.post("/api/v1/ipc-rates", response_model=IpcRateRead, status_code=201, tags=["renewals"])
def create_ipc_rate(
    body: IpcRateCreate, session: SessionDependency, actor: ReviewerActor
) -> IpcRate:
    return use_cases.create_ipc_rate(session, body.model_dump())


@app.get("/api/v1/ipc-rates", response_model=list[IpcRateRead], tags=["renewals"])
def list_ipc_rates(session: SessionDependency, actor: ReadActor) -> list[IpcRate]:
    return session.scalars(select(IpcRate).order_by(IpcRate.effective_on.desc())).all()
