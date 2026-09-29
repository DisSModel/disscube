"""Backward-compatibility shim for API configuration."""

from __future__ import annotations

from disscube.api.app import DEFAULT_CATALOG_PATH as CATALOG_PATH
from disscube.api.app import DEFAULT_STORE_PATH as STORE_PATH

__all__ = ["CATALOG_PATH", "STORE_PATH"]
