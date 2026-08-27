"""
Operator plugin system for disscube.

Every operator is a concrete subclass of ``Operator``.  Defining ``name``
on the subclass is the only registration step required — ``__init_subclass__``
inserts it into ``OPERATOR_REGISTRY`` automatically.

Adding a new operator:
1. Create a subclass of ``Operator`` anywhere that is imported at startup.
2. Set ``name`` and override ``resampling()`` / ``compute()`` as needed.
3. No changes to Aggregator or any other pipeline stage are required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import numpy as np
import xarray as xr
from rasterio.warp import Resampling

if TYPE_CHECKING:
    import geopandas as gpd
    from disscube.models.grid import GridSpec
    from disscube.models.variable import Variable

# Maps operator name → Operator subclass.  Populated automatically.
OPERATOR_REGISTRY: dict[str, type[Operator]] = {}


class Operator:
    """
    Base class for all disscube derivation operators.

    Subclasses register themselves into ``OPERATOR_REGISTRY`` the moment the
    class body is executed (via ``__init_subclass__``), so there is no
    separate registration call.

    Parameters (class-level)
    ------------------------
    name : str
        Unique operator identifier used in ``Variable.operator`` and
        ``OPERATOR_REGISTRY``.
    requires_class_code : bool
        When ``True``, ``Derivation`` enforces that ``class_code`` is set
        at construction time (fail-fast validation).
    _resampling : Resampling
        Default rasterio resampling method used by ``GridAligner`` for
        raster sources.  Override in subclasses to match the operator's
        aggregation semantics.
    """

    name: ClassVar[str]
    requires_class_code: ClassVar[bool] = False
    # When True, ``Derivation`` enforces that ``mapping`` (a source-value ->
    # target-value lookup table) is set at construction time, the same
    # fail-fast contract ``requires_class_code`` provides for class-aware
    # operators.
    requires_mapping: ClassVar[bool] = False
    _resampling: ClassVar[Resampling] = Resampling.nearest

    # When True, GridAligner must NOT pre-aggregate the band with this
    # operator's ``resampling()``.  Instead it provides a fine-resolution
    # array reprojected with ``Resampling.nearest`` whose origin is snapped
    # to the target grid, at a resolution that is an integer sub-multiple of
    # the target cell size.  The operator's ``compute()`` then performs the
    # window aggregation into the target grid.  Used by categorical operators
    # (percentage / majority / minority) that must see sub-cell class
    # composition rather than a pre-collapsed mode.
    needs_fine_alignment: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "name") and isinstance(cls.name, str):
            OPERATOR_REGISTRY[cls.name] = cls

    @classmethod
    def resampling(cls) -> Resampling:
        """Rasterio resampling method for ``GridAligner``."""
        return cls._resampling

    def compute(
        self,
        data: "xr.DataArray | gpd.GeoDataFrame",
        var: "Variable",
        grid: "GridSpec",
    ) -> xr.DataArray:
        """
        Derive a single variable from aligned source data.

        Parameters
        ----------
        data : xr.DataArray | geopandas.GeoDataFrame
            Source already reprojected and clipped to ``grid`` extent.
            ``DataArray`` for raster sources; ``GeoDataFrame`` for vectors.
        var : Variable
            Operator name and optional ``class_code`` for this variable.
        grid : GridSpec
            Target grid — provides ``rows``, ``cols``, ``transform``,
            ``xs``, ``ys``, ``resolution``, and ``bbox``.

        Returns
        -------
        xr.DataArray
            Shape ``(rows, cols)`` with dims ``("y", "x")`` and coords
            matching ``grid.ys`` / ``grid.xs``.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.compute() is not implemented"
        )