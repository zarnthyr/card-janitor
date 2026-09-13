# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath

ADDON_NAME = "card-retirement"
OUTPUT_FILE = Path(f"{ADDON_NAME}.ankiaddon")
SRC_DIR = Path("src")
BUILD_DIR = Path("build")
EXPECTED_MANIFEST = {"name": "Card Retirement", "package": "card_retirement"}
REQUIRED_PACKAGE_FILES = {
    "LICENSE",
    "README.md",
    "__init__.py",
    "actions.py",
    "addon.py",
    "config.json",
    "config.md",
    "config.schema.json",
    "configuration.py",
    "engine.py",
    "evaluator.py",
    "manifest.json",
    "models.py",
    "ui.py",
}
FORBIDDEN_NAMES = {"package.py"}
FORBIDDEN_PARTS = {"__pycache__"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}


def is_forbidden_package_path(path: str | Path) -> bool:
    candidate = PurePosixPath(str(path).replace("\\", "/"))
    return (
        candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.name in FORBIDDEN_NAMES
        or any(part in FORBIDDEN_PARTS for part in candidate.parts)
        or candidate.suffix in FORBIDDEN_SUFFIXES
        or any(".egg-info" in part for part in candidate.parts)
        or candidate.as_posix().startswith("build/")
    )


def clean() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    OUTPUT_FILE.unlink(missing_ok=True)


def prepare_build() -> None:
    BUILD_DIR.mkdir(parents=True)
    for path in SRC_DIR.iterdir():
        if path.is_file() and path.name != "package.py":
            shutil.copy2(path, BUILD_DIR / path.name)
    for name in ("manifest.json", "README.md", "LICENSE"):
        shutil.copy2(name, BUILD_DIR / name)
    shutil.copy2("docs/config.md", BUILD_DIR / "config.md")


def build_files() -> list[Path]:
    return sorted(path for path in BUILD_DIR.rglob("*") if path.is_file())


def create_archive() -> None:
    with zipfile.ZipFile(OUTPUT_FILE, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in build_files():
            archive.write(path, path.relative_to(BUILD_DIR))


def validate_package(path: Path = OUTPUT_FILE) -> None:
    if not path.exists():
        raise SystemExit(f"{path} was not created")
    with zipfile.ZipFile(path) as archive:
        archive_names = archive.namelist()
        names = set(archive_names)
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            manifest = None
    missing = REQUIRED_PACKAGE_FILES - names
    forbidden = {name for name in names if is_forbidden_package_path(name)}
    duplicates = {name for name in names if archive_names.count(name) > 1}
    if missing or forbidden or duplicates or manifest != EXPECTED_MANIFEST:
        print(f"missing={sorted(missing)}")
        print(f"forbidden={sorted(forbidden)}")
        print(f"duplicates={sorted(duplicates)}")
        print(f"manifest={manifest!r}")
        raise SystemExit(1)


def main() -> None:
    clean()
    prepare_build()
    create_archive()
    validate_package()
    print(f"Created {OUTPUT_FILE} with {len(build_files())} files")


if __name__ == "__main__":
    main()
