import xarray as xr
import rioxarray
import sys
import os

def convert_zarr_to_tif(zarr_path, output_tif):
    if not os.path.exists(zarr_path):
        print(f"Error: Path {zarr_path} does not exist.")
        return

    print(f"Opening Zarr: {zarr_path}")
    try:
        # Abre o dataset Zarr
        ds = xr.open_zarr(zarr_path)
        
        # The Zarr written by DisSCube is a Dataset; take its first data variable.
        var_names = list(ds.data_vars)
        if not var_names:
            print("Error: No data variables found in Zarr.")
            return
            
        da = ds[var_names[0]]
        
        # Make sure the spatial dims are set correctly for rioxarray
        if 'x' not in da.coords or 'y' not in da.coords:
            print("Error: Spatial coordinates (x, y) not found in DataArray.")
            return

        print(f"Converting variable '{var_names[0]}' to GeoTIFF...")
        
        # Escreve o TIFF
        da.rio.to_raster(output_tif)
        print(f"Success! Saved to {output_tif}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python tools/zarr_to_tif.py <input_zarr_path> <output_tif_path>")
        sys.exit(1)

    zarr_path = sys.argv[1]
    output_tif = sys.argv[2]
    convert_zarr_to_tif(zarr_path, output_tif)
