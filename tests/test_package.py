# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
import zipfile
from pathlib import Path

import pytest

from package import (
    DEV_ADDON_NAME,
    EXPECTED_MANIFEST,
    REQUIRED_PACKAGE_FILES,
    install_development_addon,
    is_forbidden_package_path,
    uninstall_development_addon,
    validate_package,
)


def write_archive(path: Path, names: set[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            contents = json.dumps(EXPECTED_MANIFEST) if name == "manifest.json" else ""
            archive.writestr(name, contents)


def test_valid_archive(tmp_path: Path) -> None:
    archive = tmp_path / "addon.ankiaddon"
    write_archive(archive, set(REQUIRED_PACKAGE_FILES))
    validate_package(archive)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "addon.ankiaddon"
    write_archive(archive, set(REQUIRED_PACKAGE_FILES) - {"ui.py"})
    with pytest.raises(SystemExit):
        validate_package(archive)


@pytest.mark.parametrize("name", ["../bad.py", "/bad.py", "__pycache__/bad.pyc", "package.py"])
def test_forbidden_paths(name: str) -> None:
    assert is_forbidden_package_path(name)


def test_development_install_preserves_config(tmp_path: Path) -> None:
    addons_dir = tmp_path / "addons21"
    destination = install_development_addon(addons_dir)

    assert destination == addons_dir / DEV_ADDON_NAME
    assert (destination / "addon.py").is_symlink()
    assert (destination / "browsing.py").is_symlink()
    assert (destination / "note_type_picker.py").is_symlink()
    assert (destination / "manifest.json").is_symlink()
    assert not (destination / "config.json").is_symlink()

    custom_config = '{"custom": true}\n'
    (destination / "config.json").write_text(custom_config, encoding="utf-8")
    install_development_addon(addons_dir)
    assert (destination / "config.json").read_text(encoding="utf-8") == custom_config

    uninstall_development_addon(addons_dir)
    assert not destination.exists()
