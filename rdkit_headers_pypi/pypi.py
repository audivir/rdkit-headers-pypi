"""PyPI lookups: package downloads and RDKit release discovery."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from packaging.version import Version
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if TYPE_CHECKING:
    from _typeshed import StrPath

logger = logging.getLogger(__name__)

RDKIT_PYPI_URL = "https://pypi.org/pypi/rdkit/json"
PYPI_RELEASE_URL_TEMPLATE = "https://pypi.org/pypi/{package}/{version}/json"
PYPI_PROJECT_URL_TEMPLATE = "https://pypi.org/pypi/{package}/json"

# PyPI's CDN intermittently answers 503 "Backend is unhealthy", retry transient errors
_session = requests.Session()
_session.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
    ),
)


def download_wheel(
    package: str, version: str, cache_dir: StrPath, platform: str = ""
) -> Path | None:
    """Downloads one PyPI wheel into cache_dir and returns its path or None if unavailable.

    Args:
        package: PyPI project name.
        version: Exact release version.
        cache_dir: Directory the wheel is downloaded into.
        platform: Substring the wheel filename platform tag must contain, e.g. "linux" to
            prefer a manylinux wheel regardless of host OS. Any wheel matches if empty.
    """
    response = _session.get(
        PYPI_RELEASE_URL_TEMPLATE.format(package=package, version=version), timeout=30
    )
    response.raise_for_status()
    files: list[dict[str, str]] = response.json()["urls"]
    wheel = next(
        (f for f in files if f["packagetype"] == "bdist_wheel" and platform in f["filename"]),
        None,
    )
    if wheel is None:
        return None
    return download(wheel["url"], Path(cache_dir) / wheel["filename"])


def download(url: str, output_path: Path) -> Path:
    """Downloads a file from url to output_path, reusing an existing cached copy."""
    if output_path.exists():
        logger.info("Using cached file: %s", output_path.name)
        return output_path

    logger.info("Downloading %s...", url)
    with _session.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with output_path.open("wb") as f:
            shutil.copyfileobj(response.raw, f)
    return output_path


def list_rdkit_versions() -> list[str]:
    """Returns every RDKit release on PyPI that has at least one published wheel."""
    response = _session.get(RDKIT_PYPI_URL, timeout=30)
    response.raise_for_status()
    releases: dict[str, list[dict[str, str]]] = response.json()["releases"]
    versions = [
        version
        for version, files in releases.items()
        if any(f["packagetype"] == "bdist_wheel" for f in files)
    ]
    return sorted(versions, key=Version)


def list_published_versions(package: str) -> set[str]:
    """Returns every version of package already published on PyPI, empty if never published."""
    response = _session.get(PYPI_PROJECT_URL_TEMPLATE.format(package=package), timeout=30)
    if response.status_code == requests.codes.not_found:
        return set()
    response.raise_for_status()
    return set(response.json()["releases"])


def get_builder_version(package: str, version: str) -> str | None:
    """Returns the __builder_version__ the published package was built with, or None if unset."""
    with tempfile.TemporaryDirectory() as tmp:
        wheel_path = download_wheel(package, version, tmp)
        if not wheel_path:
            return None
        pkg_name = package.replace("-", "_")
        with zipfile.ZipFile(wheel_path) as whl:
            try:
                content = whl.read(f"{pkg_name}/__init__.py").decode()
            except KeyError:
                return None
    match = re.search(r'__builder_version__ = "([^"]*)"', content)
    return match.group(1) if match else None


def matching_post_numbers(version: str, published: set[str]) -> list[int]:
    """Returns the .postN suffixes already published for version, sorted ascending."""
    post_pattern = re.compile(rf"{re.escape(version)}\.post(\d+)")
    posts = [
        int(match.group(1))
        for candidate in published
        if (match := post_pattern.fullmatch(candidate))
    ]
    return sorted(posts)


def latest_published_generation(version: str, published: set[str]) -> str | None:
    """Returns the highest already-published generation (bare or .postN) of version, if any."""
    posts = matching_post_numbers(version, published)
    if posts:
        return f"{version}.post{posts[-1]}"
    return version if version in published else None


def resolve_release_version(package: str, version: str, builder_version: str) -> str:
    """Determines the exact version string to publish package==version as.

    Checks the builder_version of the latest already-published generation (bare or .postN),
    not just the bare version, so a version already re-released under the current builder
    is not bumped on reruns.
    """
    published = list_published_versions(package)
    latest = latest_published_generation(version, published)
    if latest is None:
        return version
    if get_builder_version(package, latest) == builder_version:
        return latest

    posts = matching_post_numbers(version, published)
    return f"{version}.post{(posts[-1] if posts else 0) + 1}"


def needs_build(package: str, version: str, published: set[str], builder_version: str) -> bool:
    """Whether version has no published generation of package yet, or its latest is stale."""
    latest = latest_published_generation(version, published)
    return latest is None or get_builder_version(package, latest) != builder_version
