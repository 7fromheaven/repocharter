#!/usr/bin/env python3
"""Report the resumable state of one RepoCharter checkout without changing it."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path


def run(command: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def git(repo: Path, *args: str) -> str:
    proc = run(["git", *args], repo)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_agentkit_path(path: Path, module_name: str):
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_loader(
        module_name, SourceFileLoader(module_name, str(path)),
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_path(kit_root: Path) -> Path | None:
    """Prefer the 0.4+ implementation, falling back to the 0.3.x monolith."""
    canonical = kit_root / "repocharter"
    if canonical.is_file():
        return canonical
    compatibility = kit_root / "agentkit"
    return compatibility if compatibility.is_file() else None


def load_agentkit(repo: Path):
    candidate = runtime_path(repo / "kit")
    return load_agentkit_path(candidate, "repocharter_checkpoint_target") \
        if candidate is not None else None


def distribution_agentkit(repo: Path):
    """Use the source distribution's version when this script is run from `kit/skills`."""
    script = Path(__file__).resolve()
    candidate = next((runtime_path(parent) for parent in script.parents
                      if parent.name == "kit" and runtime_path(parent) is not None), None)
    target = runtime_path(repo / "kit")
    if candidate is None or (target is not None and candidate.resolve() == target.resolve()):
        return None
    return load_agentkit_path(candidate, "repocharter_checkpoint_distribution")


def strict_verification(repo: Path, effective: bool, kit_root: Path | None = None) -> dict:
    residue = (kit_root or (repo / "kit")) / "verify" / "residue.py"
    if not residue.is_file():
        return {"ran": False, "passed": False, "errors": ["vendored verifier is absent"]}
    command = [sys.executable, str(residue), "--repo", str(repo), "--strict", "--json"]
    if effective:
        command.append("--effective")
    proc = run(command, repo, timeout=90)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = {"errors": [proc.stderr.strip() or "verifier returned unreadable output"]}
    return {
        "ran": True,
        "effective": effective,
        "passed": proc.returncode == 0,
        "stage": report.get("stage"),
        "errors": report.get("errors") or [],
        "warnings": report.get("warnings") or [],
        "todos": report.get("todos") or [],
    }


def provider_state(repo: Path, harness: str, compat: dict, agentkit) -> dict:
    return agentkit.provider_promotion_state(repo, harness, compat)


def checkpoint(repo: Path, effective: bool) -> dict:
    top = git(repo, "rev-parse", "--show-toplevel")
    if not top:
        raise SystemExit(f"checkpoint: {repo} is not a Git checkout")
    repo = Path(top).resolve()
    porcelain = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], repo
    ).stdout.splitlines()
    dot_git = repo / ".git"
    checkout_shape = "linked-worktree" if dot_git.is_file() else "normal"
    distribution = distribution_agentkit(repo)
    # Load the newer distribution first so its lib modules cannot be shadowed by an older
    # target's same-named imports during an upgrade checkpoint.
    agentkit = load_agentkit(repo)
    operating_agentkit = distribution or agentkit
    compat = read_json(repo / ".agents" / "compatibility.json") or {}
    verification = strict_verification(
        repo, effective, Path(distribution.KIT_ROOT) if distribution is not None else None,
    )

    providers = {}
    if operating_agentkit is not None:
        for harness in ("codex", "claude-code"):
            providers[harness] = provider_state(
                repo, harness, compat, operating_agentkit,
            )

    codex = providers.get("codex") or {}
    codex_discovery = codex.get("discovery") or {}
    broken_linked_discovery = (
        checkout_shape == "linked-worktree"
        and codex_discovery.get("status") in {"missing", "wrong-checkout"}
    )
    ready_without_promotion = (
        verification.get("passed") is True
        and bool(providers)
        and all(state.get("current") for state in providers.values())
    )
    target_version = getattr(agentkit, "KIT_VERSION", None)
    expected_version = getattr(distribution, "KIT_VERSION", target_version)
    upgrade_required = target_version != expected_version
    unrelated = operating_agentkit.unrelated_changes(repo) \
        if operating_agentkit is not None else list(porcelain)
    unrelated = unrelated or []
    dirty_requires_clone = bool(unrelated) and upgrade_required
    route = "standalone-clone" if broken_linked_discovery or dirty_requires_clone else "in-place"
    standalone_mode = None
    if route == "standalone-clone":
        # Source substitution means migration changes are already prepared in this checkout;
        # preserving them is the point of the fallback. Other dirty trees conservatively start
        # from clean HEAD so unrelated work remains only in the untouched source checkout.
        standalone_mode = "preserve-state" if broken_linked_discovery else "clean-head"
    return {
        "repo": str(repo),
        "checkout": {
            "shape": checkout_shape,
            "branch": git(repo, "branch", "--show-current") or "(detached)",
            "head": git(repo, "rev-parse", "HEAD"),
            "dirty": bool(porcelain),
            "changes": porcelain,
        },
        "agentkitVersion": getattr(agentkit, "KIT_VERSION", None),
        "expectedAgentkitVersion": expected_version,
        "upgradeRequired": upgrade_required,
        "manifestVersion": compat.get("agentkitVersion"),
        "strictVerification": verification,
        "providers": providers,
        "route": route,
        "standaloneMode": standalone_mode,
        "unrelatedChanges": unrelated,
        "readyWithoutPromotion": ready_without_promotion,
    }


def print_human(report: dict) -> None:
    checkout = report["checkout"]
    print(f"RepoCharter checkpoint -> {report['repo']}")
    print(f"  checkout     {checkout['shape']} · {checkout['branch']} · {checkout['head'][:12]}")
    print(f"  tree         {'dirty (' + str(len(checkout['changes'])) + ')' if checkout['dirty'] else 'clean'}")
    print(f"  version      kit {report['agentkitVersion'] or 'absent'} · expected "
          f"{report['expectedAgentkitVersion'] or 'unknown'} · "
          f"manifest {report['manifestVersion'] or 'absent'}")
    verify = report["strictVerification"]
    label = "PASS" if verify.get("passed") else ("FAIL" if verify.get("ran") else "NOT RUN")
    print(f"  verification {label}{' (effective)' if verify.get('effective') else ''}")
    for harness, state in report["providers"].items():
        print(f"  {harness:<12} {'current; no promotion' if state['current'] else 'promotion required'}")
        for reason in state["reasons"]:
            print(f"    - {reason}")
    print(f"  route        {report['route']}")
    if report["readyWithoutPromotion"]:
        print("  next         continue with validation/integration; do not rerun live provider probes")
    elif report["route"] == "standalone-clone":
        print(f"  next         prepare_standalone.py --mode {report['standaloneMode']}; continue there")
    else:
        print("  next         fix verification findings, then promote only providers named above")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="target repository (default: cwd)")
    parser.add_argument("--effective", action="store_true",
                        help="include Codex's effective prompt-ledger verification")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    report = checkpoint(Path(args.repo).resolve(), args.effective)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
