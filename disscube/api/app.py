"""
Experimental HTTP API for DisSCube.

Scope: remote *orchestration* of the catalog — register grids and sources,
trigger derivations, and query what has been derived. It does **not** serve
raster data: models read derived variables in-process through
``CubeClient.load()`` / ``CubeClient.to_lucc_data()``, from the same Zarr
store (local or S3 via fsspec) that the API writes to.

Requires the optional ``api`` extra::

    pip install "disscube[api]"
    uvicorn disscube.api.app:app

The catalog and store locations come from ``DISSCUBE_CATALOG`` and
``DISSCUBE_STORE`` (see :mod:`disscube.api.config`) and are opened when the
application starts, not when this module is imported.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

try:
    from fastapi import Depends, FastAPI, HTTPException, Request
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "The DisSCube HTTP API requires the optional 'api' extra: pip install \"disscube[api]\""
    ) from exc

from disscube.client import CubeClient
from disscube.models import DerivedVariable, GridSpec, SpatialDerivation, SpatialSource

from . import config


def get_cube(request: Request) -> CubeClient:
    """Dependency: the CubeClient opened in the application lifespan."""
    return request.app.state.cube


Cube = Annotated[CubeClient, Depends(get_cube)]


def create_app(catalog_path: str | None = None, store_path: str | None = None) -> FastAPI:
    """
    Build the API application.

    Parameters default to ``config.CATALOG_PATH`` / ``config.STORE_PATH``.
    The ``CubeClient`` is created in the application lifespan, so building
    the app has no side effects on disk.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.cube = CubeClient(
            catalog_path or config.CATALOG_PATH,
            store_path or config.STORE_PATH,
        )
        yield

    app = FastAPI(
        title="DisSCube API (experimental)",
        description="Catalog orchestration for DisSCube. Does not serve raster data.",
        lifespan=lifespan,
    )

    @app.get("/grids", response_model=list[GridSpec])
    def list_grids(cube: Cube):
        return cube.catalog.list_grids()

    @app.post("/grids")
    def register_grid(grid: GridSpec, cube: Cube):
        cube.register_grid(grid)
        return {"status": "ok"}

    @app.get("/sources", response_model=list[SpatialSource])
    def list_spatial_sources(cube: Cube):
        return cube.catalog.list_spatial_sources()

    @app.post("/sources")
    def register_spatial_source(source: SpatialSource, cube: Cube):
        cube.register_spatial_source(source)
        return {"status": "ok"}

    @app.post("/derive", response_model=list[DerivedVariable])
    def derive(derivation: SpatialDerivation, cube: Cube):
        try:
            return cube.derive(derivation)
        except Exception as e:  # API boundary: report any derivation failure as a 400
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/catalog", response_model=list[DerivedVariable])
    def get_catalog(cube: Cube, grid: str | None = None, role: str | None = None):
        return cube.search(grid=grid, role=role)

    @app.get("/variables/{variable_id}", response_model=DerivedVariable)
    def get_variable(variable_id: str, cube: Cube):
        """Metadata of a derived variable (including its Zarr ``asset_url``), not its data."""
        for d in cube.search():
            if d.id == variable_id or d.name == variable_id:
                return d
        raise HTTPException(status_code=404, detail="Variable not found")

    return app


app = create_app()
