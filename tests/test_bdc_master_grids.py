"""
Tests for BDC grid import and tile-based loading.

What is actually implemented (and tested here):
  - import_bdc_grids() registers BR/5km and BR/1km simulation grids plus
    BDC_SM/MD/LG tile sources in the catalog.
  - load(name, tile_id=...) correctly filters to a specific tile.

What is planned but not yet implemented (marked xfail):
  - load(name) without tile_id raising when multiple tiles of the same
    variable exist on the same grid (tile disambiguation).
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from disscube.client import CubeClient
from disscube.utils.bdc_importer import import_bdc_grids


class TestBDCMasterGrids(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.catalog_path = os.path.join(self._tmpdir, "catalog.db")
        self.store_path = os.path.join(self._tmpdir, "store")
        self.cube = CubeClient(self.catalog_path, self.store_path)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # BDC importer
    # ------------------------------------------------------------------

    @patch('fiona.open')
    def test_importer_registers_simulation_grids(self, mock_fiona):
        """import_bdc_grids() creates the BR/5km and BR/1km simulation grids."""
        mock_src = MagicMock()
        mock_src.__enter__.return_value = [
            {
                'properties': {'tile': '001001'},
                'geometry': {'type': 'Polygon',
                             'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
            },
            {
                'properties': {'tile': '001002'},
                'geometry': {'type': 'Polygon',
                             'coordinates': [[[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]]}
            }
        ]
        mock_fiona.return_value = mock_src

        import_bdc_grids(self.cube, "sm.shp", "md.shp", "lg.shp")

        grid_ids = [g.id for g in self.cube.catalog.list_grids()]
        self.assertIn("BR/5km", grid_ids)
        self.assertIn("BR/1km", grid_ids)

    @patch('fiona.open')
    def test_importer_registers_bdc_tile_sources(self, mock_fiona):
        """import_bdc_grids() registers 2 tiles × 3 BDC levels = 6 SpatialSources."""
        mock_src = MagicMock()
        mock_src.__enter__.return_value = [
            {
                'properties': {'tile': '001001'},
                'geometry': {'type': 'Polygon',
                             'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
            },
            {
                'properties': {'tile': '001002'},
                'geometry': {'type': 'Polygon',
                             'coordinates': [[[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]]}
            }
        ]
        mock_fiona.return_value = mock_src

        import_bdc_grids(self.cube, "sm.shp", "md.shp", "lg.shp")

        sources = self.cube.catalog.list_spatial_sources()
        source_ids = [s.id for s in sources]
        # 2 tiles per level × 3 levels (SM, MD, LG)
        self.assertEqual(len(sources), 6)
        self.assertIn("BDC_SM_001001", source_ids)
        self.assertIn("BDC_SM_001002", source_ids)
        self.assertIn("BDC_LG_001002", source_ids)

    # ------------------------------------------------------------------
    # VariableWriter: tile_id extraction from BDC_ source IDs
    # ------------------------------------------------------------------

    @patch('disscube.pipeline.writer.VariableWriter._calculate_dir_hash')
    @patch('xarray.Dataset.to_zarr')
    @patch('fsspec.AbstractFileSystem.exists')
    def test_derive_uses_tile_id(self, mock_exists, mock_to_zarr, mock_hash):
        """VariableWriter extracts tile_id from source IDs following BDC_LEVEL_TILE convention."""
        mock_exists.return_value = False
        mock_hash.return_value = "fake_hash"

        from disscube.models import GridSpec, SpatialSource, SpatialDerivation, Variable
        grid = GridSpec(id="BDC_SM", type="reference", crs="EPSG:31984",
                        resolution=10, bbox=[0, 0, 100, 100])
        source = SpatialSource(id="BDC_SM_001", name="Tile 001",
                               format="raster", asset_url="tile.tif", crs="EPSG:31984")
        self.cube.register_grid(grid)
        self.cube.register_spatial_source(source)

        from disscube.pipeline.writer import VariableWriter
        from disscube.pipeline.context import PipelineContext
        import xarray as xr
        import numpy as np

        derivation = SpatialDerivation(
            source_id="BDC_SM_001", grid_id="BDC_SM", role="test",
            variables=[Variable(name="var1", operator="mean")],
        )
        ds = xr.Dataset({"var1": (("y", "x"), np.random.rand(10, 10))})
        ctx = PipelineContext(source=source, grid=grid, derivation=derivation, data=ds)
        VariableWriter(self.cube.store, self.cube.catalog).execute(ctx)

        derived = self.cube.catalog.search_derived_variables(grid_id="BDC_SM")
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0].tile_id, "001")
        self.assertIn("001", derived[0].asset_url)
        self.assertIn("001", derived[0].id)

    # ------------------------------------------------------------------
    # load() with tile_id
    # ------------------------------------------------------------------

    @patch('fsspec.AbstractFileSystem.exists', return_value=True)
    @patch('xarray.open_zarr')
    def test_load_with_explicit_tile_id_filters_correctly(self, mock_open_zarr, mock_exists):
        """load(name, tile_id='001') returns only the matching tile."""
        from disscube.models import DerivedVariable
        import xarray as xr

        mock_da = MagicMock(spec=xr.DataArray)
        mock_ds = MagicMock()
        mock_ds.__getitem__.return_value = mock_da
        mock_open_zarr.return_value = mock_ds

        d1 = DerivedVariable(
            id="hash_001_var1", name="var1", grid_id="BDC_SM", role="test",
            times=[], dtype="float32", derivation_id="hash", spec_hash="hash",
            tile_id="001", asset_url="path/001.zarr",
        )
        d2 = DerivedVariable(
            id="hash_002_var1", name="var1", grid_id="BDC_SM", role="test",
            times=[], dtype="float32", derivation_id="hash", spec_hash="hash",
            tile_id="002", asset_url="path/002.zarr",
        )
        self.cube.catalog.save_derived(d1)
        self.cube.catalog.save_derived(d2)

        res = self.cube.load("var1", tile_id="001")
        self.assertEqual(res, mock_da)
        mock_open_zarr.assert_called_with(
            self.cube.store.get_full_path("path/001.zarr"), consolidated=False
        )

    @patch('fsspec.AbstractFileSystem.exists', return_value=True)
    @patch('xarray.open_zarr')
    def test_load_without_tile_id_raises_for_ambiguous_tiles(self, mock_open_zarr, mock_exists):
        """load(name) without tile_id raises ValueError when multiple tiles exist on the same grid."""
        from disscube.models import DerivedVariable

        d1 = DerivedVariable(
            id="hash_001_var1", name="var1", grid_id="BDC_SM", role="test",
            times=[], dtype="float32", derivation_id="hash", spec_hash="hash",
            tile_id="001", asset_url="path/001.zarr",
        )
        d2 = DerivedVariable(
            id="hash_002_var1", name="var1", grid_id="BDC_SM", role="test",
            times=[], dtype="float32", derivation_id="hash", spec_hash="hash",
            tile_id="002", asset_url="path/002.zarr",
        )
        self.cube.catalog.save_derived(d1)
        self.cube.catalog.save_derived(d2)

        with self.assertRaises(ValueError):
            self.cube.load("var1")


if __name__ == '__main__':
    unittest.main()
