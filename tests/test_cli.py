"""Tests for the build and versions CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rdkit_headers_pypi import __version__ as BUILDER_VERSION  # noqa: N812
from rdkit_headers_pypi.cli import build, main, versions


def test_build_creates_boost_and_rdkit_packages_from_the_detected_boost_version(
    tmp_path: Path,
) -> None:
    mock_builder = MagicMock()
    mock_builder.build_rdkit.return_value = (Path("rdkit_inst"), "1.85.0", Path("boost_inst"))

    with (
        patch("rdkit_headers_pypi.cli.HeaderBuilder", return_value=mock_builder),
        # unpublished, so resolve_release_version returns the bare version unchanged
        patch("rdkit_headers_pypi.pypi.list_published_versions", return_value=set()),
    ):
        build("2026.3.5", cache=tmp_path / "cache", dist=tmp_path / "dist")

    calls = mock_builder.create_python_package.call_args_list
    boost_spec, boost_version, boost_src, boost_builder_version = calls[0][0]
    assert (boost_spec.name, boost_version, boost_src) == (
        "boost-headers",
        "1.85.0",
        Path("boost_inst"),
    )
    assert boost_builder_version == BUILDER_VERSION

    rdkit_spec, rdkit_version, rdkit_src, rdkit_builder_version = calls[1][0]
    assert (rdkit_spec.name, rdkit_version, rdkit_src) == (
        "rdkit-headers",
        "2026.3.5",
        Path("rdkit_inst"),
    )
    assert rdkit_builder_version == BUILDER_VERSION
    assert rdkit_spec.dependencies == ["boost-headers~=1.85.0", "rdkit==2026.3.5", "numpy<2.0"]


def test_build_bumps_postfix_when_republishing_under_a_newer_builder_version(
    tmp_path: Path,
) -> None:
    mock_builder = MagicMock()
    mock_builder.build_rdkit.return_value = (Path("rdkit_inst"), "1.85.0", Path("boost_inst"))

    def fake_published(package: str) -> set[str]:
        return {"1.85.0"} if package == "boost-headers" else {"2026.3.5"}

    with (
        patch("rdkit_headers_pypi.cli.HeaderBuilder", return_value=mock_builder),
        patch("rdkit_headers_pypi.pypi.list_published_versions", side_effect=fake_published),
        patch("rdkit_headers_pypi.pypi.get_builder_version", return_value="0.0.1"),
    ):
        build("2026.3.5", cache=tmp_path / "cache", dist=tmp_path / "dist")

    calls = mock_builder.create_python_package.call_args_list
    assert calls[0][0][1] == "1.85.0.post1"
    assert calls[1][0][1] == "2026.3.5.post1"
    assert calls[1][0][0].dependencies[0] == "boost-headers~=1.85.0"


def test_build_exits_with_status_one_on_builder_error(tmp_path: Path) -> None:
    mock_builder = MagicMock()
    mock_builder.build_rdkit.side_effect = ValueError("cmake configure failed")

    with (
        patch("rdkit_headers_pypi.cli.HeaderBuilder", return_value=mock_builder),
        pytest.raises(SystemExit) as exc_info,
    ):
        build("2026.3.5", cache=tmp_path / "cache", dist=tmp_path / "dist")

    assert exc_info.value.code == 1


def test_versions_prints_all_rdkit_releases_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("rdkit_headers_pypi.cli.list_rdkit_versions", return_value=["2024.9.6", "2025.9.3"]):
        versions()

    assert json.loads(capsys.readouterr().out) == ["2024.9.6", "2025.9.3"]


def test_main_registers_both_commands_and_runs_the_app() -> None:
    mock_app = MagicMock()
    with patch("rdkit_headers_pypi.cli.doctyper.DocTyper", return_value=mock_app):
        main()

    registered = [c.args[0] for c in mock_app.command.return_value.call_args_list]
    assert registered == [build, versions]
    mock_app.assert_called_once_with()


def test_versions_missing_only_excludes_releases_up_to_date_with_the_current_builder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch(
            "rdkit_headers_pypi.cli.list_rdkit_versions",
            return_value=["2024.9.6", "2025.9.3", "2026.3.5"],
        ),
        patch("rdkit_headers_pypi.cli.list_published_versions", return_value={"2024.9.6"}),
        patch("rdkit_headers_pypi.pypi.get_builder_version", return_value=BUILDER_VERSION),
    ):
        versions(missing_only=True)

    assert json.loads(capsys.readouterr().out) == ["2025.9.3", "2026.3.5"]


def test_versions_missing_only_includes_releases_with_a_stale_post_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 2024.9.6.post1 is already published under an older builder version, so it still
    # 2025.9.3 was never published
    # 2026.3.5 is up to date.
    def fake_get_builder_version(unused_package: str, version: str) -> str:
        return BUILDER_VERSION if version == "2026.3.5" else "0.0.1"

    with (
        patch(
            "rdkit_headers_pypi.cli.list_rdkit_versions",
            return_value=["2024.9.6", "2025.9.3", "2026.3.5"],
        ),
        patch(
            "rdkit_headers_pypi.cli.list_published_versions",
            return_value={"2024.9.6", "2024.9.6.post1", "2026.3.5"},
        ),
        patch("rdkit_headers_pypi.pypi.get_builder_version", side_effect=fake_get_builder_version),
    ):
        versions(missing_only=True)

    assert json.loads(capsys.readouterr().out) == ["2024.9.6", "2025.9.3"]
