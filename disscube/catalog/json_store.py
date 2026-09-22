import json
from pathlib import Path

from disscube.models import DerivedVariable, GridSpec, SpatialRelation, SpatialSource


class JsonCatalogStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data = {"grids": {}, "sources": {}, "derived": {}, "relations": []}
        if self.path.exists():
            with open(self.path, "r") as f:
                self._data = json.load(f)
            if "relations" not in self._data:
                self._data["relations"] = []

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def save_grid(self, grid: GridSpec) -> None:
        self._data["grids"][grid.id] = grid.model_dump()
        self._save()

    def get_grid(self, grid_id: str) -> GridSpec | None:
        data = self._data["grids"].get(grid_id)
        return GridSpec(**data) if data else None

    def list_grids(self) -> list[GridSpec]:
        return [GridSpec(**g) for g in self._data["grids"].values()]

    def save_spatial_source(self, source: SpatialSource) -> None:
        self._data["sources"][source.id] = source.model_dump()
        self._save()

    def get_spatial_source(self, source_id: str) -> SpatialSource | None:
        data = self._data["sources"].get(source_id)
        return SpatialSource(**data) if data else None

    def list_spatial_sources(self) -> list[SpatialSource]:
        return [SpatialSource(**s) for s in self._data["sources"].values()]

    def save_derived(self, derived: DerivedVariable) -> None:
        self._data["derived"][derived.id] = derived.model_dump()
        self._save()

    def delete_derived(self, derived_id: str) -> None:
        self._data["derived"].pop(derived_id, None)
        self._save()

    def search_derived_variables(self, grid_id: str | None = None, role: str | None = None, tile_id: str | None = None) -> list[DerivedVariable]:
        results = []
        for d in self._data["derived"].values():
            if grid_id and d["grid_id"] != grid_id:
                continue
            if role and d["role"] != role:
                continue
            if tile_id and d.get("tile_id") != tile_id:
                continue
            results.append(DerivedVariable(**d))
        return results

    def get_derived_by_hash(self, spec_hash: str) -> DerivedVariable | None:
        for d in self._data["derived"].values():
            if d["spec_hash"] == spec_hash:
                return DerivedVariable(**d)
        return None

    def save_relation(self, relation: SpatialRelation) -> None:
        # Check if already exists to avoid duplicates
        existing = self.get_relation(relation.source_grid_id, relation.target_grid_id)
        if existing:
            # Update
            for i, r in enumerate(self._data["relations"]):
                if r["source_grid_id"] == relation.source_grid_id and r["target_grid_id"] == relation.target_grid_id:
                    self._data["relations"][i] = relation.model_dump()
                    break
        else:
            self._data["relations"].append(relation.model_dump())
        self._save()

    def get_relations(self, grid_id: str) -> list[SpatialRelation]:
        """Busca bidirecional: source_grid_id OU target_grid_id."""
        return [
            SpatialRelation(**r) for r in self._data["relations"]
            if r["source_grid_id"] == grid_id or r["target_grid_id"] == grid_id
        ]

    def get_relation(self, source_id: str, target_id: str) -> SpatialRelation | None:
        for r in self._data["relations"]:
            if r["source_grid_id"] == source_id and r["target_grid_id"] == target_id:
                return SpatialRelation(**r)
        return None
