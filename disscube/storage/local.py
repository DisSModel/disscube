"""Backward-compatibility shim for AssetStore (now canonically in disscube.storage)."""

from __future__ import annotations

from disscube.storage import AssetStore

__all__ = ["AssetStore"]
