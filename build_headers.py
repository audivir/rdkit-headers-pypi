"""Header Package Builder for RDKit and Boost.

Automates the compilation and packaging of C++ headers into Python wheels.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import build.util
import jinja2
import requests

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _typeshed import StrPath

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(".cache")
DEFAULT_DIST_DIR = Path("dist")
TEMPLATE_DIR = Path("templates")

BOOST_URL_TEMPLATE = "https://archives.boost.io/release/{version}/source/boost_{version_underscore}.tar.gz"
RDKIT_URL_TEMPLATE = "https://github.com/rdkit/rdkit/archive/refs/tags/Release_{version_underscore}.tar.gz"


class BuilderError(Exception):
    """Base class for builder exceptions."""


class DependencyError(BuilderError):
    """System dependency (e.g., cmake) is missing."""


class BuildFailureError(BuilderError):
    """Compilation or installation step failed."""


class HeaderBuilder:
    """Builds headers for RDKit and Boost."""

    def __init__(
        self,
        cache_dir: StrPath | None = DEFAULT_CACHE_DIR,
        dist_dir: StrPath | None = DEFAULT_DIST_DIR,
    ) -> None:
        """Initialize builder."""
        self.cache_dir = Path(cache_dir)
        self.dist_dir = Path(dist_dir)
        self.cpu_count = os.cpu_count() or 1

        # setup templates
        loader = jinja2.FileSystemLoader(str(TEMPLATE_DIR))
        self.env = jinja2.Environment(loader=loader, autoescape=True)

        # validate environment
        for tool in ["cmake", "make", "rsync"]:
            if not shutil.which(tool):
                raise DependencyError(
                    f"Required system tool '{tool}' not found in PATH.",
                )

    def _safe_extract(self, tar_path: Path, extract_to: Path) -> None:
        """Safe extraction of tarballs to prevent path traversal."""
        logger.info("Extracting %s...", tar_path.name)
        with tarfile.open(tar_path) as tar:
            if sys.version_info >= (3, 12):
                tar.extractall(extract_to, filter="data")
            else:
                for member in tar.getmembers():
                    if member.name.startswith("../"):
                        raise BuildFailureError(
                            "Tarball contains path traversal attempt.",
                        )
                    tar.extract(member, extract_to)

    def download(self, url: str, output_name: str) -> Path:
        """Download a file from a URL to cache."""
        output_path = self.cache_dir / output_name
        if output_path.exists():
            logger.info("Using cached file: %s", output_name)
            return output_path

        logger.info("Downloading %s...", url)
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with output_path.open("wb") as f:
                shutil.copyfileobj(r.raw, f)
        return output_path

    def build_boost(self, version: str) -> Path:
        """Build Boost headers."""
        v_underscore = version.replace(".", "_")
        url = BOOST_URL_TEMPLATE.format(
            version=version,
            version_underscore=v_underscore,
        )
        tar_path = self.download(url, f"boost_{v_underscore}.tar.gz")

        extract_dir = self.cache_dir / f"boost_{v_underscore}"
        install_dir = self.cache_dir / f"boost_inst_{v_underscore}"

        if not (install_dir / "include").exists():
            self._safe_extract(tar_path, self.cache_dir)

            logger.info("Bootstrapping Boost...")
            subprocess.check_call(  # noqa: S603
                [
                    "./bootstrap.sh",
                    f"--prefix={install_dir.absolute()}",
                    f"--with-python={sys.executable}",
                    "--with-libraries=python,system,serialization,iostreams",
                ],
                cwd=extract_dir,
            )

            logger.info("Installing Boost Headers/Libs...")
            py_include = sysconfig.get_paths()["include"]
            subprocess.check_call(  # noqa: S603
                [
                    "./b2",
                    "install",
                    f"-j{self.cpu_count}",
                    f"include={py_include}",
                    "variant=release",
                ],
                cwd=extract_dir,
            )

        return install_dir

    def build_rdkit(self, version: str) -> tuple[Path, str]:
        """Build RDKit headers."""
        v_underscore = version.replace(".", "_")
        url = RDKIT_URL_TEMPLATE.format(version_underscore=v_underscore)
        tar_path = self.download(url, f"rdkit_{v_underscore}.tar.gz")

        extract_dir = self.cache_dir / f"rdkit-Release_{v_underscore}"
        install_dir = self.cache_dir / f"rdkit_inst_{v_underscore}"

        self._safe_extract(tar_path, self.cache_dir)

        # Detect Boost version from RDKit source
        cmakelists = extract_dir / "CMakeLists.txt"
        match = re.search(
            r'RDK_BOOST_VERSION "(\d+\.\d+\.\d+)"',
            cmakelists.read_text(),
        )
        if not match:
            raise BuildFailureError(
                "Could not detect required Boost version from RDKit source.",
            )
        boost_ver = match.group(1)

        boost_path = self.build_boost(boost_ver)

        build_dir = extract_dir / "build"
        build_dir.mkdir(exist_ok=True)

        cmake_args = [
            "cmake",
            "..",
            "-DRDK_INSTALL_INTREE=OFF",
            f"-DCMAKE_INSTALL_PREFIX={install_dir.absolute()}",
            f"-DBoost_ROOT={boost_path.absolute()}",
        ]

        if sys.platform == "darwin":
            libdir = sysconfig.get_config_var("LIBDIR")
            ldv = sysconfig.get_config_var("LDVERSION")
            cmake_args.append(
                f"-DCMAKE_SHARED_LINKER_FLAGS={libdir}/libpython{ldv}.dylib",
            )

        subprocess.check_call(cmake_args, cwd=build_dir)  # noqa: S603
        subprocess.check_call(["make", "install", f"-j{self.cpu_count}"], cwd=build_dir)  # noqa: S603,S607

        return install_dir, boost_ver

    def create_python_package(
        self,
        name: str,
        version: str,
        include_src: StrPath,
        deps: Sequence[str] | None = None,
    ) -> None:
        """Generates the wheel-ready directory structure."""
        pkg_root = Path(name)
        pkg_name = name.replace("-", "_")
        pkg_dir = pkg_root / pkg_name
        pkg_dir.mkdir(parents=True, exist_ok=True)

        # 1. Generate Metadata
        for template_name, target in [
            ("pyproject.j2", pkg_root / "pyproject.toml"),
            ("init.j2", pkg_dir / "__init__.py"),
        ]:
            template = self.env.get_template(template_name)
            content = template.render(
                name=name,
                version=version,
                dependencies=deps or [],
            )
            target.write_text(content)

        # 2. Sync Headers
        logger.info("Syncing headers for %s...", name)
        subprocess.check_call(  # noqa: S603
            [  # noqa: S607
                "rsync",
                "-av",
                "--delete",
                str(include_src / "include/"),
                str(pkg_dir / "include/"),
            ],
        )

        # 3. Build Wheel
        logger.info("Building wheel for %s...", name)
        builder = build.ProjectBuilder(pkg_root)
        builder.build("wheel", self.dist_dir)
        builder.build("sdist", self.dist_dir)


def main() -> None:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(description="Build RDKit & Boost header packages.")
    parser.add_argument(
        "rdkit_version",
        help="Version of RDKit to build (e.g. 2023.09.1)",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Cache directory",
    )
    parser.add_argument(
        "--dist",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Output directory",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    builder = HeaderBuilder(args.cache, args.dist)

    try:
        # Build RDKit (which triggers Boost build)
        rdkit_inst, boost_ver = builder.build_rdkit(args.rdkit_version)

        # Package Boost
        boost_inst = builder.cache_dir / f"boost_inst_{boost_ver.replace('.', '_')}"
        builder.create_python_package("boost-headers", boost_ver, boost_inst)

        # Package RDKit
        builder.create_python_package(
            "rdkit-headers",
            args.rdkit_version,
            rdkit_inst,
            deps=[f"boost-headers=={boost_ver}"],
        )

        print(f"Build Complete! Files available in: {args.dist}")  # noqa: T201

    except BuilderError:
        logger.exception("Build Failed")
        sys.exit(1)
    except Exception:
        logger.exception("Unexpected error")
        sys.exit(1)


if __name__ == "__main__":
    main()
