from unittest.mock import patch

from disscube.config.runner import _fetch_file_source
from disscube.config.schema import FileSource


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


def test_prodes_url_scheme_guard():
    import pytest

    from disscube.sources.prodes import _require_scheme

    assert _require_scheme("https://example.org/a.zip") == "https://example.org/a.zip"
    assert _require_scheme("file:///tmp/a.zip", ("http", "https", "file")).startswith("file://")
    with pytest.raises(ValueError, match="scheme not allowed"):
        _require_scheme("ftp://example.org/a.zip")
    with pytest.raises(ValueError, match="scheme not allowed"):
        _require_scheme("file:///etc/passwd")  # file:// is rejected unless explicitly allowed
