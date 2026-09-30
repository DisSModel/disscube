from unittest.mock import patch

from disscube.pipeline.runner import _fetch_file_source
from disscube.pipeline.schema import FileSource


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
        mock_retrieve.assert_called_once()
        _, kwargs = mock_retrieve.call_args
        assert kwargs["url"] == "https://example.com/ferrovias.zip"
        assert kwargs["known_hash"] == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert kwargs["path"] == tmp_path / "data"
        assert kwargs["fname"] == "ferrovias.zip"
        assert kwargs["downloader"].kwargs["headers"] == {"User-Agent": "disscube"}


def test_fetch_file_source_github_token_for_api(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token123")
    target = tmp_path / "data" / "roads.zip"
    source = FileSource(
        id="roads",
        type="file",
        path="data/roads.zip",
        url="https://api.github.com/repos/org/repo/releases/assets/456",
    )

    with patch("pooch.retrieve") as mock_retrieve:
        mock_retrieve.return_value = str(target)
        _fetch_file_source(source, target)

        mock_retrieve.assert_called_once()
        _, kwargs = mock_retrieve.call_args
        headers = kwargs["downloader"].kwargs["headers"]
        assert headers["Authorization"] == "Bearer token123"
        assert headers["Accept"] == "application/octet-stream"


def test_fetch_file_source_github_web_release_avoids_api_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token123")
    target = tmp_path / "data" / "roads.zip"
    source = FileSource(
        id="roads",
        type="file",
        path="data/roads.zip",
        url="https://github.com/org/repo/releases/download/v1.0/roads.zip",
    )

    with patch("pooch.retrieve") as mock_retrieve:
        mock_retrieve.return_value = str(target)
        _fetch_file_source(source, target)

        mock_retrieve.assert_called_once()
        _, kwargs = mock_retrieve.call_args
        headers = kwargs["downloader"].kwargs["headers"]
        assert "Authorization" not in headers
        assert headers == {"User-Agent": "disscube"}


def test_fetch_file_source_extracts_zip(tmp_path):
    import zipfile

    archive_path = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("roads.shp", "shapefile content")
        zf.writestr("roads.dbf", "dbf content")

    target = tmp_path / "extracted" / "roads.shp"
    source = FileSource(
        id="roads",
        type="file",
        path="extracted/roads.shp",
        url="https://example.com/archive.zip",
        archive="zip",
    )

    with patch("pooch.retrieve") as mock_retrieve:
        mock_retrieve.return_value = str(archive_path)
        result = _fetch_file_source(source, target)

        assert result == target
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "shapefile content"
        assert (tmp_path / "extracted" / "roads.dbf").exists()



def test_prodes_url_scheme_guard():
    import pytest

    from disscube.sources.prodes import _require_scheme

    assert _require_scheme("https://example.org/a.zip") == "https://example.org/a.zip"
    assert _require_scheme("file:///tmp/a.zip", ("http", "https", "file")).startswith("file://")
    with pytest.raises(ValueError, match="scheme not allowed"):
        _require_scheme("ftp://example.org/a.zip")
    with pytest.raises(ValueError, match="scheme not allowed"):
        _require_scheme("file:///etc/passwd")  # file:// is rejected unless explicitly allowed
