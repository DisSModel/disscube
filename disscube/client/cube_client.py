from __future__ import annotations

import logging
from typing import Optional, List, Any
import os
import numpy as np
import xarray as xr
from disscube.catalog.sqlite_store import SqliteCatalogStore

log = logging.getLogger(__name__)
from disscube.storage import AssetStore
from disscube.models import GridSpec, SpatialSource, SpatialDerivation, DerivedVariable, SpatialRelation
from disscube.pipeline import PipelineContext
from disscube.pipeline.normalizer import Normalizer
from disscube.pipeline.aligner import GridAligner
from disscube.pipeline.aggregator import Aggregator
from disscube.pipeline.writer import VariableWriter


class CubeClient:
    def __init__(self, catalog: str, store: str):
        self.catalog = SqliteCatalogStore(catalog)
        self.store = AssetStore(store)

    def register_grid(self, grid: GridSpec):
        self.catalog.save_grid(grid)

    def register_spatial_source(self, source: SpatialSource):
        self.catalog.save_spatial_source(source)

    def register_relation(self, relation: SpatialRelation):
        self.catalog.save_relation(relation)

    def get_relations(self, grid_id: str) -> List[SpatialRelation]:
        return self.catalog.get_relations(grid_id)

    def search(self, grid: Optional[str] = None, role: Optional[str] = None) -> List[DerivedVariable]:
        return self.catalog.search_derived_variables(grid_id=grid, role=role)

    def derive(self, derivation: SpatialDerivation, tile_id: Optional[str] = None) -> List[DerivedVariable]:
        derivation = derivation.model_copy(deep=True)
        if not derivation.relations:
            derivation.relations = self.get_relations(derivation.grid_id)

        spec_hash = derivation.spec_hash()

        expected = {v.name for v in derivation.variables}
        all_derived = self.catalog.search_derived_variables(tile_id=tile_id)
        cached_vars = [
            d for d in all_derived
            if d.spec_hash == spec_hash and self.store.fs.exists(d.asset_url)
        ]
        cached_names = {d.name for d in cached_vars}

        if expected == cached_names:
            return cached_vars

        source = self.catalog.get_spatial_source(derivation.source_id)
        if not source:
            raise ValueError(f"SpatialSource not found: {derivation.source_id}")

        grid = self.catalog.get_grid(derivation.grid_id)
        if not grid:
            raise ValueError(f"Grid not found: {derivation.grid_id}")

        if tile_id:
            tile_source = self._resolve_tile_source(grid.id, tile_id)

            grid = GridSpec(
                id=grid.id,
                type=grid.type,
                crs=grid.crs,
                resolution=grid.resolution,
                bbox=tile_source.bbox,
                description=f"Temporary tile grid for {tile_id}"
            )

        ctx = PipelineContext(source=source, grid=grid, derivation=derivation, tile_id=tile_id)

        pipeline = [
            Normalizer(),
            GridAligner(),
            Aggregator(),
            VariableWriter(self.store, self.catalog)
        ]

        for stage in pipeline:
            ctx = stage.execute(ctx)

        return [
            d for d in self.catalog.search_derived_variables(tile_id=tile_id)
            if d.spec_hash == spec_hash
        ]

    # BDC tile levels, coarsest-last. Order is only used for reporting; an
    # ambiguous id is always an error, never resolved by precedence.
    _BDC_TILE_LEVELS = ("SM", "MD", "LG")

    def _resolve_tile_source(self, grid_id: str, tile_id: str) -> SpatialSource:
        """
        Find the ``SpatialSource`` whose ``bbox`` defines ``tile_id``.

        Two registration conventions are supported, tried in this order:

        1. ``{grid_id}_{tile_id}`` — a tile mesh defined for one specific
           grid (see ``docs/architecture/tiling.md``).
        2. ``BDC_{LEVEL}_{tile_id}`` — the national BDC tile grids, as
           registered by ``disscube.utils.bdc_importer``. These are
           grid-independent: the same tile envelope serves every grid that
           shares the BDC CRS, so they are not duplicated per grid.

        A fully-qualified id (``"BDC_SM_027005"``) is also accepted directly,
        which is the way to disambiguate a bare id that exists at more than
        one BDC level.

        Raises
        ------
        ValueError
            If nothing matches, if the match has no ``bbox``, or if a bare
            tile id exists at several BDC levels. The levels cover different
            areas (SM ~1.5°, MD ~3°, LG ~6°) and share many ids, so guessing
            a level would silently derive the wrong extent.
        """
        scoped = self.catalog.get_spatial_source(f"{grid_id}_{tile_id}")
        if scoped is not None:
            if not scoped.bbox:
                raise ValueError(
                    f"Tile source {scoped.id!r} has no bbox; a tile source must "
                    "carry the bbox of the partition to process."
                )
            return scoped

        qualified = self.catalog.get_spatial_source(tile_id)
        if qualified is not None and qualified.bbox:
            return qualified

        matches = []
        for level in self._BDC_TILE_LEVELS:
            candidate = self.catalog.get_spatial_source(f"BDC_{level}_{tile_id}")
            if candidate is not None and candidate.bbox:
                matches.append((level, candidate))

        if len(matches) == 1:
            return matches[0][1]

        if len(matches) > 1:
            found = ", ".join(f"BDC_{lvl}_{tile_id}" for lvl, _ in matches)
            raise ValueError(
                f"Tile id {tile_id!r} is ambiguous: it exists at several BDC "
                f"levels ({found}), which cover different areas. Pass the "
                f"fully-qualified id instead, e.g. tile_id='BDC_"
                f"{matches[0][0]}_{tile_id}'."
            )

        raise ValueError(
            f"Tile {tile_id!r} with valid bbox not found for grid {grid_id!r}. "
            f"Looked for {grid_id}_{tile_id}, {tile_id}, and BDC_"
            f"{{{','.join(self._BDC_TILE_LEVELS)}}}_{tile_id}."
        )

    def derive_declarative(
        self,
        derivation: "Derivation",  # noqa: F821 — imported lazily to avoid circular refs
        grid_id: str,
        tile_id: Optional[str] = None,
    ) -> List[DerivedVariable]:
        """
        Thin convenience wrapper: build a ``SpatialDerivation`` from a
        declarative ``Derivation`` and call the existing ``derive()`` pipeline.

        No new execution logic is introduced here.

        Parameters
        ----------
        derivation : Derivation
            Declarative description of the derivation intent.
        grid_id : str
            Target grid identifier.
        tile_id : str | None
            Optional tile sub-identifier, forwarded to ``derive()``.
        """
        return self.derive(derivation.to_spatial_derivation(grid_id), tile_id=tile_id)

    def purge_stale(self) -> int:
        """
        Remove catalog entries whose Zarr files no longer exist on disk.

        Returns the number of entries removed. Safe to call at any time;
        entries with valid files are untouched.
        """
        all_derived = self.catalog.search_derived_variables()
        removed = 0
        for d in all_derived:
            if not os.path.exists(d.asset_url):
                self.catalog.delete_derived(d.id)
                log.debug("Purged stale catalog entry: %s", d.asset_url)
                removed += 1
        if removed:
            log.info("purge_stale: removed %d stale catalog entries", removed)
        return removed

    def load(
        self,
        variable_id: str,
        tile_id: Optional[str] = None,
        grid_id: Optional[str] = None,
    ) -> xr.DataArray:
        """
        Load a derived variable as an xr.DataArray.

        For temporal variables (DerivedVariable.times is non-empty), returns
        a DataArray with dims (time, y, x). For static variables, returns (y, x).
        """
        matches = []
        for d in self.catalog.search_derived_variables(tile_id=tile_id, grid_id=grid_id):
            if d.id == variable_id or d.name == variable_id:
                matches.append(d)

        if not matches:
            msg = f"Derived variable not found: {variable_id}"
            if grid_id:
                msg += f" on grid {grid_id}"
            raise ValueError(msg)

        if len(matches) > 1 and tile_id is None and not grid_id:
            grid_ids = list(set(m.grid_id for m in matches))
            if len(grid_ids) > 1:
                raise ValueError(
                    f"Multiple grids found for {variable_id}: {grid_ids}. "
                    "Please specify grid_id."
                )

        if tile_id is None and len(matches) > 1:
            candidate_tiles = sorted(set(m.tile_id for m in matches if m.tile_id))
            if len(candidate_tiles) > 1:
                raise ValueError(
                    f"Variable '{variable_id}' exists across multiple tiles: {candidate_tiles}. "
                    "Pass tile_id=<tile> to load a specific tile."
                )

        # Separate temporal from static matches, skipping stale catalog entries
        # whose files no longer exist on disk (catalog can accumulate orphans when
        # a source_id or other spec field changes between runs).
        def _exists(d: DerivedVariable) -> bool:
            ok = os.path.exists(d.asset_url)
            if not ok:
                log.debug("Stale catalog entry skipped (file missing): %s", d.asset_url)
            return ok

        temporal = [d for d in matches if d.times and _exists(d)]
        static   = [d for d in matches if not d.times and _exists(d)]

        if temporal:
            # Stack temporal slices along time axis sorted by first time value
            temporal_sorted = sorted(temporal, key=lambda d: d.times[0])
            slices = []
            time_coords = []
            for d in temporal_sorted:
                da = xr.open_zarr(d.asset_url, consolidated=False)[d.name]
                slices.append(da)
                time_coords.extend(d.times)
            return xr.concat(slices, dim=xr.DataArray(time_coords, dims="time"))

        # Static — original behaviour
        if not static:
            msg = f"Derived variable not found on disk: {variable_id}"
            if grid_id:
                msg += f" on grid {grid_id}"
            raise ValueError(msg)
        derived = static[0]
        return xr.open_zarr(derived.asset_url, consolidated=False)[derived.name]

    def to_lucc_data(
        self,
        variables: List[str],
        grid_id: Optional[str] = None,
        period: Optional[tuple[str, str]] = None,
    ) -> "RasterBackend":
        """
        Standard integration point for the DisSModel ecosystem.
        Returns a RasterBackend containing all requested variables.

        Static variables are stored as (y, x) arrays — identical to the
        previous behaviour; existing executors require no changes.

        Temporal variables are stored as (time, y, x) arrays with an
        explicit time axis in ``backend.time_coords``. CA models retrieve
        a 2D slice via ``backend.get(name, time=step)``.

        Parameters
        ----------
        variables : list[str]
            Names of derived variables to load.
        grid_id : str | None
            Restrict search to a specific grid. Required when the same
            variable name exists on multiple grids.
        period : tuple[str, str] | None
            Optional ``(start, end)`` filter for temporal variables.
            Only time slices whose value falls within [start, end] are loaded.
            Static variables are unaffected.
            Example: ``period=("2000", "2014")``

        Notes
        -----
        CONTRACT decisions (open — to be resolved before 1.0):

        1. Canonical temporal type: ``valid_from`` / ``valid_until`` accept
           year strings ("2020") or ISO dates ("2020-01-01");
           ``DerivedVariable.times`` stores ``list[int]`` (years only).
           Open: validate year-only format at model construction time so
           callers cannot silently store wrong temporal metadata.

        2. Missing-time behavior: a temporal variable whose slices are all
           outside ``period`` is skipped with ``log.warning`` and absent from
           the returned backend. The caller cannot distinguish "variable was
           static (period ignored)" from "existed but outside the range".
           Open: raise ``ValueError``, return a NaN slice, or keep skip.

        3. Empty-period backend: when every requested variable is filtered
           out by ``period``, ``RasterBackend`` is initialized but holds no
           data arrays. The caller receives a valid-looking but empty backend.
           Open: raise before returning when no variable was stored.
        """
        from dissmodel.geo.raster.backend import RasterBackend

        detected_crs = None
        backend = None

        for var_name in variables:
            da = self.load(var_name, grid_id=grid_id)

            # CRS detection — run once
            if detected_crs is None:
                detected_crs = da.attrs.get("crs")
                if not detected_crs and "spatial_ref" in da.coords:
                    try:
                        detected_crs = da.rio.crs
                    except Exception:
                        pass

            if backend is None:
                rows, cols = da.sizes["y"], da.sizes["x"]
                backend = RasterBackend(shape=(rows, cols))

            if da.ndim == 3 and "time" in da.dims:
                # Temporal variable — optionally filter by period
                if period is not None:
                    start, end = period
                    time_vals = da.coords["time"].values
                    if len(time_vals) > 0 and isinstance(time_vals[0], (int, np.integer)):
                        try:
                            start_val, end_val = int(start), int(end)
                        except ValueError:
                            start_val, end_val = start, end
                    else:
                        start_val, end_val = start, end

                    mask = (time_vals >= start_val) & (time_vals <= end_val)
                    da = da.isel(time=mask)

                if da.sizes.get("time", 0) == 0:
                    log.warning("%s: no data in period %s, skipped", var_name, period)
                    continue

                time_coords = da.coords["time"].values
                arr = da.transpose("time", "y", "x").values
                backend.set(var_name, arr, time=time_coords)

            else:
                # Static variable — backward-compatible path
                arr = da.transpose("y", "x").values
                backend.set(var_name, arr)

        if backend is None:
            raise ValueError(f"No variables could be loaded: {variables}")

        if backend.crs is None and detected_crs:
            backend.crs = detected_crs

        return backend

    def to_data_source(self, derived_id: str) -> dict:
        """
        Helper for dissmodel-platform ExperimentRecord.
        """
        derived = None
        for d in self.catalog.search_derived_variables():
            if d.id == derived_id:
                derived = d
                break

        if not derived:
            raise ValueError(f"Derived variable not found: {derived_id}")

        return {
            "uri": derived.asset_url,
            "checksum": derived.content_hash,
            "type": "local" if derived.asset_url.startswith("/") else "s3"
        }
