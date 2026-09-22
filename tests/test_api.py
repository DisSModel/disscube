"""
Tests for the experimental HTTP API (disscube.api).

Skipped when the optional ``api`` extra (fastapi) is not installed.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from disscube.api import create_app  # noqa: E402

GRID = {
    "id": "T/100m",
    "type": "local",
    "crs": "EPSG:31983",
    "resolution": 100.0,
    "bbox": [0.0, 0.0, 400.0, 400.0],
}


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "catalog.db"), str(tmp_path / "store"))
    with TestClient(app) as c:  # the context manager runs the lifespan
        yield c


@pytest.fixture
def raster_path(tmp_path):
    """8x8 GeoTIFF at 50 m covering GRID's bbox; values 0..63."""
    path = tmp_path / "src.tif"
    data = np.arange(64, dtype="float32").reshape(8, 8)
    with rasterio.open(
        path, "w", driver="GTiff", height=8, width=8, count=1, dtype="float32",
        crs="EPSG:31983", transform=from_origin(0.0, 400.0, 50.0, 50.0),
    ) as dst:
        dst.write(data, 1)
    return path


def test_create_app_has_no_side_effects(tmp_path):
    catalog = tmp_path / "catalog.db"
    create_app(str(catalog), str(tmp_path / "store"))
    assert not catalog.exists()  # the catalog is opened only at startup


def test_register_and_list_grids(client):
    assert client.get("/grids").json() == []
    assert client.post("/grids", json=GRID).json() == {"status": "ok"}
    grids = client.get("/grids").json()
    assert [g["id"] for g in grids] == ["T/100m"]


def test_invalid_payload_is_rejected(client):
    r = client.post("/grids", json={"id": "incomplete"})
    assert r.status_code == 422


def test_unknown_variable_returns_404(client):
    assert client.get("/variables/nope").status_code == 404


def test_derive_failure_returns_400(client):
    client.post("/grids", json=GRID)
    r = client.post("/derive", json={
        "source_id": "missing_source",
        "grid_id": "T/100m",
        "role": "driver",
        "variables": [{"name": "v", "operator": "mean"}],
    })
    assert r.status_code == 400


def test_derive_end_to_end(client, raster_path):
    client.post("/grids", json=GRID)
    client.post("/sources", json={
        "id": "src",
        "name": "synthetic raster",
        "format": "raster",
        "asset_url": str(raster_path),
        "crs": "EPSG:31983",
    })
    assert [s["id"] for s in client.get("/sources").json()] == ["src"]

    r = client.post("/derive", json={
        "source_id": "src",
        "grid_id": "T/100m",
        "role": "driver",
        "variables": [{"name": "elev", "operator": "mean"}],
    })
    assert r.status_code == 200, r.text
    derived = r.json()
    assert [d["name"] for d in derived] == ["elev"]

    catalog = client.get("/catalog", params={"grid": "T/100m"}).json()
    assert [d["name"] for d in catalog] == ["elev"]

    meta = client.get("/variables/elev").json()
    assert meta["spec_hash"] == derived[0]["spec_hash"]
    assert meta["asset_url"].endswith("elev.zarr")
