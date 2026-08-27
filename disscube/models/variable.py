import json
import hashlib
from typing import Literal
from pydantic import BaseModel
from .grid import SpatialRelation


class Variable(BaseModel):
    name: str
    operator: str
    class_code: int | None = None

    # Explicit source-value -> target-value lookup table, used by value-remapping
    # operators (e.g. "reclassify"). Kept as plain data (not a callable) so it
    # travels through the catalog and is folded into spec_hash() — two
    # derivations using different tables are always distinct products.
    mapping: dict[int, int] | None = None


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

    def spec_hash(self) -> str:
        """
        Deterministic SHA-256 hash of the derivation spec.

        Includes ``valid_from`` and ``valid_until`` so that derives for
        different time periods are always treated as distinct products.
        A static derivation (both None) hashes differently from any
        temporal derivation.
        """
        variables_data = [
            v.model_dump() for v in sorted(self.variables, key=lambda x: x.name)
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

        encoded = json.dumps(relevant_data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def is_temporal(self) -> bool:
        """Return True if this derivation covers a specific time window."""
        return self.valid_from is not None or self.valid_until is not None