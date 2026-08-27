"""Tests for HeaderBuilder.create_python_package."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from rdkit_headers_pypi.builder import HeaderBuilder, PackageSpec


@pytest.fixture
def builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HeaderBuilder:
    monkeypatch.chdir(tmp_path)
    return HeaderBuilder(tmp_path / "cache", tmp_path / "dist")


def test_create_python_package_renders_metadata_and_syncs_headers(
    builder: HeaderBuilder, tmp_path: Path
) -> None:
    include_src = tmp_path / "install"
    (include_src / "include" / "boost").mkdir(parents=True)
    (include_src / "include" / "boost" / "version.hpp").write_text("// version")

    spec = PackageSpec("boost-headers", "Boost headers.", "BSL-1.0")

    with patch("rdkit_headers_pypi.builder.build.ProjectBuilder") as mock_project_builder:
        builder.create_python_package(spec, "1.85.0", include_src, "0.1.0")

    pkg_root = tmp_path / "boost-headers"
    pyproject = (pkg_root / "pyproject.toml").read_text()
    assert 'name = "boost-headers"' in pyproject
    assert 'dynamic = ["version"]' in pyproject
    assert 'license = "BSL-1.0"' in pyproject

    init_py = (pkg_root / "boost_headers" / "__init__.py").read_text()
    assert '__version__ = "1.85.0"' in init_py
    assert '__builder_version__ = "0.1.0"' in init_py

    assert (pkg_root / "boost_headers" / "include" / "boost" / "version.hpp").read_text() == (
        "// version"
    )

    mock_project_builder.assert_called_once_with(Path("boost-headers"))
    build_instance = mock_project_builder.return_value
    build_instance.build.assert_any_call("wheel", str(tmp_path / "dist"))
    build_instance.build.assert_any_call("sdist", str(tmp_path / "dist"))


def test_create_python_package_clears_stale_setuptools_build_cache(
    builder: HeaderBuilder, tmp_path: Path
) -> None:
    include_src = tmp_path / "install"
    (include_src / "include").mkdir(parents=True)

    pkg_root = tmp_path / "boost-headers"
    (pkg_root / "build" / "lib").mkdir(parents=True)
    (pkg_root / "boost_headers.egg-info").mkdir(parents=True)

    spec = PackageSpec("boost-headers", "Boost headers.", "BSL-1.0")
    with patch("rdkit_headers_pypi.builder.build.ProjectBuilder"):
        builder.create_python_package(spec, "1.85.0", include_src, "0.1.0")

    assert not (pkg_root / "build").exists()
    assert not (pkg_root / "boost_headers.egg-info").exists()
