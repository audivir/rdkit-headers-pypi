"""Tests for build_boost/build_rdkit subprocess orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

import pytest

from rdkit_headers_pypi.builder import BOOST_LIBRARIES, HeaderBuilder


@pytest.fixture
def builder(tmp_path: Path) -> HeaderBuilder:
    return HeaderBuilder(tmp_path / "cache", tmp_path / "dist")


def test_build_boost_skips_compiling_if_headers_already_installed(builder: HeaderBuilder) -> None:
    install_dir = builder.cache_dir / "boost_inst_1_85_0"
    (install_dir / "include" / "boost").mkdir(parents=True)

    with patch("rdkit_headers_pypi.builder.subprocess.check_call") as mock_check_call:
        result = builder.build_boost("1.85.0")

    mock_check_call.assert_not_called()
    assert result == install_dir


def test_build_boost_bootstraps_and_compiles_only_the_required_libraries(
    builder: HeaderBuilder,
) -> None:
    extract_dir = builder.cache_dir / "boost_1_85_0"
    extract_dir.mkdir(parents=True)

    with (
        patch("rdkit_headers_pypi.builder.download", return_value=Path("archive.tar.gz")),
        patch("rdkit_headers_pypi.builder.safe_extract") as mock_extract,
        patch("rdkit_headers_pypi.builder.subprocess.check_call") as mock_check_call,
    ):
        result = builder.build_boost("1.85.0")

    mock_extract.assert_not_called()  # extract_dir already exists
    install_dir = builder.cache_dir / "boost_inst_1_85_0"
    assert result == install_dir

    bootstrap_call, b2_call = mock_check_call.call_args_list
    # bootstrap.sh must not restrict --with-libraries
    assert bootstrap_call == call(["./bootstrap.sh"], cwd=extract_dir)
    b2_args = b2_call.args[0]
    assert b2_args[0] == "./b2"
    assert f"--prefix={install_dir.absolute()}" in b2_args
    for library in BOOST_LIBRARIES:
        assert f"--with-{library}" in b2_args


def test_build_boost_extracts_the_archive_when_not_already_extracted(
    builder: HeaderBuilder,
) -> None:
    with (
        patch("rdkit_headers_pypi.builder.download", return_value=Path("archive.tar.gz")),
        patch("rdkit_headers_pypi.builder.safe_extract") as mock_extract,
        patch("rdkit_headers_pypi.builder.subprocess.check_call"),
    ):
        builder.build_boost("1.85.0")

    mock_extract.assert_called_once_with(Path("archive.tar.gz"), builder.cache_dir)


def test_build_rdkit_skips_configuring_if_already_installed(builder: HeaderBuilder) -> None:
    install_dir = builder.cache_dir / "rdkit_inst_2026_03_5"
    install_dir.mkdir(parents=True)
    boost_install_dir = builder.cache_dir / "boost_inst_1_85_0"

    with (
        patch("rdkit_headers_pypi.builder.download_wheel", return_value=Path("rdkit.whl")),
        patch("rdkit_headers_pypi.builder.extract_boost_version", return_value="1.85.0"),
        patch("rdkit_headers_pypi.builder.download", return_value=Path("rdkit.tar.gz")),
        patch.object(builder, "build_boost", return_value=boost_install_dir) as mock_build_boost,
        patch("rdkit_headers_pypi.builder.subprocess.check_call") as mock_check_call,
    ):
        result = builder.build_rdkit("2026.3.5")

    mock_build_boost.assert_called_once_with("1.85.0")
    mock_check_call.assert_not_called()
    assert result == (install_dir, "1.85.0", boost_install_dir)


def test_build_rdkit_raises_if_no_rdkit_wheel_is_found(builder: HeaderBuilder) -> None:
    with (
        patch("rdkit_headers_pypi.builder.download_wheel", return_value=None),
        pytest.raises(ValueError, match=r"RDKit wheel for version 2026\.3\.5 not found"),
    ):
        builder.build_rdkit("2026.3.5")


def test_build_rdkit_raises_if_boost_version_cannot_be_extracted(builder: HeaderBuilder) -> None:
    with (
        patch("rdkit_headers_pypi.builder.download_wheel", return_value=Path("rdkit.whl")),
        patch("rdkit_headers_pypi.builder.extract_boost_version", return_value=None),
        pytest.raises(ValueError, match="Boost version could not be extracted"),
    ):
        builder.build_rdkit("2026.3.5")


def test_build_rdkit_zero_pads_the_month_in_the_github_tag(builder: HeaderBuilder) -> None:
    boost_install_dir = builder.cache_dir / "boost_inst_1_85_0"
    # safe_extract is mocked below (it would normally create this), so create it directly.
    (builder.cache_dir / "rdkit-Release_2026_03_5").mkdir(parents=True)

    with (
        patch("rdkit_headers_pypi.builder.download_wheel", return_value=Path("rdkit.whl")),
        patch("rdkit_headers_pypi.builder.extract_boost_version", return_value="1.85.0"),
        patch("rdkit_headers_pypi.builder.download") as mock_download,
        patch.object(builder, "build_boost", return_value=boost_install_dir),
        patch("rdkit_headers_pypi.builder.safe_extract"),
        patch("rdkit_headers_pypi.builder.subprocess.check_call") as mock_check_call,
        patch("rdkit_headers_pypi.builder.install_headers"),
    ):
        builder.build_rdkit("2026.3.5")

    url = mock_download.call_args.args[0]
    assert "Release_2026_03_5.tar.gz" in url

    cmake_args = mock_check_call.call_args.args[0]
    assert cmake_args[0] == "cmake"
    assert "-DRDK_BUILD_PYTHON_WRAPPERS=OFF" in cmake_args
    assert "-DRDK_BUILD_FREETYPE_SUPPORT=OFF" in cmake_args
    assert not any(arg.startswith("-DBoost_ROOT") for arg in cmake_args)

    # Boost_ROOT must be passed via environment, not -D: the legacy FindBoost module only
    # accepts BOOST_ROOT, while newer RDKit's find_package(Boost ... CONFIG) needs Boost_ROOT.
    cmake_env = mock_check_call.call_args.kwargs["env"]
    assert cmake_env["BOOST_ROOT"] == str(boost_install_dir.absolute())
    assert cmake_env["Boost_ROOT"] == str(boost_install_dir.absolute())


def test_build_rdkit_removes_the_stale_checkout_when_clean_cache_is_set(tmp_path: Path) -> None:
    builder = HeaderBuilder(tmp_path / "cache", tmp_path / "dist", clean_cache=True)
    extract_dir = builder.cache_dir / "rdkit-Release_2026_03_5"
    (extract_dir / "stale_file").mkdir(parents=True)
    boost_install_dir = builder.cache_dir / "boost_inst_1_85_0"

    def fake_extract(unused_tar_path: Path, unused_extract_to: Path) -> None:
        extract_dir.mkdir(parents=True)

    with (
        patch("rdkit_headers_pypi.builder.download_wheel", return_value=Path("rdkit.whl")),
        patch("rdkit_headers_pypi.builder.extract_boost_version", return_value="1.85.0"),
        patch("rdkit_headers_pypi.builder.download", return_value=Path("rdkit.tar.gz")),
        patch.object(builder, "build_boost", return_value=boost_install_dir),
        patch("rdkit_headers_pypi.builder.safe_extract", side_effect=fake_extract) as mock_extract,
        patch("rdkit_headers_pypi.builder.subprocess.check_call"),
        patch("rdkit_headers_pypi.builder.install_headers"),
    ):
        builder.build_rdkit("2026.3.5")

    mock_extract.assert_called_once_with(Path("rdkit.tar.gz"), builder.cache_dir)
    assert not (extract_dir / "stale_file").exists()
