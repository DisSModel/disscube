"""
Operator registry for disscube.

Importing this package is sufficient to populate ``OPERATOR_REGISTRY`` —
each submodule defines ``Operator`` subclasses that self-register via
``__init_subclass__``.
"""

# Import submodules to trigger auto-registration of all operator classes.
from . import proximity, zonal  # noqa: F401
from .base import OPERATOR_REGISTRY, Operator
from .proximity import ProximityAggregator
from .zonal import ZonalAggregator

__all__ = [
    "OPERATOR_REGISTRY",
    "Operator",
    "ProximityAggregator",   # legacy shim
    "ZonalAggregator",       # legacy shim
]
