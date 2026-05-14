import datetime
import re
from pathlib import Path

from setuptools import find_namespace_packages, setup


BASE_DIR = Path(__file__).resolve().parent
DIST_NAME = "cells-log"
MODULE_ROOT = "cells"
VERSION_FILE = BASE_DIR / MODULE_ROOT / "log" / "version.py"
README_FILE = BASE_DIR / "README.md"


def build_version() -> str:
    """Generate a timestamp-based version for fallback."""
    return datetime.datetime.now().strftime("%Y.%m.%d.%H%M%S")


def read_version(version_file: Path) -> str:
    """Read __VERSION__ from the version module without importing the package."""
    content = version_file.read_text(encoding="utf-8")
    match = re.search(r'^__VERSION__\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if match is None:
        raise ValueError(f"Unable to find __VERSION__ in {version_file}")
    return match.group(1)


def read_readme(readme_file: Path) -> str:
    """Read the project README for package metadata."""
    return readme_file.read_text(encoding="utf-8")


try:
    version = read_version(VERSION_FILE)
except Exception:
    version = build_version()

try:
    long_description = read_readme(README_FILE)
except Exception:
    long_description = "Orjson-first JSON serialization utilities for the cells namespace."

setup(
    name=DIST_NAME,
    version=version,
    license="MPL-2.0",
    author="HarmonSir",
    author_email="git@pylab.me",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_namespace_packages(include=[f"{MODULE_ROOT}.*"]),
    include_package_data=True,
    zip_safe=False,
    python_requires=">=3.9",
    install_requires=[
    ],
)
