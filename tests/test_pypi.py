"""Tests for PyPI lookup and download helpers."""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from rdkit_headers_pypi.pypi import (
    download,
    download_wheel,
    get_builder_version,
    list_published_versions,
    list_rdkit_versions,
    needs_build,
    resolve_release_version,
)

if TYPE_CHECKING:
    from pathlib import Path


def _mock_response(json_data: object, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    return response


def test_list_rdkit_versions_keeps_only_releases_with_a_wheel_and_sorts_by_version() -> None:
    releases = {
        "2024.9.6": [{"packagetype": "sdist"}],
        "2025.9.3": [{"packagetype": "bdist_wheel"}],
        "2024.3.1": [{"packagetype": "bdist_wheel"}, {"packagetype": "sdist"}],
    }
    response = _mock_response({"releases": releases})
    with patch("rdkit_headers_pypi.pypi._session.get", return_value=response):
        assert list_rdkit_versions() == ["2024.3.1", "2025.9.3"]


def test_list_published_versions_returns_empty_set_for_unpublished_package() -> None:
    response = _mock_response({}, status_code=404)
    with patch("rdkit_headers_pypi.pypi._session.get", return_value=response):
        assert list_published_versions("rdkit-headers") == set()


def test_list_published_versions_returns_release_keys() -> None:
    response = _mock_response({"releases": {"1.85.0": [], "1.86.0": []}})
    with patch("rdkit_headers_pypi.pypi._session.get", return_value=response):
        assert list_published_versions("boost-headers") == {"1.85.0", "1.86.0"}


def test_download_wheel_filters_by_platform_substring(tmp_path: Path) -> None:
    files = [
        {"packagetype": "sdist", "filename": "rdkit-1.0.tar.gz", "url": "http://x/sdist"},
        {
            "packagetype": "bdist_wheel",
            "filename": "rdkit-1.0-cp312-macosx.whl",
            "url": "http://x/mac",
        },
        {
            "packagetype": "bdist_wheel",
            "filename": "rdkit-1.0-cp312-manylinux.whl",
            "url": "http://x/linux",
        },
    ]
    response = _mock_response({"urls": files})
    with (
        patch("rdkit_headers_pypi.pypi._session.get", return_value=response),
        patch("rdkit_headers_pypi.pypi.download") as mock_download,
    ):
        download_wheel("rdkit", "1.0", tmp_path, platform="linux")
    mock_download.assert_called_once_with(
        "http://x/linux", tmp_path / "rdkit-1.0-cp312-manylinux.whl"
    )


def test_download_wheel_raises_dependency_error_if_no_wheel_matches(tmp_path: Path) -> None:
    files = [{"packagetype": "sdist", "filename": "x.tar.gz", "url": "http://x/sdist"}]
    response = _mock_response({"urls": files})
    with patch("rdkit_headers_pypi.pypi._session.get", return_value=response):
        assert download_wheel("rdkit", "1.0", tmp_path) is None


def test_download_reuses_cached_file(tmp_path: Path) -> None:
    output_path = tmp_path / "cached.tar.gz"
    output_path.write_bytes(b"cached")

    with patch("rdkit_headers_pypi.pypi._session.get") as mock_get:
        result = download("http://example.com/file", output_path)

    mock_get.assert_not_called()
    assert result == output_path


def test_download_streams_response_to_output_path(tmp_path: Path) -> None:
    output_path = tmp_path / "fresh.tar.gz"
    response = MagicMock()
    response.raw = io.BytesIO(b"downloaded content")
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch("rdkit_headers_pypi.pypi._session.get", return_value=response) as mock_get:
        result = download("http://example.com/file", output_path)

    mock_get.assert_called_once()
    assert result.read_bytes() == b"downloaded content"


def _make_wheel(tmp_path: Path, pkg_name: str, init_contents: str) -> Path:
    wheel_path = tmp_path / "pkg.whl"
    with zipfile.ZipFile(wheel_path, "w") as whl:
        whl.writestr(f"{pkg_name}/__init__.py", init_contents)
    return wheel_path


def test_get_builder_version_reads_the_declared_value(tmp_path: Path) -> None:
    wheel_path = _make_wheel(
        tmp_path, "boost_headers", '__version__ = "1.85.0"\n__builder_version__ = "0.1.0"\n'
    )
    with patch("rdkit_headers_pypi.pypi.download_wheel", return_value=wheel_path):
        assert get_builder_version("boost-headers", "1.85.0") == "0.1.0"


def test_get_builder_version_returns_none_if_unpublished() -> None:
    with patch("rdkit_headers_pypi.pypi.download_wheel", return_value=None):
        assert get_builder_version("boost-headers", "9.9.9") is None


def test_get_builder_version_returns_none_if_the_field_predates_tracking(tmp_path: Path) -> None:
    wheel_path = _make_wheel(tmp_path, "boost_headers", '__version__ = "1.85.0"\n')
    with patch("rdkit_headers_pypi.pypi.download_wheel", return_value=wheel_path):
        assert get_builder_version("boost-headers", "1.85.0") is None


def test_get_builder_version_returns_none_if_init_py_is_missing(tmp_path: Path) -> None:
    wheel_path = tmp_path / "empty.whl"
    with zipfile.ZipFile(wheel_path, "w"):
        pass
    with patch("rdkit_headers_pypi.pypi.download_wheel", return_value=wheel_path):
        assert get_builder_version("boost-headers", "1.85.0") is None


def test_resolve_release_version_returns_bare_version_if_unpublished() -> None:
    with patch("rdkit_headers_pypi.pypi.list_published_versions", return_value=set()):
        assert resolve_release_version("boost-headers", "1.85.0", "0.2.0") == "1.85.0"


def test_resolve_release_version_returns_bare_version_if_up_to_date() -> None:
    with (
        patch("rdkit_headers_pypi.pypi.list_published_versions", return_value={"1.85.0"}),
        patch("rdkit_headers_pypi.pypi.get_builder_version", return_value="0.2.0"),
    ):
        assert resolve_release_version("boost-headers", "1.85.0", "0.2.0") == "1.85.0"


def test_resolve_release_version_bumps_to_the_first_postfix_if_stale() -> None:
    with (
        patch("rdkit_headers_pypi.pypi.list_published_versions", return_value={"1.85.0"}),
        patch("rdkit_headers_pypi.pypi.get_builder_version", return_value="0.1.0"),
    ):
        assert resolve_release_version("boost-headers", "1.85.0", "0.2.0") == "1.85.0.post1"


def test_resolve_release_version_increments_past_existing_postfixes_if_stale() -> None:
    published = {"1.85.0", "1.85.0.post1", "1.85.0.post2"}
    with (
        patch("rdkit_headers_pypi.pypi.list_published_versions", return_value=published),
        patch("rdkit_headers_pypi.pypi.get_builder_version", return_value="0.1.0"),
    ):
        assert resolve_release_version("boost-headers", "1.85.0", "0.2.0") == "1.85.0.post3"


def test_resolve_release_version_returns_latest_postfix_if_already_up_to_date() -> None:
    published = {"1.85.0", "1.85.0.post1"}
    with (
        patch("rdkit_headers_pypi.pypi.list_published_versions", return_value=published),
        patch("rdkit_headers_pypi.pypi.get_builder_version", return_value="0.2.0"),
    ):
        assert resolve_release_version("boost-headers", "1.85.0", "0.2.0") == "1.85.0.post1"


def test_needs_build_when_never_published() -> None:
    assert needs_build("rdkit-headers", "2026.3.5", set(), "0.2.0") is True


def test_needs_build_when_latest_generation_is_stale() -> None:
    published = {"2025.9.3", "2025.9.3.post1"}
    with patch("rdkit_headers_pypi.pypi.get_builder_version", return_value="0.1.0"):
        assert needs_build("rdkit-headers", "2025.9.3", published, "0.2.0") is True


def test_needs_build_when_latest_generation_is_up_to_date() -> None:
    published = {"2025.9.3", "2025.9.3.post1"}
    with patch("rdkit_headers_pypi.pypi.get_builder_version", return_value="0.2.0") as mock_get:
        assert needs_build("rdkit-headers", "2025.9.3", published, "0.2.0") is False
    mock_get.assert_called_once_with("rdkit-headers", "2025.9.3.post1")
