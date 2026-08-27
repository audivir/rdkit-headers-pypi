"""Tests for builder.py helper functions and HeaderBuilder setup."""

from __future__ import annotations

import tarfile
import zipfile
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from rdkit_headers_pypi.builder import (
    HeaderBuilder,
    PackageSpec,
    extract_boost_version,
    install_headers,
    safe_extract,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_safe_extract_extracts_regular_members(tmp_path: Path) -> None:
    tar_path = tmp_path / "archive.tar.gz"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "file.txt").write_text("content")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(source_dir, arcname="pkg")

    extract_to = tmp_path / "out"
    extract_to.mkdir()
    safe_extract(tar_path, extract_to)

    assert (extract_to / "pkg" / "file.txt").read_text() == "content"


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = 0
        tar.addfile(info)

    extract_to = tmp_path / "out"
    extract_to.mkdir()
    with pytest.raises(ValueError, match="Tarball contains path traversal attempt"):
        safe_extract(tar_path, extract_to)


def test_safe_extract_rejects_absolute_path_members(tmp_path: Path) -> None:
    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = 0
        tar.addfile(info)

    extract_to = tmp_path / "out"
    extract_to.mkdir()
    with pytest.raises(ValueError, match="Tarball contains path traversal attempt"):
        safe_extract(tar_path, extract_to)


def test_safe_extract_rejects_nested_traversal(tmp_path: Path) -> None:
    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        info = tarfile.TarInfo(name="subdir/../../escape.txt")
        info.size = 0
        tar.addfile(info)

    extract_to = tmp_path / "out"
    extract_to.mkdir()
    with pytest.raises(ValueError, match="Tarball contains path traversal attempt"):
        safe_extract(tar_path, extract_to)


def test_install_headers_replays_file_and_directory_install_rules(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    module_dir = build_dir / "Code" / "RDGeneral"
    module_dir.mkdir(parents=True)

    header = tmp_path / "source" / "Code" / "RDGeneral" / "types.h"
    header.parent.mkdir(parents=True)
    header.write_text("// types")

    hash_dir = tmp_path / "source" / "Code" / "RDGeneral" / "hash"
    hash_dir.mkdir(parents=True)
    (hash_dir / "hash.hpp").write_text("// hash")

    lib = tmp_path / "source" / "build" / "lib" / "libRDKitRDGeneral.dylib"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"not a real library")

    (module_dir / "cmake_install.cmake").write_text(
        f"""
if(CMAKE_INSTALL_COMPONENT STREQUAL "runtime" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${{CMAKE_INSTALL_PREFIX}}/lib" TYPE SHARED_LIBRARY FILES
    "{lib}"
    )
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "dev" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${{CMAKE_INSTALL_PREFIX}}/include/rdkit/RDGeneral" TYPE FILE FILES
    "{header}"
    )
  file(INSTALL DESTINATION "${{CMAKE_INSTALL_PREFIX}}/include/rdkit/RDGeneral" TYPE DIRECTORY FILES
    "{hash_dir}"
    )
endif()
"""
    )

    install_dir = tmp_path / "install"
    install_headers(build_dir, install_dir)

    assert (install_dir / "include" / "rdkit" / "RDGeneral" / "types.h").read_text() == "// types"
    assert (
        install_dir / "include" / "rdkit" / "RDGeneral" / "hash" / "hash.hpp"
    ).read_text() == "// hash"
    # the library install(...) block must never be replayed (its source is never compiled).
    assert not (install_dir / "lib").exists()


def test_install_headers_skips_a_source_file_that_no_longer_exists(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "cmake_install.cmake").write_text(
        'file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/include/rdkit/X" TYPE FILE FILES\n'
        '  "/nonexistent/missing.h"\n'
        "  )\n"
    )

    install_dir = tmp_path / "install"
    install_headers(build_dir, install_dir)

    assert not any(p.is_file() for p in (install_dir / "include").rglob("*"))


def test_extract_boost_version_reads_embedded_so_version(tmp_path: Path) -> None:
    wheel_path = tmp_path / "rdkit.whl"
    with zipfile.ZipFile(wheel_path, "w") as whl:
        whl.writestr("rdkit.libs/libboost_python312-abc123.so.1.85.0", b"")

    assert extract_boost_version(wheel_path) == "1.85.0"


def test_extract_boost_version_raises_if_no_boost_library_present(tmp_path: Path) -> None:
    wheel_path = tmp_path / "rdkit.whl"
    with zipfile.ZipFile(wheel_path, "w") as whl:
        whl.writestr("rdkit.libs/libfreetype.so", b"")

    assert extract_boost_version(wheel_path) is None


def test_extract_boost_version_raises_if_version_suffix_is_missing(tmp_path: Path) -> None:
    wheel_path = tmp_path / "rdkit.whl"
    with zipfile.ZipFile(wheel_path, "w") as whl:
        whl.writestr("rdkit.libs/libboost_python312.dylib", b"")

    assert extract_boost_version(wheel_path) is None


def test_header_builder_raises_dependency_error_if_a_required_tool_is_missing(
    tmp_path: Path,
) -> None:
    with (
        patch("rdkit_headers_pypi.builder.shutil.which", return_value=None),
        pytest.raises(ValueError, match=r"Required system tool '\w+' not found in PATH"),
    ):
        HeaderBuilder(tmp_path / "cache", tmp_path / "dist")


def test_header_builder_creates_cache_and_dist_directories(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    dist_dir = tmp_path / "dist"
    with patch("rdkit_headers_pypi.builder.shutil.which", return_value="/usr/bin/tool"):
        HeaderBuilder(cache_dir, dist_dir)

    assert cache_dir.is_dir()
    assert dist_dir.is_dir()


def test_package_spec_dependencies_default_to_empty_tuple() -> None:
    spec = PackageSpec("boost-headers", "Boost headers.", "BSL-1.0")
    assert spec.dependencies == ()
