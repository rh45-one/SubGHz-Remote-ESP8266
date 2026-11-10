"""Version allocation, change detection, and release-note helper for GUI Docker releases."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

TAG_PREFIX = "ui-v"
TAG_RE = re.compile(r"^ui-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

GUI_RELEVANT_PREFIXES = (
    "gui/app/",
    "gui/Dockerfile",
    "gui/.dockerignore",
    "gui/requirements.txt",
    "gui/docker-compose.yml",
    "gui/docker-compose.dev.yml",
    "gui/.env.example",
    ".github/workflows/release-gui.yml",
    "scripts/release_version.py",
)


def parse_ui_tag(tag: str) -> tuple[int, int, int] | None:
    match = TAG_RE.match(tag.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_version(version: tuple[int, int, int]) -> str:
    return f"{version[0]}.{version[1]}.{version[2]}"


def format_ui_tag(version: tuple[int, int, int]) -> str:
    return f"{TAG_PREFIX}{format_version(version)}"


def bump_version(current: tuple[int, int, int] | None, bump: str = "patch") -> tuple[int, int, int]:
    if bump not in {"patch", "minor", "major"}:
        raise ValueError(f"Unsupported version bump: {bump}")
    if current is None:
        return (1, 0, 0) if bump == "major" else (0, 1, 0)
    major, minor, patch = current
    if bump == "major":
        return (major + 1, 0, 0)
    if bump == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def is_gui_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized.removeprefix("./")
    normalized = normalized.lstrip("/")
    for prefix in GUI_RELEVANT_PREFIXES:
        if prefix.endswith("/"):
            if normalized.startswith(prefix):
                return True
        elif normalized == prefix:
            return True
    return False


def plan_release(
    *,
    target_sha: str,
    main_head_sha: str,
    is_target_on_main: bool,
    tag_commits: dict[str, str],
    changed_paths: list[str] | None,
    is_latest_tag_ancestor_of_target: bool = True,
    bump: str = "patch",
    force: bool = False,
) -> dict:
    target = target_sha.strip().lower()
    main_head = main_head_sha.strip().lower()
    if not SHA_RE.match(target):
        raise ValueError("target_sha must be a 40-character lowercase hex SHA")
    if not SHA_RE.match(main_head):
        raise ValueError("main_head_sha must be a 40-character lowercase hex SHA")
    if bump not in {"patch", "minor", "major"}:
        raise ValueError(f"Unsupported version bump: {bump}")

    if not is_target_on_main:
        return {
            "should_release": False,
            "is_rerun": False,
            "promote_latest": False,
            "target_sha": target,
            "reason": "Target commit is not an ancestor of main",
        }

    parsed_tags: dict[tuple[int, int, int], tuple[str, str]] = {}
    for raw_tag, commit_sha in tag_commits.items():
        ver = parse_ui_tag(raw_tag)
        if ver is not None:
            normalized_sha = commit_sha.strip().lower()
            if not SHA_RE.match(normalized_sha):
                raise ValueError(f"Tag {raw_tag} has invalid commit SHA: {commit_sha}")
            parsed_tags[ver] = (format_ui_tag(ver), normalized_sha)

    highest_version = max(parsed_tags.keys(), default=None)
    previous_tag = format_ui_tag(highest_version) if highest_version is not None else ""

    existing_for_target = [ver for ver, (_tag, sha) in parsed_tags.items() if sha == target]
    if existing_for_target:
        version_tuple = max(existing_for_target)
        tag = format_ui_tag(version_tuple)
        version_str = format_version(version_tuple)
        promote_latest = (highest_version is None or version_tuple >= highest_version) and (target == main_head)
        prior_versions = [v for v in parsed_tags.keys() if v < version_tuple]
        prev_for_existing = format_ui_tag(max(prior_versions)) if prior_versions else ""
        return {
            "should_release": True,
            "is_rerun": True,
            "tag": tag,
            "version": version_str,
            "sha_tag": f"sha-{target[:12]}",
            "target_sha": target,
            "previous_tag": prev_for_existing,
            "promote_latest": promote_latest,
            "reason": f"Re-running existing release {tag}",
        }

    if target != main_head:
        return {
            "should_release": False,
            "is_rerun": False,
            "promote_latest": False,
            "target_sha": target,
            "previous_tag": previous_tag,
            "reason": "Stale commit: main has already advanced past target_sha",
        }

    if highest_version is not None and not is_latest_tag_ancestor_of_target:
        return {
            "should_release": False,
            "is_rerun": False,
            "promote_latest": False,
            "target_sha": target,
            "previous_tag": previous_tag,
            "reason": f"Latest release tag {previous_tag} is not an ancestor of target_sha",
        }

    if highest_version is not None and not force:
        gui_changed = any(is_gui_path(path) for path in (changed_paths or []))
        if not gui_changed:
            return {
                "should_release": False,
                "is_rerun": False,
                "promote_latest": False,
                "target_sha": target,
                "previous_tag": previous_tag,
                "reason": f"No GUI or deployment changes since {previous_tag}",
            }

    version_tuple = bump_version(highest_version, bump)
    tag = format_ui_tag(version_tuple)
    version_str = format_version(version_tuple)
    return {
        "should_release": True,
        "is_rerun": False,
        "tag": tag,
        "version": version_str,
        "sha_tag": f"sha-{target[:12]}",
        "target_sha": target,
        "previous_tag": previous_tag,
        "promote_latest": True,
        "reason": "Initial GUI release" if highest_version is None else f"Releasing {tag} after {previous_tag}",
    }


def render_release_notes(
    *,
    tag: str,
    version: str,
    image_repo: str,
    digest: str,
    target_sha: str,
    previous_tag: str = "",
    promote_latest: bool = True,
) -> str:
    if parse_ui_tag(tag) is None:
        raise ValueError(f"Invalid UI release tag: {tag}")
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError(f"Invalid OCI image digest: {digest}")
    lines = [
        f"## Web UI Container Release `{tag}`",
        "",
        "### Published OCI Images (GHCR)",
        f"- **Versioned tag (recommended for pinning/rollback):** `{image_repo}:{tag}`",
        f"- **Semantic version alias:** `{image_repo}:{version}`",
        f"- **Commit SHA tag:** `{image_repo}:sha-{target_sha[:12]}`",
    ]
    if promote_latest:
        lines.append(f"- **Moving auto-update tag:** `{image_repo}:latest`")
    lines.extend([
        f"- **Multi-arch manifest digest (`linux/amd64`, `linux/arm64`):** `{image_repo}@{digest}`",
        "",
        "### Pull & Update",
        "```sh",
        f"docker pull {image_repo}:{tag}",
        "docker compose -f gui/docker-compose.yml pull",
        "docker compose -f gui/docker-compose.yml up -d",
        "```",
        "",
        "### Pin or Roll Back to This Release",
        "Set `GUI_IMAGE_TAG` in `gui/.env` (or pass it inline) and recreate the container without touching the `signals` volume:",
        "```sh",
        f"GUI_IMAGE_TAG={tag} docker compose -f gui/docker-compose.yml pull",
        f"GUI_IMAGE_TAG={tag} docker compose -f gui/docker-compose.yml up -d",
        "```",
    ])
    if previous_tag:
        lines.extend([
            "",
            f"Previous UI release tag: `{previous_tag}`",
        ])
    return "\n".join(lines) + "\n"


def _git(repo_dir: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo_dir, text=True).strip()


def _git_is_ancestor(repo_dir: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "git merge-base --is-ancestor failed")
    return result.returncode == 0


def plan_from_git(
    repo_dir: Path,
    *,
    target_sha: str,
    main_ref: str = "refs/remotes/origin/main",
    bump: str = "patch",
    force: bool = False,
) -> dict:
    resolved_target = _git(repo_dir, "rev-parse", f"{target_sha}^{{commit}}")
    resolved_main = _git(repo_dir, "rev-parse", f"{main_ref}^{{commit}}")
    is_on_main = _git_is_ancestor(repo_dir, resolved_target, resolved_main)

    raw_tags = _git(
        repo_dir,
        "for-each-ref",
        "--format=%(refname:strip=2) %(objectname:lstrip=0) %(*objectname:lstrip=0)",
        f"refs/tags/{TAG_PREFIX}*",
    )
    tag_commits: dict[str, str] = {}
    for line in raw_tags.splitlines():
        parts = line.split()
        if not parts:
            continue
        tag_name = parts[0]
        commit_sha = parts[2] if len(parts) > 2 and parts[2] else parts[1]
        if parse_ui_tag(tag_name) is not None:
            tag_commits[tag_name] = commit_sha

    parsed_versions = {ver: sha for tag, sha in tag_commits.items() if (ver := parse_ui_tag(tag)) is not None}
    highest_version = max(parsed_versions.keys(), default=None)
    changed_paths: list[str] | None = None
    is_latest_ancestor = True

    if highest_version is not None:
        latest_tag_sha = parsed_versions[highest_version]
        is_latest_ancestor = _git_is_ancestor(repo_dir, latest_tag_sha, resolved_target)
        if is_latest_ancestor:
            diff_output = _git(repo_dir, "diff", "--name-only", f"{latest_tag_sha}..{resolved_target}")
            changed_paths = [line for line in diff_output.splitlines() if line.strip()]

    return plan_release(
        target_sha=resolved_target,
        main_head_sha=resolved_main,
        is_target_on_main=is_on_main,
        tag_commits=tag_commits,
        changed_paths=changed_paths,
        is_latest_tag_ancestor_of_target=is_latest_ancestor,
        bump=bump,
        force=force,
    )


def _write_github_output(output_path: Path, plan: dict) -> None:
    with output_path.open("a", encoding="utf-8") as handle:
        for key, value in plan.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            else:
                rendered = str(value)
            handle.write(f"{key}={rendered}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Compute release plan from git state")
    plan_parser.add_argument("--repo", type=Path, default=Path("."))
    plan_parser.add_argument("--target-sha", required=True)
    plan_parser.add_argument("--main-ref", default="refs/remotes/origin/main")
    plan_parser.add_argument("--bump", choices=["patch", "minor", "major"], default="patch")
    plan_parser.add_argument("--force", action="store_true")
    plan_parser.add_argument("--github-output", type=Path)

    notes_parser = subparsers.add_parser("notes", help="Render GitHub Release markdown notes")
    notes_parser.add_argument("--tag", required=True)
    notes_parser.add_argument("--version", required=True)
    notes_parser.add_argument("--image-repo", required=True)
    notes_parser.add_argument("--digest", required=True)
    notes_parser.add_argument("--target-sha", required=True)
    notes_parser.add_argument("--previous-tag", default="")
    notes_parser.add_argument("--promote-latest", choices=["true", "false"], default="true")
    notes_parser.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "plan":
        plan = plan_from_git(
            args.repo.resolve(),
            target_sha=args.target_sha,
            main_ref=args.main_ref,
            bump=args.bump,
            force=args.force,
        )
        if args.github_output:
            _write_github_output(args.github_output, plan)
        print(json.dumps(plan, indent=2))
        return 0

    if args.command == "notes":
        notes = render_release_notes(
            tag=args.tag,
            version=args.version,
            image_repo=args.image_repo,
            digest=args.digest,
            target_sha=args.target_sha,
            previous_tag=args.previous_tag,
            promote_latest=(args.promote_latest == "true"),
        )
        if args.output:
            args.output.write_text(notes, encoding="utf-8")
        else:
            sys.stdout.write(notes)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
