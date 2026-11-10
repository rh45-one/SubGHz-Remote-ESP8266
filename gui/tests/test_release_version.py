import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_version", ROOT / "scripts/release_version.py")
release_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_version)

SHA_A = "1" * 40
SHA_B = "2" * 40
SHA_C = "3" * 40


def test_parse_and_bump_ui_tags():
    assert release_version.parse_ui_tag("ui-v0.1.0") == (0, 1, 0)
    assert release_version.parse_ui_tag("ui-v12.34.56") == (12, 34, 56)
    for invalid in ("v0.1.0", "ui-v01.1.0", "ui-v0.1", "ui-v0.1.0-rc1", ""):
        assert release_version.parse_ui_tag(invalid) is None

    assert release_version.bump_version(None, "patch") == (0, 1, 0)
    assert release_version.bump_version(None, "minor") == (0, 1, 0)
    assert release_version.bump_version(None, "major") == (1, 0, 0)
    assert release_version.bump_version((0, 1, 0), "patch") == (0, 1, 1)
    assert release_version.bump_version((0, 1, 4), "minor") == (0, 2, 0)
    assert release_version.bump_version((0, 2, 3), "major") == (1, 0, 0)


def test_gui_path_detection():
    assert release_version.is_gui_path("gui/app/main.py")
    assert release_version.is_gui_path("gui/app/static/app.mjs")
    assert release_version.is_gui_path("gui/Dockerfile")
    assert release_version.is_gui_path("gui/docker-compose.yml")
    assert release_version.is_gui_path("gui/docker-compose.dev.yml")
    assert release_version.is_gui_path("gui/.env.example")
    assert release_version.is_gui_path(".github/workflows/release-gui.yml")
    assert not release_version.is_gui_path("firmware/src/main.cpp")
    assert not release_version.is_gui_path("README.md")
    assert not release_version.is_gui_path("gui/tests/test_api.py")


def test_initial_and_incremental_release_planning():
    initial = release_version.plan_release(
        target_sha=SHA_A,
        main_head_sha=SHA_A,
        is_target_on_main=True,
        tag_commits={},
        changed_paths=None,
    )
    assert initial["should_release"] is True
    assert initial["is_rerun"] is False
    assert initial["tag"] == "ui-v0.1.0"
    assert initial["version"] == "0.1.0"
    assert initial["sha_tag"] == f"sha-{SHA_A[:12]}"
    assert initial["promote_latest"] is True

    patch_next = release_version.plan_release(
        target_sha=SHA_B,
        main_head_sha=SHA_B,
        is_target_on_main=True,
        tag_commits={"ui-v0.1.0": SHA_A},
        changed_paths=["gui/app/static/style.css"],
    )
    assert patch_next["should_release"] is True
    assert patch_next["tag"] == "ui-v0.1.1"
    assert patch_next["previous_tag"] == "ui-v0.1.0"
    assert patch_next["promote_latest"] is True

    minor_next = release_version.plan_release(
        target_sha=SHA_B,
        main_head_sha=SHA_B,
        is_target_on_main=True,
        tag_commits={"ui-v0.1.0": SHA_A},
        changed_paths=["gui/Dockerfile"],
        bump="minor",
    )
    assert minor_next["tag"] == "ui-v0.2.0"


def test_skips_non_gui_changes_unless_forced():
    skipped = release_version.plan_release(
        target_sha=SHA_B,
        main_head_sha=SHA_B,
        is_target_on_main=True,
        tag_commits={"ui-v0.1.0": SHA_A},
        changed_paths=["firmware/src/main.cpp", "README.md"],
    )
    assert skipped["should_release"] is False
    assert "No GUI or deployment changes" in skipped["reason"]

    forced = release_version.plan_release(
        target_sha=SHA_B,
        main_head_sha=SHA_B,
        is_target_on_main=True,
        tag_commits={"ui-v0.1.0": SHA_A},
        changed_paths=["README.md"],
        force=True,
    )
    assert forced["should_release"] is True
    assert forced["tag"] == "ui-v0.1.1"


def test_guards_against_off_main_stale_and_non_ancestor_commits():
    off_main = release_version.plan_release(
        target_sha=SHA_B,
        main_head_sha=SHA_A,
        is_target_on_main=False,
        tag_commits={"ui-v0.1.0": SHA_A},
        changed_paths=["gui/app/main.py"],
    )
    assert off_main["should_release"] is False

    stale = release_version.plan_release(
        target_sha=SHA_B,
        main_head_sha=SHA_C,
        is_target_on_main=True,
        tag_commits={"ui-v0.1.0": SHA_A},
        changed_paths=["gui/app/main.py"],
    )
    assert stale["should_release"] is False
    assert "Stale commit" in stale["reason"]

    non_ancestor = release_version.plan_release(
        target_sha=SHA_B,
        main_head_sha=SHA_B,
        is_target_on_main=True,
        tag_commits={"ui-v0.1.0": SHA_A},
        changed_paths=["gui/app/main.py"],
        is_latest_tag_ancestor_of_target=False,
    )
    assert non_ancestor["should_release"] is False
    assert "not an ancestor" in non_ancestor["reason"]


def test_rerun_preserves_existing_tag_and_never_regresses_latest():
    older_rerun = release_version.plan_release(
        target_sha=SHA_A,
        main_head_sha=SHA_B,
        is_target_on_main=True,
        tag_commits={"ui-v0.1.0": SHA_A, "ui-v0.1.1": SHA_B},
        changed_paths=[],
    )
    assert older_rerun["should_release"] is True
    assert older_rerun["is_rerun"] is True
    assert older_rerun["tag"] == "ui-v0.1.0"
    assert older_rerun["promote_latest"] is False

    head_rerun = release_version.plan_release(
        target_sha=SHA_B,
        main_head_sha=SHA_B,
        is_target_on_main=True,
        tag_commits={"ui-v0.1.0": SHA_A, "ui-v0.1.1": SHA_B},
        changed_paths=[],
    )
    assert head_rerun["should_release"] is True
    assert head_rerun["is_rerun"] is True
    assert head_rerun["tag"] == "ui-v0.1.1"
    assert head_rerun["previous_tag"] == "ui-v0.1.0"
    assert head_rerun["promote_latest"] is True


def test_release_notes_and_compose_workflow_invariants():
    digest = "sha256:" + ("a" * 64)
    notes = release_version.render_release_notes(
        tag="ui-v0.1.0",
        version="0.1.0",
        image_repo="ghcr.io/rh45-one/subghz-remote-esp8266-gui",
        digest=digest,
        target_sha=SHA_A,
        previous_tag="",
        promote_latest=True,
    )
    assert "ghcr.io/rh45-one/subghz-remote-esp8266-gui:ui-v0.1.0" in notes
    assert "ghcr.io/rh45-one/subghz-remote-esp8266-gui:latest" in notes
    assert f"ghcr.io/rh45-one/subghz-remote-esp8266-gui@{digest}" in notes
    assert "GUI_IMAGE_TAG=ui-v0.1.0" in notes

    with pytest.raises(ValueError):
        release_version.render_release_notes(
            tag="ui-v0.1.0",
            version="0.1.0",
            image_repo="ghcr.io/rh45-one/subghz-remote-esp8266-gui",
            digest="invalid-digest",
            target_sha=SHA_A,
        )

    compose = (ROOT / "gui/docker-compose.yml").read_text(encoding="utf-8")
    assert "${GUI_IMAGE:-ghcr.io/rh45-one/subghz-remote-esp8266-gui}:${GUI_IMAGE_TAG:-latest}" in compose
    assert "127.0.0.1:8000:8000" in compose
    assert "com.centurylinklabs.watchtower.enable" in compose
    assert "containrrr/watchtower" not in compose

    dev_compose = (ROOT / "gui/docker-compose.dev.yml").read_text(encoding="utf-8")
    assert "build:" in dev_compose

    workflow = (ROOT / ".github/workflows/release-gui.yml").read_text(encoding="utf-8")
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert "cancel-in-progress: false" in workflow
    assert workflow.index("gh release create") < workflow.index("docker buildx imagetools create")

    git_plan = release_version.plan_from_git(ROOT, target_sha="HEAD", main_ref="HEAD")
    assert git_plan["should_release"] is True
    assert git_plan["tag"].startswith("ui-v")

