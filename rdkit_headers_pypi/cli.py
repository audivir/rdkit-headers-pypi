"""Command implementations and entry point for the header builder CLI."""

from __future__ import annotations

import json
import logging
from pathlib import Path  # noqa: TC003

import doctyper

from rdkit_headers_pypi import __version__ as BUILDER_VERSION  # noqa: N812
from rdkit_headers_pypi.builder import (
    DEFAULT_CACHE_DIR,
    DEFAULT_DIST_DIR,
    HeaderBuilder,
    PackageSpec,
)
from rdkit_headers_pypi.pypi import (
    list_published_versions,
    list_rdkit_versions,
    needs_build,
    resolve_release_version,
)

logger = logging.getLogger(__name__)

BOOST_LICENSE = "BSL-1.0"
RDKIT_LICENSE = "BSD-3-Clause"


def build(
    rdkit_version: str,
    cache: Path = DEFAULT_CACHE_DIR,
    dist: Path = DEFAULT_DIST_DIR,
    clean: bool = False,
    verbose: bool = False,
) -> None:
    """Builds the boost-headers and rdkit-headers wheels for one RDKit release.

    Args:
        rdkit_version: RDKit release to build headers for, e.g. 2024.9.6.
        cache: Directory for downloaded sources and intermediate build artifacts.
        dist: Directory the built wheels and sdists are written to.
        clean: Whether to remove any cached RDKit source checkout before building.
        verbose: Whether to enable debug logging.

    Raises:
        SystemExit: If the build fails.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s: %(message)s"
    )

    try:
        builder = HeaderBuilder(cache, dist, clean)
        rdkit_inst, boost_ver, boost_inst = builder.build_rdkit(rdkit_version)

        boost_release = resolve_release_version("boost-headers", boost_ver, BUILDER_VERSION)
        builder.create_python_package(
            PackageSpec("boost-headers", "Boost headers.", BOOST_LICENSE),
            boost_release,
            boost_inst,
            BUILDER_VERSION,
        )

        rdkit_release = resolve_release_version("rdkit-headers", rdkit_version, BUILDER_VERSION)
        builder.create_python_package(
            PackageSpec(
                "rdkit-headers",
                "RDKit headers.",
                RDKIT_LICENSE,
                [f"boost-headers~={boost_ver}", f"rdkit=={rdkit_version}", "numpy<2.0"],
            ),
            rdkit_release,
            rdkit_inst,
            BUILDER_VERSION,
        )
    except ValueError as e:
        logger.exception("Build failed for RDKit %s", rdkit_version)
        raise SystemExit(1) from e

    logger.info("Build complete, wheels available in %s", dist)


def versions(missing_only: bool = False) -> None:
    """Prints every RDKit release on PyPI with a published wheel, as a JSON array.

    Args:
        missing_only: Whether to exclude RDKit versions whose rdkit-headers is already
            published under the current builder version.
    """
    rdkit_versions = list_rdkit_versions()
    if missing_only:
        published = list_published_versions("rdkit-headers")
        rdkit_versions = [
            v for v in rdkit_versions if needs_build("rdkit-headers", v, published, BUILDER_VERSION)
        ]
    print(json.dumps(rdkit_versions))  # noqa: T201


def main() -> None:
    """Runs the rdkit-headers-pypi CLI."""
    app = doctyper.DocTyper()
    app.command()(build)
    app.command()(versions)
    app()
