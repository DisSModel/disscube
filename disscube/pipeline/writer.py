import zarr
import xarray as xr
from pathlib import Path
from disscube.pipeline import PipelineStage, PipelineContext
from disscube.models import DerivedVariable

class VariableWriter(PipelineStage):
    def __init__(self, storage, catalog):
        self.storage = storage
        self.catalog = catalog

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        ds = ctx.data
        grid = ctx.grid
        derivation = ctx.derivation
        spec_hash = derivation.spec_hash()
        
        # Extract tile_id: prioritize context, then fallback to source_id pattern
        tile_id = ctx.tile_id
        if not tile_id and grid.id.startswith("BDC_"):
            # Expecting source.id like "BDC_LG_009002"
            parts = ctx.source.id.split("_")
            if len(parts) >= 3:
                tile_id = parts[-1]

        # Save each variable to Zarr
        for var_name in ds.data_vars:
            da = ds[var_name]
            
            # Ensure spatial_ref coordinate is included if it exists in the dataset
            if "spatial_ref" in ds.coords and "spatial_ref" not in da.coords:
                da = da.assign_coords({"spatial_ref": ds.coords["spatial_ref"]})
            
            # Add metadata
            da.attrs["grid_id"] = grid.id
            da.attrs["role"] = derivation.role
            da.attrs["spec_hash"] = spec_hash
            if tile_id:
                da.attrs["tile_id"] = tile_id
            if "spatial_ref" in da.coords:
                da.attrs["crs"] = grid.crs
            
            # Storage path: derived/{grid_id}/{tile_id or 'global'}/{spec_hash}/{var_name}.zarr
            #
            # `relative_path` is what the catalog records and `full_path` is
            # what actually gets written: the store root is a deployment
            # detail (it may be "./data/" in one run and "/srv/cube" in the
            # next), so baking it into the catalog would tie every entry to
            # the machine and working directory that produced it.
            partition = tile_id if tile_id else "global"
            relative_path = f"derived/{grid.id}/{partition}/{spec_hash}/{var_name}.zarr"
            full_path = self.storage.get_full_path(relative_path)
            
            # Save as dataset to preserve all coordinates (including spatial_ref)
            da.to_dataset(name=var_name).to_zarr(full_path, mode="w", consolidated=False)
            
            # Calculate content hash (SHA-256) of the Zarr directory
            content_hash = self._calculate_dir_hash(full_path)
            
            # Register in catalog
            derived_id = f"{spec_hash}_{var_name}"
            if tile_id:
                derived_id = f"{spec_hash}_{tile_id}_{var_name}"

            # Determine temporal markers. 
            # If the source has an explicit time, use it.
            # Otherwise, use the valid_from from the derivation (converted to int if possible).
            times = []
            if ctx.source.time is not None:
                times = [ctx.source.time]
            elif derivation.valid_from is not None:
                try:
                    # Extract year component: accepts "2020" or "2020-01-01"
                    times = [int(derivation.valid_from.split("-")[0])]
                except (ValueError, AttributeError):
                    pass

            derived = DerivedVariable(
                id=derived_id,
                name=var_name,
                grid_id=grid.id,
                role=derivation.role,
                times=times,
                dtype=str(da.dtype),
                derivation_id=spec_hash,
                spec_hash=spec_hash,
                tile_id=tile_id,
                content_hash=content_hash,
                asset_url=relative_path,
            )
            self.catalog.save_derived(derived)
            
        return ctx

    def _calculate_dir_hash(self, path: str) -> str:
        """
        Calculates SHA-256 of a directory by hashing all file contents.
        """
        import hashlib
        from pathlib import Path
        
        hasher = hashlib.sha256()
        root = Path(path)
        
        # Sort files to ensure deterministic hash
        for file in sorted(root.rglob("*")):
            if file.is_file():
                # Hash relative path to capture structure changes
                hasher.update(str(file.relative_to(root)).encode())
                with open(file, "rb") as f:
                    # Read in chunks for memory efficiency
                    for chunk in iter(lambda: f.read(4096), b""):
                        hasher.update(chunk)
        
        return hasher.hexdigest()
