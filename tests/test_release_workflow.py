# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


@pytest.mark.parametrize("state", ["missing", "draft", "published", "upload_failure"])
def test_release_publication_resumes_only_drafts(state: str) -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    block = workflow.split("      - name: Publish GitHub release\n        run: |\n", 1)[1]
    script = textwrap.dedent(block.split("        env:\n", 1)[0])
    mock = """
gh() {
  echo "$*" >&2
  case "$2" in
    view)
      case "$TEST_RELEASE_STATE" in
        missing) return 1 ;;
        published) echo false ;;
        *) echo true ;;
      esac ;;
    upload)
      if [[ "$TEST_RELEASE_STATE" == "upload_failure" ]]; then return 1; fi ;;
  esac
}
"""
    bash = shutil.which("bash")
    assert bash is not None
    # Execute the repository workflow with gh replaced by a local shell function.
    result = subprocess.run(  # noqa: S603
        [bash, "-e", "-c", mock + script],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "RELEASE_TAG": "v0.2.1", "TEST_RELEASE_STATE": state},
    )
    calls = result.stderr.splitlines()
    assert calls[0].startswith("release view v0.2.1 --json isDraft")
    if state == "published":
        assert len(calls) == 1
        assert "leaving its assets untouched" in result.stdout
    else:
        if state == "missing":
            assert "--draft --verify-tag" in calls[1]
        assert "release upload v0.2.1 card-janitor.ankiaddon --clobber" in calls
        if state == "upload_failure":
            assert result.returncode != 0
            assert not any("release edit" in call for call in calls)
            return
        assert calls[-1] == "release edit v0.2.1 --draft=false"
    assert result.returncode == 0
