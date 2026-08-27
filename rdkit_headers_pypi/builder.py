"""Builds Boost and RDKit header packages from source."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import build
import jinja2
from packaging.version import Version

from rdkit_headers_pypi.pypi import download, download_wheel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _typeshed import StrPath

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(".cache")
DEFAULT_DIST_DIR = Path("dist")
TEMPLATE_DIR = Path(__file__).parent / "templates"

BOOST_URL_TEMPLATE = (
    "https://archives.boost.io/release/{version}/source/boost_{version_underscore}.tar.gz"
)
RDKIT_URL_TEMPLATE = (
    "https://github.com/rdkit/rdkit/archive/refs/tags/Release_{version_underscore}.tar.gz"
)
REQUIRED_TOOLS = ("cmake", "rsync")
# matches file copies to "include/..." destinations
CMAKE_INSTALL_HEADER_RE = re.compile(
    r'file\(INSTALL DESTINATION "\$\{CMAKE_INSTALL_PREFIX\}(/include[^"]*)" '
    r'TYPE (FILE|DIRECTORY) FILES\s*((?:"[^"]*"\s*)+)\)'
)
QUOTED_PATH_RE = re.compile(r'"([^"]*)"')
# necessary libraries for cmake configure of RDKit
BOOST_LIBRARIES = ("system", "serialization", "iostreams")


def safe_extract(tar_path: Path, extract_to: Path) -> None:
    """Extracts tar_path into extract_to, rejecting members that resolve outside it."""
    logger.info("Extracting %s...", tar_path.name)
    with tarfile.open(tar_path) as tar:
        root = extract_to.resolve()
        for member in tar.getmembers():
            target = (extract_to / member.name).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Tarball contains path traversal attempt.")
            tar.extract(member, extract_to)


def install_headers(build_dir: Path, install_dir: Path) -> None:
    """Replays every header `install(...)` rule from `cmake_install.cmake` in `build_dir`."""
    for script in build_dir.rglob("cmake_install.cmake"):
        content = script.read_text()
        for dest_suffix, install_type, files_blob in CMAKE_INSTALL_HEADER_RE.findall(content):
            dest_dir = install_dir / dest_suffix.lstrip("/")
            dest_dir.mkdir(parents=True, exist_ok=True)
            for src in QUOTED_PATH_RE.findall(files_blob):
                src_path = Path(src)
                if not src_path.exists():
                    continue
                if install_type == "FILE":
                    shutil.copy2(src_path, dest_dir / src_path.name)
                else:
                    shutil.copytree(src_path, dest_dir / src_path.name, dirs_exist_ok=True)


def extract_boost_version(rdkit_wheel: Path) -> str | None:
    """Extracts the Boost release version linked by a RDKit wheel from its bundled libraries."""
    with zipfile.ZipFile(rdkit_wheel) as whlzip:
        for name in whlzip.namelist():
            if name.startswith("rdkit.libs/libboost_"):
                if match := re.search(r"libboost_[\w-]+\.so\.(\d+\.\d+\.\d+)", name):
                    return match.group(1)
                return None
    return None


class PackageSpec(NamedTuple):
    """Stores the static metadata for one generated header package."""

    name: str
    description: str
    license_id: str
    dependencies: Sequence[str] = ()


class HeaderBuilder:
    """Manages the boost-headers and rdkit-headers build and packaging pipeline."""

    def __init__(
        self,
        cache_dir: StrPath = DEFAULT_CACHE_DIR,
        dist_dir: StrPath = DEFAULT_DIST_DIR,
        clean_cache: bool = False,
    ) -> None:
        """Initializes the builder, validating required system tools are on PATH.

        Raises:
            ValueError: If cmake or rsync is missing from PATH.
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.dist_dir = Path(dist_dir)
        self.dist_dir.mkdir(parents=True, exist_ok=True)
        self.clean_cache = clean_cache
        self.cpu_count = os.cpu_count() or 1

        loader = jinja2.FileSystemLoader(str(TEMPLATE_DIR))
        self.env = jinja2.Environment(loader=loader, autoescape=False)  # noqa: S701

        for tool in REQUIRED_TOOLS:
            if not shutil.which(tool):
                raise ValueError(f"Required system tool '{tool}' not found in PATH.")

    def build_boost(self, version: str) -> Path:
        """Compiles Boost from source and returns the directory containing its headers."""
        v_underscore = version.replace(".", "_")
        url = BOOST_URL_TEMPLATE.format(version=version, version_underscore=v_underscore)
        tar_path = download(url, self.cache_dir / f"boost_{v_underscore}.tar.gz")

        extract_dir = self.cache_dir / f"boost_{v_underscore}"
        install_dir = self.cache_dir / f"boost_inst_{v_underscore}"

        if (install_dir / "include" / "boost").exists():
            logger.info("Boost %s already built, skipping", version)
            return install_dir

        if not extract_dir.exists():
            safe_extract(tar_path, self.cache_dir)

        logger.info("Bootstrapping Boost build engine...")
        subprocess.check_call(["./bootstrap.sh"], cwd=extract_dir)

        logger.info("Compiling Boost libraries required by cmake configure of RDKit...")
        with_flags = [f"--with-{name}" for name in BOOST_LIBRARIES]
        subprocess.check_call(  # noqa: S603
            [
                "./b2",
                "install",
                f"-j{self.cpu_count}",
                f"--prefix={install_dir.absolute()}",
                *with_flags,
                "variant=release",
            ],
            cwd=extract_dir,
        )

        return install_dir

    def build_rdkit(self, version: str) -> tuple[Path, str, Path]:
        """Configures RDKit's headers from source.

        Runs cmake configure only (RDK_BUILD_PYTHON_WRAPPERS=OFF), without compiling RDKit
        itself, since only the headers (source and cmake-generated) are needed.

        Returns:
            A tuple with the RDKit install dir, the linked Boost version,
            and the Boost install dir.
        """
        # Boost version is only embedded in the .so filename of manylinux wheels
        rdkit_wheel = download_wheel("rdkit", version, self.cache_dir, platform="linux")
        if not rdkit_wheel:
            raise ValueError(f"RDKit wheel for version {version} not found")
        boost_ver = extract_boost_version(rdkit_wheel)
        if not boost_ver:
            raise ValueError("Boost version could not be extracted")

        # RDKit's GitHub tags zero-pad the month: 2026.3.5 -> Release_2026_03_5.
        rdkit_ver = Version(version)
        v_underscore = f"{rdkit_ver.major}_{rdkit_ver.minor:02d}_{rdkit_ver.micro}"
        url = RDKIT_URL_TEMPLATE.format(version_underscore=v_underscore)
        tar_path = download(url, self.cache_dir / f"rdkit_{v_underscore}.tar.gz")

        extract_dir = self.cache_dir / f"rdkit-Release_{v_underscore}"
        install_dir = self.cache_dir / f"rdkit_inst_{v_underscore}"

        boost_path = self.build_boost(boost_ver)

        if install_dir.exists():
            logger.info("RDKit %s headers already configured, skipping", version)
            return install_dir, boost_ver, boost_path

        if self.clean_cache and extract_dir.exists():
            logger.info("Cleaning build cache.")
            shutil.rmtree(extract_dir)

        if not extract_dir.exists():
            safe_extract(tar_path, self.cache_dir)

        build_dir = extract_dir / "build"
        build_dir.mkdir(exist_ok=True)

        cmake_args = [
            "cmake",
            "..",
            "-DRDK_BUILD_PYTHON_WRAPPERS=OFF",
            "-DRDK_INSTALL_INTREE=OFF",
            "-DRDK_BUILD_FREETYPE_SUPPORT=OFF",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ]
        # Boost_ROOT must be passed via environment, not -D: the legacy FindBoost module only
        # accepts BOOST_ROOT, while newer RDKit's find_package(Boost ... CONFIG) needs Boost_ROOT.
        cmake_env = {**os.environ, "BOOST_ROOT": str(boost_path.absolute())}
        cmake_env["Boost_ROOT"] = cmake_env["BOOST_ROOT"]
        subprocess.check_call(cmake_args, cwd=build_dir, env=cmake_env)  # noqa: S603

        install_headers(build_dir, install_dir)

        return install_dir, boost_ver, boost_path

    def create_python_package(
        self, spec: PackageSpec, version: str, include_src: StrPath, builder_version: str
    ) -> None:
        """Generates the wheel-ready directory structure and builds the wheel and sdist."""
        pkg_root = Path(spec.name)
        pkg_name = spec.name.replace("-", "_")
        pkg_dir = pkg_root / pkg_name
        pkg_dir.mkdir(parents=True, exist_ok=True)

        for template_name, target in [
            ("pyproject.j2", pkg_root / "pyproject.toml"),
            ("init.j2", pkg_dir / "__init__.py"),
        ]:
            template = self.env.get_template(template_name)
            content = template.render(
                name=spec.name,
                version=version,
                description=spec.description,
                license=spec.license_id,
                dependencies=spec.dependencies,
                builder_version=builder_version,
            )
            target.write_text(content)

        logger.info("Syncing headers for %s...", spec.name)
        rsync_args = [
            "rsync",
            "-av",
            "--delete",
            str(Path(include_src) / "include"),
            str(pkg_dir),
        ]
        subprocess.check_call(rsync_args)  # noqa: S603

        # removes stale build directories
        shutil.rmtree(pkg_root / "build", ignore_errors=True)
        for egg_info in pkg_root.glob("*.egg-info"):
            shutil.rmtree(egg_info)

        logger.info("Building wheel for %s...", spec.name)
        builder = build.ProjectBuilder(pkg_root)
        builder.build("wheel", str(self.dist_dir))
        builder.build("sdist", str(self.dist_dir))
