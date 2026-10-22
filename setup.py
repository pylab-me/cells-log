"""Setuptools configuration for cells-log."""

import re
from pathlib import Path

from setuptools import find_namespace_packages, setup


BASE_DIR = Path(__file__).resolve().parent
README_FILE = BASE_DIR / "README.md"
VERSION_FILE = BASE_DIR / "cells" / "log" / "version.py"
VERSION_PATTERN = re.compile(
    r'^__VERSION__\s*=\s*["\'](?P<version>[^"\']+)["\']',
    re.MULTILINE,
)


def read_version(version_file: Path) -> str:
    """Read the package version without importing application code.

    Args:
        version_file: Python module containing ``__VERSION__``.

    Returns:
        Package version declared by the module.

    Raises:
        ValueError: If the version declaration is missing.
    """
    content = version_file.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(content)
    if match is None:
        raise ValueError(f"Unable to find __VERSION__ in {version_file}")
    return match.group("version")


setup(
    name="cells-log",
    version=read_version(VERSION_FILE),
    description=(
        "Structured, bootstrap-safe logging kernel for Python services and CLI applications."
    ),
    long_description=README_FILE.read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    url="https://github.com/pylab-me/cells-log",
    project_urls={
        "Documentation": "https://github.com/pylab-me/cells-log#readme",
        "Issues": "https://github.com/pylab-me/cells-log/issues",
        "Source": "https://github.com/pylab-me/cells-log",
    },
    license="MPL-2.0",
    author="PYLAB",
    author_email="git@pylab.me",
    packages=find_namespace_packages(include=["cells.*"]),
    include_package_data=True,
    zip_safe=False,
    python_requires=">=3.10",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: System :: Logging",
    ],
    keywords=["logging", "structured-logging", "json-logs", "observability"],
)
