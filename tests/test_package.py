# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import ast
import json
import zipfile
from pathlib import Path

import pytest

import package
from package import (
    DEV_ADDON_NAME,
    EXPECTED_MANIFEST,
    REQUIRED_PACKAGE_FILES,
    development_link_sources,
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


def test_runtime_relative_imports_are_in_package_allowlist() -> None:
    source_dir = Path(__file__).resolve().parents[1] / "src"
    for name in REQUIRED_PACKAGE_FILES:
        if not name.endswith(".py"):
            continue
        tree = ast.parse((source_dir / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                assert node.module + ".py" in REQUIRED_PACKAGE_FILES, (name, node.module)


def test_bundled_readme_links_and_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_dir = tmp_path / "build"
    monkeypatch.setattr(package, "BUILD_DIR", build_dir)
    package.prepare_build()
    assert {path.name for path in build_dir.iterdir()} == REQUIRED_PACKAGE_FILES
    readme = (build_dir / "README.md").read_text(encoding="utf-8")
    assert "./docs/" not in readme
    assert "./assets/" not in readme
    assert "[policies.md](policies.md)" in readme


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "addon.ankiaddon"
    write_archive(archive, set(REQUIRED_PACKAGE_FILES) - {"ui.py"})
    with pytest.raises(SystemExit):
        validate_package(archive)


def test_unexpected_file_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "addon.ankiaddon"
    write_archive(archive, set(REQUIRED_PACKAGE_FILES) | {"scratch.py"})
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
    assert (destination / "picker_state.py").is_symlink()
    assert (destination / "policy_editor.py").is_symlink()
    assert (destination / "json_editor.py").is_symlink()
    assert not (destination / "dialogs.py").exists()
    assert (destination / "manifest.json").is_symlink()
    assert not (destination / "config.json").is_symlink()

    custom_config = '{"custom": true}\n'
    (destination / "config.json").write_text(custom_config, encoding="utf-8")
    install_development_addon(addons_dir)
    assert (destination / "config.json").read_text(encoding="utf-8") == custom_config

    uninstall_development_addon(addons_dir)
    assert not destination.exists()


def test_reinstall_removes_only_obsolete_source_links(tmp_path: Path) -> None:
    addons_dir = tmp_path / "addons21"
    destination = install_development_addon(addons_dir)
    source_dir = development_link_sources()["addon.py"].parent
    obsolete = destination / "obsolete.py"
    obsolete.symlink_to(source_dir / "obsolete.py")
    unrelated = destination / "personal-link"
    unrelated.symlink_to(tmp_path / "personal")
    personal = destination / "personal.txt"
    personal.write_text("keep", encoding="utf-8")
    install_development_addon(addons_dir)
    assert not obsolete.is_symlink()
    assert unrelated.is_symlink()
    assert personal.read_text(encoding="utf-8") == "keep"
