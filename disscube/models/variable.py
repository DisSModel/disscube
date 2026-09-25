import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel

from .grid import SpatialRelation


class Variable(BaseModel):
    name: str
    operator: str
    class_code: int | None = None
    params: dict[str, Any] = {}
    """Operator options (e.g. ``{"crs": "EPSG:5880"}`` for ``distance``)."""
    fill: Literal["nearest"] | None = None
    """Fill the cells left without a value (NaN) from the nearest cell that has one."""

    def hash_data(self) -> dict:
        """The fields that enter ``spec_hash``: ``params`` and ``fill`` only when set,
        so variables that do not use them keep the hashes they had before."""
        data = self.model_dump(exclude={"params", "fill"})
        if self.params:
            data["params"] = self.params
        if self.fill is not None:
            data["fill"] = self.fill
        return data


class SpatialSource(BaseModel):
    id: str
    name: str
    format: Literal["raster", "vector"]
    asset_url: str
    checksum: str | None = None
    crs: str
    bbox: list[float] | None = None
    time: int | None = None
    tags: list[str] = []
    band_map: dict[str, int] = {}  # variable_name -> band_index (1-based), optional
    read_options: dict[str, Any] = {}
    """Vector sources: keyword arguments for ``geopandas.read_file`` (``where``,
    ``encoding``, ``layer``, …). Callers fold them into ``checksum`` so that a
    different selection of the same file is a different product."""
    nodata: float | None = None
    """Raster sources: the value that means "no data", when the file does not declare it."""

    def fingerprint(self) -> str | None:
        """``checksum``, with ``read_options`` and ``nodata`` folded in when set: the
        content a derivation reads. ``None`` when there is nothing to fingerprint."""
        if not self.read_options and self.nodata is None:
            return self.checksum
        payload = {"checksum": self.checksum, "read_options": self.read_options, "nodata": self.nodata}
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class DerivedVariable(BaseModel):
    id: str
    name: str
    grid_id: str
    role: str
    times: list[int]
    dtype: str
    units: str | None = None
    derivation_id: str
    spec_hash: str
    tile_id: str | None = None
    content_hash: str | None = None
    asset_url: str


class SpatialDerivation(BaseModel):
    source_id: str
    grid_id: str
    role: str
    variables: list[Variable]
    relations: list[SpatialRelation] = []

    # Temporal validity window — both None means the variable is static.
    # ISO 8601 date strings ("2000-01-01") or year strings ("2000") are accepted.
    # Two derivations with different valid_from/valid_until produce different
    # spec_hashes, ensuring reproducibility of temporal derives.
    valid_from:  str | None = None
    valid_until: str | None = None

    # Checksum of the source's content, ``SpatialSource.fingerprint()`` (its
    # checksum, plus its read options and nodata when set), copied
    # by ``CubeClient.derive()``. When set, it enters the hash, so replacing
    # the source file (with a new checksum) yields a new product instead of a
    # stale cache hit. When None, the hash is the same as before this field
    # existed, so catalogs built earlier stay valid.
    source_checksum: str | None = None

    def spec_hash(self) -> str:
        """
        Deterministic SHA-256 hash of the derivation spec.

        Includes ``valid_from`` and ``valid_until`` so that derives for
        different time periods are always treated as distinct products.
        A static derivation (both None) hashes differently from any
        temporal derivation. ``source_checksum`` is included only when set.
        """
        variables_data = [
            v.hash_data() for v in sorted(self.variables, key=lambda x: x.name)
        ]

        # relations are intentionally excluded from the hash:
        # no pipeline stage reads SpatialRelation during computation, so
        # including them would make the cache key sensitive to metadata that
        # does not affect the output — violating the reproducibility guarantee.
        relevant_data = {
            "source_id":   self.source_id,
            "grid_id":     self.grid_id,
            "role":        self.role,
            "variables":   variables_data,
            "valid_from":  self.valid_from,   # None for static variables
            "valid_until": self.valid_until,  # None for static variables
        }
        if self.source_checksum is not None:
            relevant_data["source_checksum"] = self.source_checksum

        encoded = json.dumps(relevant_data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def is_temporal(self) -> bool:
        """Return True if this derivation covers a specific time window."""
        return self.valid_from is not None or self.valid_until is not None