# rdkit-headers-pypi

Builds Boost and RDKit C++ headers from source and packages them as installable
Python wheels, for every RDKit release published on PyPI.

## Prerequisites

- Python 3.10 to 3.14.
- `cmake` and `rsync`.
- A C++ toolchain (only used to compile the Boost libraries RDKit's cmake configure step
  validates; RDKit itself is never compiled).

## Installation

The `boost-headers` and `rdkit-headers` wheels this tool produces are published to PyPI, but
the tool itself is not; install it directly from this repository:

```bash
uv tool install git+https://github.com/audivir/rdkit-headers-pypi
```

## Usage

Build the `boost-headers` and `rdkit-headers` wheels for one RDKit release:

```bash
rdkit-headers-pypi build 2026.3.5
python -m pip install dist/boost_headers-*.whl dist/rdkit_headers-*.whl
```

List every RDKit release on PyPI with a published wheel:

```bash
rdkit-headers-pypi versions
```

Use `--missing-only` to list only releases `rdkit-headers` has not published yet.

Once installed, `boost-headers` and `rdkit-headers` each expose their include directory:

```python
from boost_headers import get_include as boost_include
from rdkit_headers import get_include as rdkit_include
```

## Acknowledgments

`boost-headers` and `rdkit-headers` repackage, unmodified, header files from the
[Boost](https://www.boost.org) and [RDKit](https://www.rdkit.org) projects. See
`boost-headers/NOTICE` and `rdkit-headers/NOTICE`.

## License

`rdkit-headers-pypi`: MIT, see `LICENSE`.
`boost-headers`: [Boost Software License 1.0](boost-headers/LICENSE).
`rdkit-headers`: [BSD 3-Clause License](rdkit-headers/LICENSE).
