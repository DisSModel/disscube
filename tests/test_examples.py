"""
Run every script in examples/ as a user would, in a fresh working directory.

Keeps the examples in sync with the API: a change that breaks an example
fails the suite.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "examples").glob("[0-9][0-9]_*.py"))


def test_examples_are_discovered():
    assert EXAMPLES, "no examples found"


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.name)
def test_example_runs(script, tmp_path):
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "workspace")],
        cwd=tmp_path, capture_output=True, text=True, timeout=300, check=False,
        # examples that download data (07_bdc_cube.py) use their offline stand-in
        env={**os.environ, "DISSCUBE_OFFLINE": "1"},
    )
    assert result.returncode == 0, f"{script.name} failed:\n{result.stdout}\n{result.stderr}"
    assert (tmp_path / "workspace" / "catalog.db").exists()
