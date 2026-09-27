from pathlib import Path
from unittest.mock import patch
import pytest
from disscube.config.schema import FileSource
from disscube.config.runner import _fetch_file_source

def test_fetch_file_source_calls_pooch(tmp_path):
    target = tmp_path / "data" / "ferrovias.zip"
    source = FileSource(
        id="ferrovias",
        type="file",
        path="data/ferrovias.zip",
        url="https://example.com/ferrovias.zip",
        sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )

    with patch("pooch.retrieve") as mock_retrieve:
        mock_retrieve.return_value = str(target)
        result = _fetch_file_source(source, target)

        assert result == target
        mock_retrieve.assert_called_once_with(
            url="https://example.com/ferrovias.zip",
            known_hash="sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            path=tmp_path / "data",
            fname="ferrovias.zip",
            downloader=None,
        )
