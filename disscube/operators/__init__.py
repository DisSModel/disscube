"""
Operator registry for disscube.

Importing this package is sufficient to populate ``OPERATOR_REGISTRY`` —
each submodule defines ``Operator`` subclasses that self-register via
``__init_subclass__``.
"""

# Import submodules to trigger auto-registration of all operator classes.
from . import zonal, proximity, reclassify  # noqa: F401

from .zonal import ZonalAggregator
from .proximity import ProximityAggregator
from .base import OPERATOR_REGISTRY, Operator

__all__ = [
    "Operator",
    "OPERATOR_REGISTRY",
    "ZonalAggregator",       # legacy shim
    "ProximityAggregator",   # legacy shim
]
