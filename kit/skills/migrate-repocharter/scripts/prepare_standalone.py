#!/usr/bin/env python3
"""Preserve one checkout's uncommitted state in an independent normal clone."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath


def run(command: list[str], cwd: Path, *, data: bytes | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(command, cwd=cwd, input=data, capture_output=True)
    if check and proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return proc


def git(repo: Path, *args: str, data: bytes | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *args], repo, data=data, check=check)


def output(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.decode("utf-8", errors="strict").strip()


def patch_bytes(repo: Path, staged: bool) -> bytes:
    args = ["diff", "--binary", "--full-index", "--no-ext-diff"]
    if staged:
        args.insert(1, "--cached")
    return git(repo, *args).stdout


def status_bytes(repo: Path) -> bytes:
    return git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout


def safe_untracked(repo: Path) -> list[PurePosixPath]:
    raw = git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout
    paths: list[PurePosixPath] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        text = item.decode("utf-8", errors="strict")
        path = PurePosixPath(text)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise RuntimeError(f"unsafe untracked path reported by Git: {text!r}")
        source = repo.joinpath(*path.parts)
        mode = source.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            raise RuntimeError(f"untracked special file cannot be preserved safely: {text}")
        paths.append(path)
    return paths


def copy_untracked(source: Path, destination: Path, paths: list[PurePosixPath]) -> None:
    for relative in paths:
        src = source.joinpath(*relative.parts)
        dst = destination.joinpath(*relative.parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare(source: Path, destination: Path, branch: str | None, mode: str) -> dict:
    top = output(source, "rev-parse", "--show-toplevel")
    source = Path(top).resolve()
    destination = destination.resolve()
    if destination.exists():
        raise RuntimeError(f"destination already exists; left untouched: {destination}")
    if source == destination or source in destination.parents:
        raise RuntimeError("destination must be outside the source checkout")
    if (source / ".gitmodules").exists():
        raise RuntimeError("repositories with submodules require an explicit preservation review")

    head = output(source, "rev-parse", "HEAD")
    source_status = status_bytes(source)
    preserve = mode == "preserve-state"
    staged = patch_bytes(source, True) if preserve else b""
    unstaged = patch_bytes(source, False) if preserve else b""
    untracked = safe_untracked(source) if preserve else []
    expected_status = source_status if preserve else b""
    branch = branch or "chore/repocharter-migration"
    check_branch = git(source, "check-ref-format", "--branch", branch, check=False)
    if check_branch.returncode != 0:
        raise RuntimeError(f"invalid destination branch name: {branch}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--no-hardlinks", "--no-checkout", str(source), str(destination)],
        source)
    try:
        git(destination, "switch", "--detach", head)
        git(destination, "switch", "-c", branch)
        if staged:
            git(destination, "apply", "--index", "--binary", data=staged)
        if unstaged:
            git(destination, "apply", "--binary", data=unstaged)
        copy_untracked(source, destination, untracked)

        canonical_origin = output(source, "remote", "get-url", "origin") \
            if git(source, "remote", "get-url", "origin", check=False).returncode == 0 else ""
        if canonical_origin:
            git(destination, "remote", "set-url", "origin", canonical_origin)
        if (destination / ".githooks").is_dir():
            git(destination, "config", "core.hooksPath", ".githooks")

        after_staged = patch_bytes(destination, True)
        after_unstaged = patch_bytes(destination, False)
        after_status = status_bytes(destination)
        if digest(staged) != digest(after_staged):
            raise RuntimeError("staged patch differs after transfer; destination retained for review")
        if digest(unstaged) != digest(after_unstaged):
            raise RuntimeError("unstaged patch differs after transfer; destination retained for review")
        if expected_status != after_status:
            raise RuntimeError("porcelain state differs after transfer; destination retained for review")
    except Exception:
        print(f"prepare-standalone: incomplete destination retained at {destination}", file=sys.stderr)
        raise

    return {
        "source": str(source),
        "destination": str(destination),
        "head": head,
        "branch": branch,
        "mode": mode,
        "sourceStateLeftUntouched": bool(source_status),
        "stagedSha256": digest(staged),
        "unstagedSha256": digest(unstaged),
        "untrackedFiles": len(untracked),
        "statusSha256": digest(expected_status),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="source repository (default: cwd)")
    parser.add_argument("--dest", required=True, help="new private standalone-clone path")
    parser.add_argument("--branch", help="new branch name (default: chore/repocharter-migration)")
    parser.add_argument(
        "--mode", choices=("clean-head", "preserve-state"), default="clean-head",
        help=("clean-head leaves source WIP only in the source; preserve-state copies reviewed "
              "prepared migration changes (default: clean-head)"),
    )
    args = parser.parse_args()
    try:
        result = prepare(Path(args.repo).resolve(), Path(args.dest), args.branch, args.mode)
    except (OSError, RuntimeError, UnicodeError) as exc:
        print(f"prepare-standalone: {exc}", file=sys.stderr)
        return 1
    print("RepoCharter standalone clone prepared and byte-checked")
    for key, value in result.items():
        print(f"  {key}: {value}")
    print("  next: rerun checkpoint.py in this clone, then apply before provider trust/promotion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
