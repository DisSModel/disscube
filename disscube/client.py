from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

import numpy as np
import xarray as xr

from disscube.catalog.sqlite_store import SqliteCatalogStore
from disscube.models import DerivedVariable, GridSpec, SpatialDerivation, SpatialRelation, SpatialSource
from disscube.pipeline import PipelineContext
from disscube.pipeline.aggregator import Aggregator
from disscube.pipeline.aligner import GridAligner
from disscube.pipeline.normalizer import Normalizer
from disscube.pipeline.writer import VariableWriter
from disscube.storage import AssetStore

if TYPE_CHECKING:
    # Type-only imports: Derivation is imported lazily at runtime to avoid a
    # circular import; dissmodel is optional and only needed by to_raster_backend().
    from dissmodel.geo.raster.backend import RasterBackend

    from disscube.models import Derivation

log = logging.getLogger("disscube.client.cube_client")


def _filter_period(da: xr.DataArray, period: tuple[str, str]) -> xr.DataArray:
    """Keep the time slices of ``da`` whose value lies within ``period`` (inclusive)."""
    start, end = period
    time_vals = da.coords["time"].values
    start_val: int | str
    end_val: int | str
    if len(time_vals) > 0 and isinstance(time_vals[0], (int, np.integer)):
        try:
            start_val, end_val = int(start), int(end)
        except ValueError:
            start_val, end_val = start, end
    else:
        start_val, end_val = start, end
    return da.isel(time=(time_vals >= start_val) & (time_vals <= end_val))


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

    def get_relations(self, grid_id: str) -> list[SpatialRelation]:
        return self.catalog.get_relations(grid_id)

    def search(self, grid: str | None = None, role: str | None = None) -> list[DerivedVariable]:
        return self.catalog.search_derived_variables(grid_id=grid, role=role)

    def derive(self, derivation: SpatialDerivation, tile_id: str | None = None) -> list[DerivedVariable]:
        derivation = derivation.model_copy(deep=True)
        if not derivation.relations:
            derivation.relations = self.get_relations(derivation.grid_id)

        # A source registered with a checksum ties the product to that content:
        # new file + new checksum -> new spec_hash -> recomputed, not a stale hit.
        source = self.catalog.get_spatial_source(derivation.source_id)
        if source is not None and derivation.source_checksum is None:
            derivation.source_checksum = source.fingerprint()

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

        if not source:
            raise ValueError(f"SpatialSource not found: {derivation.source_id}")

        grid = self.catalog.get_grid(derivation.grid_id)
        if not grid:
            raise ValueError(f"Grid not found: {derivation.grid_id}")

        if tile_id:
            tile_source = self.catalog.get_spatial_source(f"{grid.id}_{tile_id}")
            if not tile_source or not tile_source.bbox:
                raise ValueError(f"Tile {tile_id} with valid bbox not found for grid {grid.id}")

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

    def derive_declarative(
        self,
        derivation: Derivation,
        grid_id: str,
        tile_id: str | None = None,
    ) -> list[DerivedVariable]:
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
        tile_id: str | None = None,
        grid_id: str | None = None,
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
            grid_ids = list({m.grid_id for m in matches})
            if len(grid_ids) > 1:
                raise ValueError(
                    f"Multiple grids found for {variable_id}: {grid_ids}. "
                    "Please specify grid_id."
                )

        if tile_id is None and len(matches) > 1:
            candidate_tiles = sorted({m.tile_id for m in matches if m.tile_id})
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
        static = [d for d in matches if not d.times and _exists(d)]

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

    # ------------------------------------------------------------------
    # Cube output: xarray Dataset (primary), exports, DisSModel adapter
    # ------------------------------------------------------------------

    def _load_variables(
        self,
        variables: list[str],
        grid_id: str | None = None,
        period: tuple[str, str] | None = None,
    ) -> dict[str, xr.DataArray]:
        """Load ``variables`` as DataArrays, applying the ``period`` filter.

        Static variables are ``(y, x)``; temporal ones are ``(time, y, x)`` with an
        integer-year ``time`` coordinate. A temporal variable with no slice inside
        ``period`` is skipped with a warning; static variables ignore ``period``.
        """
        loaded: dict[str, xr.DataArray] = {}
        for var_name in variables:
            da = self.load(var_name, grid_id=grid_id)

            if da.ndim == 3 and "time" in da.dims:
                if period is not None:
                    da = _filter_period(da, period)
                if da.sizes.get("time", 0) == 0:
                    log.warning("%s: no data in period %s, skipped", var_name, period)
                    continue
                da = da.transpose("time", "y", "x")
            else:
                da = da.transpose("y", "x")
            loaded[var_name] = da

        if not loaded:
            raise ValueError(f"No variables could be loaded: {variables}")
        return loaded

    @staticmethod
    def _detect_crs(arrays):
        """The CRS of the first array that declares one (``crs`` attribute or ``spatial_ref``)."""
        for var_name, da in arrays.items():
            crs = da.attrs.get("crs")
            if not crs and "spatial_ref" in da.coords:
                try:
                    crs = da.rio.crs
                except Exception:  # noqa: BLE001 — defensive fallback: the .rio accessor raises undocumented types (e.g. ValueError, CRSError)
                    log.debug("Could not read CRS from %s spatial_ref", var_name)
            if crs:
                return crs
        return None

    def to_dataset(
        self,
        variables: list[str],
        grid_id: str | None = None,
        period: tuple[str, str] | None = None,
    ) -> xr.Dataset:
        """
        The cube as an ``xarray.Dataset`` — the primary, dependency-free output.

        Static variables have dimensions ``(y, x)``; temporal variables
        ``(time, y, x)`` with an integer-year ``time`` coordinate shared by the
        whole Dataset (a variable that lacks a year present in another is NaN
        there). The grid's CRS and affine transform are written with rioxarray
        (``ds.rio.crs``, ``ds.rio.transform()``). Variables stay lazy (Zarr-backed).

        Auxiliary quality layers stored as extra coordinates of a variable
        (e.g. purity of a ``majority``) are not carried into the Dataset;
        ``load()`` still returns them.

        Parameters
        ----------
        variables : list[str]
            Names of derived variables to load.
        grid_id : str | None
            Restrict search to a specific grid. Required when the same
            variable name exists on multiple grids.
        period : tuple[str, str] | None
            Optional ``(start, end)`` filter for temporal variables; only time
            slices whose value falls within [start, end] are kept (e.g.
            ``period=("2000", "2014")``). Static variables are unaffected.
            A temporal variable with no slice in the range is skipped with a
            warning; if nothing is left, ``ValueError`` is raised.
        """
        arrays = self._load_variables(variables, grid_id=grid_id, period=period)

        clean = {}
        for name, da in arrays.items():
            extra = [c for c in da.coords if c not in da.dims and c != "spatial_ref"]
            clean[name] = da.drop_vars(extra) if extra else da
        ds = xr.Dataset(clean)

        crs = self._detect_crs(arrays)
        if crs:
            ds = ds.rio.write_crs(crs)
        transform = self._grid_transform(grid_id, variables)
        if transform is not None:
            ds = ds.rio.write_transform(transform)
        return ds

    def export_geotiff(
        self,
        variables: list[str],
        output: str | os.PathLike[str],
        grid_id: str | None = None,
        period: tuple[str, str] | None = None,
    ) -> None:
        """
        Write variables to a multi-band GeoTIFF.

        Static variables give one band named after the variable; temporal ones
        one band per year, named ``<variable>_<year>`` and ordered by year. If a
        ``mask`` variable is among ``variables`` (fraction of the cell inside the
        territory), cells where it is 0 are set to NaN in every other band.
        """
        from disscube.export import write_geotiff

        arrays = self._load_variables(variables, grid_id=grid_id, period=period)
        transform = self._grid_transform(grid_id, variables)
        write_geotiff(arrays, output, crs=self._detect_crs(arrays), transform=transform)

    def export_netcdf(
        self,
        variables: list[str],
        output: str | os.PathLike[str],
        grid_id: str | None = None,
        period: tuple[str, str] | None = None,
    ) -> None:
        """
        Write the cube (see :meth:`to_dataset`) to a CF-1.8 netCDF file.

        Requires the ``netcdf`` extra (``pip install disscube[netcdf]``). The
        integer-year ``time`` axis is written as ``YYYY-01-01`` dates; variables
        keep their attributes (``spec_hash``, ``operator``, ``source_id``...) and
        ``mask`` is kept as a variable, not applied to the data.
        """
        from disscube.export import write_netcdf

        write_netcdf(self.to_dataset(variables, grid_id=grid_id, period=period), output)

    def to_raster_backend(
        self,
        variables: list[str],
        grid_id: str | None = None,
        period: tuple[str, str] | None = None,
    ) -> RasterBackend:
        """
        Adapter to the DisSModel ecosystem (requires ``pip install disscube[dissmodel]``).

        Returns a ``RasterBackend`` with the requested variables, the grid's CRS and
        affine transform (``backend.crs``, ``backend.transform``). Static variables
        are ``(y, x)`` arrays; temporal ones ``(time, y, x)`` with the axis in
        ``backend.time_coords``, and each keeps its own time axis (no NaN alignment).
        CA models get a 2D slice via ``backend.get(name, time=step)``.

        ``grid_id`` and ``period`` behave as in :meth:`to_dataset`.
        """
        try:
            from dissmodel.geo.raster.backend import RasterBackend
        except ImportError as exc:
            raise ImportError(
                "to_raster_backend() needs DisSModel: pip install 'disscube[dissmodel]'"
            ) from exc

        arrays = self._load_variables(variables, grid_id=grid_id, period=period)
        first = next(iter(arrays.values()))
        backend = RasterBackend(shape=(first.sizes["y"], first.sizes["x"]))
        for var_name, da in arrays.items():
            if "time" in da.dims:
                backend.set(var_name, da.values, time=da.coords["time"].values)
            else:
                backend.set(var_name, da.values)

        crs = self._detect_crs(arrays)
        if backend.crs is None and crs:
            backend.crs = crs
        if backend.transform is None:
            backend.transform = self._grid_transform(grid_id, variables)
        return backend

    def _grid_transform(self, grid_id: str | None, variables: list[str]):
        """The affine transform of the grid the variables were derived on, or None."""
        if grid_id is None:
            ids = {d.grid_id for d in self.catalog.search_derived_variables() if d.name in variables}
            if len(ids) != 1:
                return None
            grid_id = ids.pop()
        grid = self.catalog.get_grid(grid_id)
        return grid.transform if grid is not None else None

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


# Backward-compatibility alias for legacy code importing disscube.client.cube_client
sys.modules[f"{__name__}.cube_client"] = sys.modules[__name__]

__all__ = ["CubeClient"]
