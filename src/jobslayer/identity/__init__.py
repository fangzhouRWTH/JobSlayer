"""Provider-neutral authenticated identity and authorization contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import DomainModel, RiskLevel


class AuthenticationMethod(str, Enum):
    LOCAL_SIGNED_SESSION = "local_signed_session"
    OIDC = "oidc"
    MTLS = "mtls"


class AuthorizationAction(str, Enum):
    VIEW_CONTROL_PLANE = "view_control_plane"
    EXECUTE_TASK = "execute_task"
    REVIEW_IMPLEMENTATION = "review_implementation"
    RECORD_DECISION = "record_decision"
    APPLY_DECISION = "apply_decision"
    INTEGRATE_SOURCE = "integrate_source"
    CLEANUP_WORKSPACE = "cleanup_workspace"
    RECOVER_RUN = "recover_run"
    MANAGE_WORKER = "manage_worker"
    MANAGE_TASK_PLAN = "manage_task_plan"
    USE_QUICK_AGENT = "use_quick_agent"
    EXECUTE_QUICK_AGENT = "execute_quick_agent"
    ASSIST_HUMAN_DECISION = "assist_human_decision"


class AuthenticatedPrincipal(DomainModel):
    """Short-lived identity established outside an Agent execution."""

    schema_version: str = "1.0"
    session_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    subject_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
    display_name: str = Field(min_length=1)
    roles: tuple[str, ...] = Field(min_length=1)
    authentication_method: AuthenticationMethod
    issuer: str = Field(min_length=1)
    authenticated_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_session(self) -> AuthenticatedPrincipal:
        if self.authenticated_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("identity session timestamps must include a timezone")
        if self.valid_until <= self.authenticated_at:
            raise ValueError("identity session must expire after authentication")
        if len(self.roles) != len(set(self.roles)) or any(
            not role.strip() for role in self.roles
        ):
            raise ValueError("identity roles must be unique and non-blank")
        return self

    def is_active(self, now: datetime | None = None) -> bool:
        when = now or datetime.now(UTC)
        if when.tzinfo is None:
            raise ValueError("identity check time must include a timezone")
        return self.authenticated_at <= when < self.valid_until


class SignedIdentitySession(DomainModel):
    schema_version: str = "1.0"
    key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    principal: AuthenticatedPrincipal
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuthorizationRequest(DomainModel):
    schema_version: str = "1.0"
    principal: AuthenticatedPrincipal
    action: AuthorizationAction
    task_id: str | None = None
    run_id: str | None = None
    risk: RiskLevel | None = None


class AuthorizationVerdict(DomainModel):
    schema_version: str = "1.0"
    permitted: bool
    policy_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    action: AuthorizationAction
    reason: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IdentityVerifier(Protocol):
    def verify(
        self,
        session: SignedIdentitySession,
        *,
        now: datetime | None = None,
    ) -> AuthenticatedPrincipal:
        """Verify issuer signature and validity, returning the bound principal."""


class Authorizer(Protocol):
    def authorize(
        self,
        request: AuthorizationRequest,
        *,
        now: datetime | None = None,
    ) -> AuthorizationVerdict:
        """Return a deterministic allow/deny result without changing state."""


class AuthorizationDeniedError(RuntimeError):
    """Raised before a protected operation when a verdict denies it."""


def require_authorized(verdict: AuthorizationVerdict) -> None:
    if not verdict.permitted:
        raise AuthorizationDeniedError(verdict.reason)


class AgentCredentialGrant(DomainModel):
    """Non-secret evidence for a short-lived, least-privilege agent credential."""

    schema_version: str = "1.0"
    grant_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    audience: str = Field(min_length=1)
    scopes: tuple[str, ...] = Field(min_length=1)
    issued_at: datetime
    valid_until: datetime
    broker_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_grant(self) -> AgentCredentialGrant:
        if self.issued_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("credential grant timestamps must include a timezone")
        if self.valid_until <= self.issued_at:
            raise ValueError("credential grant must expire after issuance")
        if len(self.scopes) != len(set(self.scopes)) or any(
            not scope.strip() for scope in self.scopes
        ):
            raise ValueError("credential scopes must be unique and non-blank")
        return self

    def is_active(self, now: datetime | None = None) -> bool:
        when = now or datetime.now(UTC)
        if when.tzinfo is None:
            raise ValueError("credential check time must include a timezone")
        return self.issued_at <= when < self.valid_until


class AgentCredentialBroker(Protocol):
    def issue(
        self,
        *,
        run_id: str,
        audience: str,
        scopes: tuple[str, ...],
        valid_until: datetime,
    ) -> AgentCredentialGrant:
        """Mint or bind a run-scoped credential without returning secret material."""

    def revoke(self, grant_id: str) -> None:
        """Revoke the exact grant idempotently after the worker is terminal."""


__all__ = [
    "AuthenticatedPrincipal",
    "AgentCredentialBroker",
    "AgentCredentialGrant",
    "AuthenticationMethod",
    "AuthorizationAction",
    "AuthorizationDeniedError",
    "AuthorizationRequest",
    "AuthorizationVerdict",
    "Authorizer",
    "IdentityVerifier",
    "SignedIdentitySession",
    "require_authorized",
]
