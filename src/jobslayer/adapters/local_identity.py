from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.domain.models import DomainModel
from jobslayer.domain.models import (
    ApprovalAuthority,
    ApprovalCredentialProof,
    DecisionKind,
    ExecutionCredentialProof,
    RiskLevel,
    TaskExecutionAuthorization,
    ActorType,
)
from jobslayer.identity import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    AuthorizationAction,
    AuthorizationRequest,
    AuthorizationVerdict,
    SignedIdentitySession,
)


class LocalIdentityError(RuntimeError):
    """Raised when a local signing key or session cannot be trusted."""


class _LocalIdentityKey(DomainModel):
    schema_version: str = "1.0"
    key_id: str
    secret_base64: str
    created_at: datetime


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class LocalIdentityProvider:
    """Create-only local HMAC issuer for short-lived operator sessions."""

    def __init__(self, key_path: str | Path):
        self.key_path = Path(key_path).resolve(strict=False)

    def create_key(self) -> str:
        key = _LocalIdentityKey(
            key_id=f"local-key-{uuid4().hex}",
            secret_base64=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(
                "ascii"
            ),
            created_at=datetime.now(UTC),
        )
        encoded = (_canonical(key.model_dump(mode="json")) + b"\n")
        try:
            self.key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                self.key_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                self.key_path.chmod(0o600)
        except FileExistsError as exc:
            raise LocalIdentityError(
                f"refusing to overwrite an existing identity key: {self.key_path}"
            ) from exc
        except OSError as exc:
            raise LocalIdentityError(
                f"could not create local identity key: {self.key_path}"
            ) from exc
        return key.key_id

    def issue(
        self,
        *,
        subject_id: str,
        display_name: str,
        roles: tuple[str, ...],
        lifetime: timedelta = timedelta(minutes=30),
        now: datetime | None = None,
    ) -> SignedIdentitySession:
        if lifetime <= timedelta(0) or lifetime > timedelta(hours=24):
            raise LocalIdentityError(
                "local session lifetime must be positive and no more than 24 hours"
            )
        key, secret = self._load_key()
        authenticated_at = now or datetime.now(UTC)
        if authenticated_at.tzinfo is None:
            raise LocalIdentityError("session issue time must include a timezone")
        principal = AuthenticatedPrincipal(
            session_id=f"session-{uuid4().hex}",
            subject_id=subject_id,
            display_name=display_name,
            roles=roles,
            authentication_method=AuthenticationMethod.LOCAL_SIGNED_SESSION,
            issuer=key.key_id,
            authenticated_at=authenticated_at,
            valid_until=authenticated_at + lifetime,
        )
        signature = hmac.new(
            secret,
            _canonical(principal.model_dump(mode="json")),
            hashlib.sha256,
        ).hexdigest()
        return SignedIdentitySession(
            key_id=key.key_id,
            principal=principal,
            signature=signature,
        )

    def verify(
        self,
        session: SignedIdentitySession,
        *,
        now: datetime | None = None,
    ) -> AuthenticatedPrincipal:
        key, secret = self._load_key()
        if session.key_id != key.key_id or session.principal.issuer != key.key_id:
            raise LocalIdentityError("identity session was signed by another issuer")
        expected = hmac.new(
            secret,
            _canonical(session.principal.model_dump(mode="json")),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, session.signature):
            raise LocalIdentityError("identity session signature is invalid")
        if not session.principal.is_active(now):
            raise LocalIdentityError("identity session is not currently valid")
        return session.principal

    def load_session(
        self,
        path: str | Path,
        *,
        now: datetime | None = None,
    ) -> AuthenticatedPrincipal:
        session = self.load_signed_session(path)
        return self.verify(session, now=now)

    @staticmethod
    def load_signed_session(path: str | Path) -> SignedIdentitySession:
        try:
            return SignedIdentitySession.model_validate_json(
                Path(path).read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise LocalIdentityError(
                "identity session file is unavailable or invalid"
            ) from exc

    def issue_approval_authority(
        self,
        session: SignedIdentitySession,
        *,
        allowed_decision_kinds: tuple[DecisionKind, ...],
        lifetime: timedelta = timedelta(minutes=15),
        now: datetime | None = None,
    ) -> ApprovalAuthority:
        if lifetime <= timedelta(0) or lifetime > timedelta(hours=1):
            raise LocalIdentityError(
                "approval authority lifetime must be positive and no more than one hour"
            )
        if not allowed_decision_kinds:
            raise LocalIdentityError("approval authority needs a decision kind")
        issued_at = now or datetime.now(UTC)
        principal = self.verify(session, now=issued_at)
        authorizer = RoleBasedAuthorizer()
        verdict = authorizer.authorize(
            AuthorizationRequest(
                principal=principal,
                action=AuthorizationAction.APPLY_DECISION,
            ),
            now=issued_at,
        )
        if not verdict.permitted:
            raise LocalIdentityError(f"authorization denied: {verdict.reason}")
        key, secret = self._load_key()
        authority = ApprovalAuthority(
            authorization_id=f"approval-{uuid4().hex}",
            actor_id=principal.subject_id,
            allowed_decision_kinds=allowed_decision_kinds,
            issued_at=issued_at,
            valid_until=issued_at + lifetime,
        )
        signature = hmac.new(
            secret,
            _canonical(authority.model_dump(mode="json", exclude={"proof"})),
            hashlib.sha256,
        ).hexdigest()
        return authority.model_copy(
            update={
                "proof": ApprovalCredentialProof(
                    proof_type="local_hmac_sha256",
                    issuer=key.key_id,
                    key_id=key.key_id,
                    subject_session_id=principal.session_id,
                    authorization_policy_id=authorizer.policy_id,
                    authorized_action=AuthorizationAction.APPLY_DECISION.value,
                    signature=signature,
                )
            }
        )

    def verify_approval_authority(
        self,
        authority: ApprovalAuthority,
        *,
        now: datetime | None = None,
    ) -> ApprovalAuthority:
        proof = authority.proof
        if proof is None:
            raise LocalIdentityError("approval authority has no verifiable proof")
        key, secret = self._load_key()
        if (
            proof.proof_type != "local_hmac_sha256"
            or proof.issuer != key.key_id
            or proof.key_id != key.key_id
            or proof.authorization_policy_id != RoleBasedAuthorizer.policy_id
            or proof.authorized_action != AuthorizationAction.APPLY_DECISION.value
        ):
            raise LocalIdentityError("approval authority proof metadata is invalid")
        expected = hmac.new(
            secret,
            _canonical(authority.model_dump(mode="json", exclude={"proof"})),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, proof.signature):
            raise LocalIdentityError("approval authority signature is invalid")
        when = now or datetime.now(UTC)
        if when.tzinfo is None:
            raise LocalIdentityError("approval authority check time needs a timezone")
        if when < authority.issued_at or when >= authority.valid_until:
            raise LocalIdentityError("approval authority is not currently valid")
        return authority

    def issue_execution_authorization(
        self,
        session: SignedIdentitySession,
        *,
        task_id: str,
        run_id: str,
        maximum_risk: RiskLevel,
        lifetime: timedelta = timedelta(minutes=30),
        now: datetime | None = None,
    ) -> TaskExecutionAuthorization:
        if lifetime <= timedelta(0) or lifetime > timedelta(hours=1):
            raise LocalIdentityError(
                "execution authority lifetime must be positive and no more than one hour"
            )
        issued_at = now or datetime.now(UTC)
        principal = self.verify(session, now=issued_at)
        authorizer = RoleBasedAuthorizer()
        verdict = authorizer.authorize(
            AuthorizationRequest(
                principal=principal,
                action=AuthorizationAction.EXECUTE_TASK,
                task_id=task_id,
                run_id=run_id,
                risk=maximum_risk,
            ),
            now=issued_at,
        )
        if not verdict.permitted:
            raise LocalIdentityError(f"authorization denied: {verdict.reason}")
        key, secret = self._load_key()
        authority = TaskExecutionAuthorization(
            authorization_id=f"execution-{uuid4().hex}",
            task_id=task_id,
            run_id=run_id,
            actor_type=ActorType.HUMAN,
            actor_id=principal.subject_id,
            maximum_risk=maximum_risk,
            issued_at=issued_at,
            valid_until=issued_at + lifetime,
        )
        signature = hmac.new(
            secret,
            _canonical(authority.model_dump(mode="json", exclude={"proof"})),
            hashlib.sha256,
        ).hexdigest()
        return authority.model_copy(
            update={
                "proof": ExecutionCredentialProof(
                    proof_type="local_hmac_sha256",
                    issuer=key.key_id,
                    key_id=key.key_id,
                    subject_session_id=principal.session_id,
                    authorization_policy_id=authorizer.policy_id,
                    authorized_action=AuthorizationAction.EXECUTE_TASK.value,
                    signature=signature,
                )
            }
        )

    def verify_execution_authorization(
        self,
        authority: TaskExecutionAuthorization,
        *,
        task_id: str,
        run_id: str,
        now: datetime | None = None,
    ) -> TaskExecutionAuthorization:
        proof = authority.proof
        if proof is None:
            raise LocalIdentityError("execution authority has no verifiable proof")
        key, secret = self._load_key()
        if (
            proof.proof_type != "local_hmac_sha256"
            or proof.issuer != key.key_id
            or proof.key_id != key.key_id
            or proof.authorization_policy_id != RoleBasedAuthorizer.policy_id
            or proof.authorized_action != AuthorizationAction.EXECUTE_TASK.value
        ):
            raise LocalIdentityError("execution authority proof metadata is invalid")
        if authority.task_id != task_id or authority.run_id != run_id:
            raise LocalIdentityError("execution authority belongs to another task or run")
        expected = hmac.new(
            secret,
            _canonical(authority.model_dump(mode="json", exclude={"proof"})),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, proof.signature):
            raise LocalIdentityError("execution authority signature is invalid")
        when = now or datetime.now(UTC)
        if when.tzinfo is None:
            raise LocalIdentityError("execution authority check time needs a timezone")
        if when < authority.issued_at or when >= authority.valid_until:
            raise LocalIdentityError("execution authority is not currently valid")
        return authority

    def load_approval_authority(
        self,
        path: str | Path,
        *,
        now: datetime | None = None,
    ) -> ApprovalAuthority:
        try:
            authority = ApprovalAuthority.model_validate_json(
                Path(path).read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise LocalIdentityError(
                "approval authority file is unavailable or invalid"
            ) from exc
        return self.verify_approval_authority(authority, now=now)

    @staticmethod
    def create_session_file(path: str | Path, session: SignedIdentitySession) -> None:
        destination = Path(path)
        encoded = (_canonical(session.model_dump(mode="json")) + b"\n")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                destination,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise LocalIdentityError(
                f"refusing to overwrite an existing identity session: {destination}"
            ) from exc
        except OSError as exc:
            raise LocalIdentityError(
                f"could not create identity session: {destination}"
            ) from exc

    @staticmethod
    def create_approval_authority_file(
        path: str | Path,
        authority: ApprovalAuthority,
    ) -> None:
        destination = Path(path)
        encoded = (_canonical(authority.model_dump(mode="json")) + b"\n")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                destination,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise LocalIdentityError(
                f"refusing to overwrite an approval authority: {destination}"
            ) from exc
        except OSError as exc:
            raise LocalIdentityError(
                f"could not create approval authority: {destination}"
            ) from exc

    def _load_key(self) -> tuple[_LocalIdentityKey, bytes]:
        if self.key_path.is_symlink():
            raise LocalIdentityError("identity key path must not be a symlink")
        try:
            resolved = self.key_path.resolve(strict=True)
            if not resolved.is_file():
                raise LocalIdentityError("identity key path is not a regular file")
            if os.name != "nt":
                mode = stat.S_IMODE(resolved.stat().st_mode)
                if mode & 0o077:
                    raise LocalIdentityError(
                        "identity key permissions must not allow group/other access"
                    )
            key = _LocalIdentityKey.model_validate_json(
                resolved.read_text(encoding="utf-8")
            )
            secret = base64.urlsafe_b64decode(key.secret_base64.encode("ascii"))
        except LocalIdentityError:
            raise
        except (OSError, ValueError, ValidationError) as exc:
            raise LocalIdentityError("identity key is unavailable or invalid") from exc
        if len(secret) != 32:
            raise LocalIdentityError("identity key secret has an invalid length")
        return key, secret


class RoleBasedAuthorizer:
    """Deterministic local RBAC policy; deny is the default."""

    policy_id = "local-rbac-v1"
    _ROLE_ACTIONS = {
        "observer": frozenset({AuthorizationAction.VIEW_CONTROL_PLANE}),
        "executor": frozenset(
            {
                AuthorizationAction.VIEW_CONTROL_PLANE,
                AuthorizationAction.EXECUTE_TASK,
            }
        ),
        "reviewer": frozenset(
            {
                AuthorizationAction.VIEW_CONTROL_PLANE,
                AuthorizationAction.REVIEW_IMPLEMENTATION,
            }
        ),
        "approver": frozenset(
            {
                AuthorizationAction.VIEW_CONTROL_PLANE,
                AuthorizationAction.RECORD_DECISION,
                AuthorizationAction.APPLY_DECISION,
                AuthorizationAction.INTEGRATE_SOURCE,
                AuthorizationAction.CLEANUP_WORKSPACE,
            }
        ),
        "worker-admin": frozenset(
            {
                AuthorizationAction.VIEW_CONTROL_PLANE,
                AuthorizationAction.MANAGE_WORKER,
            }
        ),
    }
    _ALL_ACTIONS = frozenset(AuthorizationAction)

    def authorize(
        self,
        request: AuthorizationRequest,
        *,
        now: datetime | None = None,
    ) -> AuthorizationVerdict:
        if not request.principal.is_active(now):
            permitted = False
            reason = "authenticated session is expired or not active"
        else:
            allowed: set[AuthorizationAction] = set()
            for role in request.principal.roles:
                if role == "operator-admin":
                    allowed.update(self._ALL_ACTIONS)
                else:
                    allowed.update(self._ROLE_ACTIONS.get(role, ()))
            permitted = request.action in allowed
            reason = (
                f"role policy permits {request.action.value}"
                if permitted
                else f"roles do not permit {request.action.value}"
            )
        return AuthorizationVerdict(
            permitted=permitted,
            policy_id=self.policy_id,
            subject_id=request.principal.subject_id,
            action=request.action,
            reason=reason,
            evidence_ids=(request.principal.session_id,),
        )
