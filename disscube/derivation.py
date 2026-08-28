"""
Declarative Derivation model — thin front-end over SpatialDerivation / Variable.

Field names are chosen to be compatible with STAC conventions where natural:

- ``valid_from`` / ``valid_until`` align with STAC ``start_datetime`` /
  ``end_datetime`` (and ``datetime`` for the static/instant case).
  No STAC logic is implemented here; the alignment is naming-only.
- ``bbox`` (optional, reserved) follows the STAC bounding-box field order:
  [xmin, ymin, xmax, ymax] in EPSG:4326.  The field is not populated
  from data and is not used in execution.

No STAC code, catalog, API, or export is implemented in this module.
"""

import json
import hashlib
from pydantic import BaseModel, model_validator

from disscube.operators.base import OPERATOR_REGISTRY
from disscube.models.variable import Variable, SpatialDerivation


class Derivation(BaseModel):
    """
    Declarative description of a single derivation intent.

    Acts as a thin, additive front-end over the existing
    ``SpatialDerivation`` / ``Variable`` machinery.  Instantiation validates
    the operator name and operator-specific field requirements so that errors
    surface before any I/O (fail-fast).

    Parameters
    ----------
    target : str
        Name of the derived variable produced (maps to ``Variable.name``).
    source_id : str
        Identifier of the ``SpatialSource`` (passed through to
        ``SpatialDerivation``).
    operator : str
        Operator name; must be a key in ``OPERATOR_REGISTRY``.
    class_code : int | None
        Target class code used by class-aware operators (e.g. ``"percentage"``).
        Required when the operator demands it; reserved but optional otherwise.
    mapping : dict[int, int] | None
        Source-value -> target-value lookup table used by value-remapping
        operators (e.g. ``"reclassify"``).  Required when the operator demands
        it.  Folded into ``spec_hash()`` via ``Variable``, so changing the
        table always yields a distinct product.
    role : str
        Semantic role of the variable (e.g. ``"driver"``, ``"state"``).
        Defaults to ``"driver"``.
    valid_from : str | None
        Start of the temporal validity window (ISO 8601 or year string).
        Aligns with STAC ``start_datetime``.  ``None`` means no lower bound.
    valid_until : str | None
        End of the temporal validity window (ISO 8601 or year string).
        Aligns with STAC ``end_datetime``.  ``None`` means no upper bound.
        Both ``None`` → static variable (aligns with STAC ``datetime``).
    purity_threshold : float | None
        Reserved for future purity-masking logic.  Included in
        ``spec_hash()`` so that two derivations with different thresholds
        are always distinct products.  Currently unused in execution.
    bbox : list[float] | None
        Optional bounding box ``[xmin, ymin, xmax, ymax]`` in EPSG:4326.
        Aligns with the STAC ``bbox`` field.  Reserved — not used in
        execution and excluded from ``spec_hash()``.
    """

    target: str
    source_id: str
    operator: str
    class_code: int | None = None
    mapping: dict[int, int] | None = None
    role: str = "driver"
    valid_from: str | None = None
    valid_until: str | None = None
    purity_threshold: float | None = None
    bbox: list[float] | None = None

    @model_validator(mode="after")
    def _validate_operator(self) -> "Derivation":
        available = sorted(OPERATOR_REGISTRY)
        if self.operator not in OPERATOR_REGISTRY:
            raise ValueError(
                f"Unknown operator {self.operator!r}. "
                f"Available operators: {available}"
            )
        meta = OPERATOR_REGISTRY[self.operator]
        if meta.requires_class_code and self.class_code is None:
            raise ValueError(
                f"Operator {self.operator!r} requires class_code to be set."
            )
        if meta.requires_mapping and not self.mapping:
            raise ValueError(
                f"Operator {self.operator!r} requires mapping to be set."
            )
        return self

    # ── Conversion helpers ────────────────────────────────────────────────────

    def to_variable(self) -> Variable:
        """
        Return the ``Variable`` instance corresponding to this derivation.

        Returns
        -------
        Variable
            With ``name=target``, ``operator=operator``, and
            ``class_code=class_code``.
        """
        return Variable(
            name=self.target,
            operator=self.operator,
            class_code=self.class_code,
            mapping=self.mapping,
        )

    def to_spatial_derivation(self, grid_id: str) -> SpatialDerivation:
        """
        Return the ``SpatialDerivation`` corresponding to this intent.

        This is the canonical bridge to the existing execution pipeline;
        ``CubeClient.derive()`` accepts the result directly.

        Parameters
        ----------
        grid_id : str
            Target grid identifier (required by ``SpatialDerivation``).

        Returns
        -------
        SpatialDerivation
        """
        return SpatialDerivation(
            source_id=self.source_id,
            grid_id=grid_id,
            role=self.role,
            variables=[self.to_variable()],
            valid_from=self.valid_from,
            valid_until=self.valid_until,
        )

    # ── Reproducibility ───────────────────────────────────────────────────────

    def spec_hash(self, grid_id: str = "__global__") -> str:
        """
        Deterministic SHA-256 hash of the derivation spec.

        Delegates to ``SpatialDerivation.spec_hash()`` for the base fields,
        then folds in ``purity_threshold`` when it is set.  ``bbox`` is
        excluded because it is descriptive metadata and does not affect
        what is computed.

        Parameters
        ----------
        grid_id : str
            Grid identifier used for the underlying
            ``SpatialDerivation.spec_hash()``.  Defaults to ``"__global__"``
            for grid-agnostic comparisons.

        Returns
        -------
        str
            64-character hex SHA-256 digest.
        """
        base = self.to_spatial_derivation(grid_id).spec_hash()
        if self.purity_threshold is None:
            return base
        payload = json.dumps(
            {"base": base, "purity_threshold": self.purity_threshold},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
