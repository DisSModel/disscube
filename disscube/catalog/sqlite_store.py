import json
import sqlite3
from pathlib import Path

from disscube.models import DerivedVariable, GridSpec, SpatialRelation, SpatialSource


class SqliteCatalogStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS grids (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS derived (
                    id TEXT PRIMARY KEY,
                    grid_id TEXT,
                    spec_hash TEXT,
                    tile_id TEXT,
                    role TEXT,
                    data TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    source_grid_id TEXT,
                    target_grid_id TEXT,
                    data TEXT NOT NULL,
                    PRIMARY KEY (source_grid_id, target_grid_id)
                )
            """)
            # Indexes for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_derived_grid ON derived(grid_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_derived_hash ON derived(spec_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_derived_tile ON derived(tile_id)")

    def save_grid(self, grid: GridSpec) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO grids (id, data) VALUES (?, ?)",
                (grid.id, grid.model_dump_json())
            )

    def get_grid(self, grid_id: str) -> GridSpec | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT data FROM grids WHERE id = ?", (grid_id,)).fetchone()
            return GridSpec(**json.loads(row["data"])) if row else None

    def list_grids(self) -> list[GridSpec]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT data FROM grids").fetchall()
            return [GridSpec(**json.loads(r["data"])) for r in rows]

    def save_spatial_source(self, source: SpatialSource) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sources (id, data) VALUES (?, ?)",
                (source.id, source.model_dump_json())
            )

    def get_spatial_source(self, source_id: str) -> SpatialSource | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT data FROM sources WHERE id = ?", (source_id,)).fetchone()
            return SpatialSource(**json.loads(row["data"])) if row else None

    def list_spatial_sources(self) -> list[SpatialSource]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT data FROM sources").fetchall()
            return [SpatialSource(**json.loads(r["data"])) for r in rows]

    def save_derived(self, derived: DerivedVariable) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO derived (id, grid_id, spec_hash, tile_id, role, data) VALUES (?, ?, ?, ?, ?, ?)",
                (derived.id, derived.grid_id, derived.spec_hash, derived.tile_id, derived.role, derived.model_dump_json())
            )

    def delete_derived(self, derived_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM derived WHERE id = ?", (derived_id,))

    def search_derived_variables(self, grid_id: str | None = None, role: str | None = None, tile_id: str | None = None) -> list[DerivedVariable]:
        query = "SELECT data FROM derived WHERE 1=1"
        params = []
        if grid_id:
            query += " AND grid_id = ?"
            params.append(grid_id)
        if role:
            query += " AND role = ?"
            params.append(role)
        if tile_id:
            query += " AND tile_id = ?"
            params.append(tile_id)
        
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [DerivedVariable(**json.loads(r["data"])) for r in rows]

    def get_derived_by_hash(self, spec_hash: str) -> DerivedVariable | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT data FROM derived WHERE spec_hash = ?", (spec_hash,)).fetchone()
            return DerivedVariable(**json.loads(row["data"])) if row else None

    def save_relation(self, relation: SpatialRelation) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO relations (source_grid_id, target_grid_id, data) VALUES (?, ?, ?)",
                (relation.source_grid_id, relation.target_grid_id, relation.model_dump_json())
            )

    def get_relations(self, grid_id: str) -> list[SpatialRelation]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT data FROM relations WHERE source_grid_id = ? OR target_grid_id = ?",
                (grid_id, grid_id)
            )
            return [SpatialRelation(**json.loads(r["data"])) for r in rows]

    def get_relation(self, source_id: str, target_id: str) -> SpatialRelation | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT data FROM relations WHERE source_grid_id = ? AND target_grid_id = ?",
                (source_id, target_id)
            ).fetchone()
            return SpatialRelation(**json.loads(row["data"])) if row else None
