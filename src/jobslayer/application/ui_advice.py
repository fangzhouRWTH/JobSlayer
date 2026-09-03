"""Application service for immutable, provider-neutral UI advice evidence."""

from __future__ import annotations

import hashlib
import json

from jobslayer.artifacts import ArtifactRegistry
from jobslayer.ui_advice import (
    UIAdviceCollection,
    UIAdviceEvidence,
    UIAdviceRequest,
    UIAdvisor,
)


class UIAdviceService:
    def __init__(self, advisor: UIAdvisor, artifacts: ArtifactRegistry):
        self.advisor = advisor
        self.artifacts = artifacts

    def collect(self, *, task_id: str, request: UIAdviceRequest) -> UIAdviceCollection:
        if not task_id.strip():
            raise ValueError("UI advice collection needs a task id")
        response = self.advisor.advise(request)
        raw_sha256 = hashlib.sha256(response.raw_output).hexdigest()
        producer = (
            f"ui-advisor:{response.provider.provider_id}"
            f"@{response.provider.provider_version}"
        )
        shared_metadata = {
            "request_id": request.request_id,
            "page_id": request.page_id,
            "scheme_id": request.scheme_id,
            "revision": request.revision,
            "descriptor_sha256": request.descriptor_sha256,
            "provider_id": response.provider.provider_id,
            "provider_version": response.provider.provider_version,
            "provider_source_commit": response.provider.source_commit,
        }
        raw_artifact = self.artifacts.register_bytes(
            task_id=task_id,
            artifact_type="ui_advice.provider_raw",
            producer=producer,
            content=response.raw_output,
            metadata=shared_metadata,
        )
        evidence_id = "ui-advice-" + hashlib.sha256(
            (
                request.request_id
                + "\0"
                + request.descriptor_sha256
                + "\0"
                + raw_sha256
            ).encode("utf-8")
        ).hexdigest()[:24]
        evidence = UIAdviceEvidence(
            evidence_id=evidence_id,
            request=request,
            provider=response.provider,
            recommendations=response.recommendations,
            raw_output_sha256=raw_sha256,
            raw_output_artifact_id=raw_artifact.artifact_id,
        )
        normalized = (
            json.dumps(
                evidence.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        normalized_artifact = self.artifacts.register_bytes(
            task_id=task_id,
            artifact_type="ui_advice.normalized_evidence",
            producer="jobslayer.ui-advice",
            content=normalized,
            metadata={
                **shared_metadata,
                "evidence_id": evidence.evidence_id,
                "raw_artifact_id": raw_artifact.artifact_id,
            },
        )
        return UIAdviceCollection(
            evidence=evidence,
            raw_artifact=raw_artifact,
            normalized_artifact=normalized_artifact,
        )


__all__ = ["UIAdviceService"]
