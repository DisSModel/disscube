"""Experimental HTTP API — requires ``pip install "disscube[api]"``."""

from .app import app, create_app

__all__ = ["app", "create_app"]
