"""Read-only, source-controlled registry for semantic UI design revisions."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from jobslayer.ui_design import (
    ActiveUIDesign,
    SemanticUIDesign,
    UIDesignActiveBinding,
    UIDesignCatalog,
    UIDesignDescriptorReference,
    UIDesignIntentState,
    UIDesignStatusCounts,
    canonical_ui_design_sha256,
    validate_ui_design_revision,
)


MAXIMUM_UI_DESIGN_BYTES = 1_048_576


class UIDesignCatalogError(RuntimeError):
    """Raised when source-controlled UI design truth cannot be trusted."""


class UIDesignNotFoundError(UIDesignCatalogError, LookupError):
    pass


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise UIDesignCatalogError(f"UI design source must be a regular file: {path}")
        content = path.read_bytes()
        if len(content) > MAXIMUM_UI_DESIGN_BYTES:
            raise UIDesignCatalogError(f"UI design source exceeds size limit: {path}")
        payload = json.loads(content.decode("utf-8"))
    except UIDesignCatalogError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UIDesignCatalogError(f"could not read UI design source: {path}") from exc
    if not isinstance(payload, dict):
        raise UIDesignCatalogError(f"UI design source must be a JSON object: {path}")
    return payload


class SourceControlledUIDesignRegistry:
    """Resolve one exact active scheme revision per registered page.

    The browser receives only the selected read model. Revision paths, hashes,
    activation decisions and stable-change policy remain backend concerns.
    """

    def __init__(self, catalog_path: str | Path):
        supplied = Path(catalog_path)
        if supplied.is_symlink():
            raise UIDesignCatalogError("UI design catalog cannot be a symlink")
        try:
            self.catalog_path = supplied.resolve(strict=True)
        except OSError as exc:
            raise UIDesignCatalogError("UI design catalog is unavailable") from exc
        self.root = self.catalog_path.parent
        try:
            self.catalog = UIDesignCatalog.model_validate(
                _read_json_object(self.catalog_path)
            )
        except ValidationError as exc:
            raise UIDesignCatalogError("UI design catalog is invalid") from exc
        self._descriptions = self._load_descriptions()
        self._active = self._resolve_active()

    def list_references(self) -> tuple[UIDesignDescriptorReference, ...]:
        return self.catalog.descriptors

    def list_active(self) -> tuple[ActiveUIDesign, ...]:
        return tuple(self._active[page_id] for page_id in sorted(self._active))

    def get_active(self, page_id: str) -> ActiveUIDesign:
        try:
            return self._active[page_id]
        except KeyError as exc:
            raise UIDesignNotFoundError(
                f"no active UI design is registered for page: {page_id}"
            ) from exc

    def _load_descriptions(
        self,
    ) -> dict[tuple[str, str, int], SemanticUIDesign]:
        descriptions: dict[tuple[str, str, int], SemanticUIDesign] = {}
        histories: dict[tuple[str, str], list[SemanticUIDesign]] = {}
        for reference in self.catalog.descriptors:
            path = self.root.joinpath(*Path(reference.path).parts)
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise UIDesignCatalogError(
                    f"UI design descriptor is unavailable: {reference.path}"
                ) from exc
            if not resolved.is_relative_to(self.root) or path.is_symlink():
                raise UIDesignCatalogError(
                    f"UI design descriptor escapes the catalog root: {reference.path}"
                )
            try:
                description = SemanticUIDesign.model_validate(
                    _read_json_object(resolved)
                )
            except ValidationError as exc:
                raise UIDesignCatalogError(
                    f"UI design descriptor is invalid: {reference.path}"
                ) from exc
            identity = (
                description.page_id,
                description.scheme_id,
                description.revision,
            )
            expected_identity = (
                reference.page_id,
                reference.scheme_id,
                reference.revision,
            )
            if identity != expected_identity:
                raise UIDesignCatalogError(
                    f"UI design descriptor identity mismatch: {reference.path}"
                )
            digest = canonical_ui_design_sha256(description)
            if digest != reference.descriptor_sha256:
                raise UIDesignCatalogError(
                    f"UI design descriptor hash mismatch: {reference.path}"
                )
            descriptions[identity] = description
            histories.setdefault(identity[:2], []).append(description)

        for identity, history in histories.items():
            revisions = tuple(item.revision for item in history)
            if revisions != tuple(range(1, len(history) + 1)):
                raise UIDesignCatalogError(
                    "UI design revision history is not contiguous: "
                    + "/".join(identity)
                )
            for previous, current in zip(history, history[1:]):
                try:
                    validate_ui_design_revision(previous, current)
                except ValueError as exc:
                    raise UIDesignCatalogError(
                        "UI design revision history is invalid: "
                        + "/".join(identity)
                    ) from exc
        return descriptions

    def _resolve_active(self) -> dict[str, ActiveUIDesign]:
        active: dict[str, ActiveUIDesign] = {}
        for binding in self.catalog.active_bindings:
            key = (binding.page_id, binding.scheme_id, binding.revision)
            description = self._descriptions.get(key)
            if (
                description is None
                or canonical_ui_design_sha256(description)
                != binding.descriptor_sha256
            ):
                raise UIDesignCatalogError(
                    f"active UI design binding does not resolve: {binding.page_id}"
                )
            counts = {
                state: sum(unit.state == state for unit in description.units())
                for state in UIDesignIntentState
            }
            active[binding.page_id] = ActiveUIDesign(
                binding=UIDesignActiveBinding.model_validate(binding),
                description=description,
                state_counts=UIDesignStatusCounts(
                    dirty=counts[UIDesignIntentState.DIRTY],
                    planned=counts[UIDesignIntentState.PLANNED],
                    stable=counts[UIDesignIntentState.STABLE],
                ),
            )
        return active


__all__ = [
    "SourceControlledUIDesignRegistry",
    "UIDesignCatalogError",
    "UIDesignNotFoundError",
]
