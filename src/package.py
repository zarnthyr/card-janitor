# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath

ADDON_NAME = "card-janitor"
OUTPUT_FILE = Path(f"{ADDON_NAME}.ankiaddon")
SRC_DIR = Path("src")
BUILD_DIR = Path("build")
EXPECTED_MANIFEST = {"name": "Card Janitor", "package": "card_janitor"}
REQUIRED_PACKAGE_FILES = {
    "LICENSE",
    "README.md",
    "__init__.py",
    "actions.py",
    "addon.py",
    "automatic.py",
    "config.json",
    "config.md",
    "config.schema.json",
    "configuration.py",
    "dialogs.py",
    "engine.py",
    "evaluator.py",
    "execution.py",
    "log.py",
    "manifest.json",
    "models.py",
    "presentation.py",
    "ui.py",
}
FORBIDDEN_NAMES = {"package.py"}
FORBIDDEN_PARTS = {"__pycache__"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
DEV_ADDON_NAME = "card_janitor"
DEV_MARKER = ".card-janitor-development-install"


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


def default_anki_addons_dir() -> Path:
    override = os.environ.get("CARD_JANITOR_ANKI_ADDONS_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Anki2/addons21"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            message = "APPDATA is not set; set CARD_JANITOR_ANKI_ADDONS_DIR"
            raise RuntimeError(message)
        return Path(appdata) / "Anki2/addons21"
    return Path.home() / ".local/share/Anki2/addons21"


def development_link_sources() -> dict[str, Path]:
    project_dir = Path(__file__).resolve().parent.parent
    source_dir = project_dir / "src"
    links = {path.name: path for path in source_dir.glob("*.py") if path.name != "package.py"}
    links.update(
        {
            "config.schema.json": source_dir / "config.schema.json",
            "manifest.json": project_dir / "manifest.json",
            "README.md": project_dir / "README.md",
            "LICENSE": project_dir / "LICENSE",
            "config.md": project_dir / "docs/config.md",
        }
    )
    return links


def install_development_addon(addons_dir: Path | None = None) -> Path:
    destination = (addons_dir or default_anki_addons_dir()) / DEV_ADDON_NAME
    marker = destination / DEV_MARKER
    if destination.exists() and not marker.is_file():
        raise RuntimeError(
            f"Refusing to modify existing non-development addon directory: {destination}"
        )

    destination.mkdir(parents=True, exist_ok=True)
    marker.write_text("Managed by the Card Janitor development installer.\n", encoding="utf-8")

    for name, source in development_link_sources().items():
        target = destination / name
        if target.is_symlink():
            if target.resolve() == source.resolve():
                continue
            target.unlink()
        elif target.exists():
            raise RuntimeError(f"Refusing to replace non-symlink development file: {target}")
        target.symlink_to(source.resolve())

    config = destination / "config.json"
    if not config.exists():
        project_config = Path(__file__).resolve().parent / "config.json"
        shutil.copy2(project_config, config)

    print(f"Installed development addon at {destination}")
    return destination


def uninstall_development_addon(addons_dir: Path | None = None) -> None:
    destination = (addons_dir or default_anki_addons_dir()) / DEV_ADDON_NAME
    marker = destination / DEV_MARKER
    if not destination.exists():
        print(f"Development addon is not installed at {destination}")
        return
    if not marker.is_file():
        raise RuntimeError(
            f"Refusing to remove existing non-development addon directory: {destination}"
        )
    shutil.rmtree(destination)
    print(f"Removed development addon at {destination}")


def main() -> None:
    clean()
    prepare_build()
    create_archive()
    validate_package()
    print(f"Created {OUTPUT_FILE} with {len(build_files())} files")


if __name__ == "__main__":
    main()
