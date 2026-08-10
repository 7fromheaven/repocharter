"""Conservative closure discovery for preserved Codex hook commands.

RepoCharter owns its managed hook scripts, but ``apply`` deliberately preserves every foreign
hook group.  A live probe has to carry the statically referenced checkout-local implementation
with that merged configuration, and an attestation has to hash the same bytes.

Arbitrary shell is not statically enumerable.  This module therefore accepts a small relocatable
surface and returns explicit errors for everything else instead of guessing or executing a hook to
discover what it depends on.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT_EXPR = "$(git rev-parse --show-toplevel)"
REPO_ROOT_TOKEN = "__AGENTKIT_REPO_ROOT__"
MANAGED_COMMAND_FRAGMENT = "/hooks/agentkit/"
SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(.*)$", re.S)
EXTERNAL_FILE_LAUNCHERS = re.compile(
    r"^(?:bash|bun|deno|node|nodejs|perl|php|python(?:\d+(?:\.\d+)*)?|ruby|sh|zsh)$"
)


@dataclass(frozen=True)
class CodexHookClosure:
    dependencies: tuple[str, ...]
    errors: tuple[str, ...]


def _group_commands(group: dict, label: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return command strings from current and legacy group shapes."""
    commands: list[tuple[str, str]] = []
    errors: list[str] = []

    legacy = group.get("command")
    if isinstance(legacy, str):
        commands.append((f"{label}.command", legacy))
    elif isinstance(legacy, list):
        for index, command in enumerate(legacy):
            if isinstance(command, str):
                commands.append((f"{label}.command[{index}]", command))
            else:
                errors.append(f"{label}.command[{index}] is not a string")
    elif legacy is not None:
        errors.append(f"{label}.command is neither a string nor a string array")

    handlers = group.get("hooks")
    if handlers is not None and not isinstance(handlers, list):
        errors.append(f"{label}.hooks is not an array")
    for index, handler in enumerate(handlers or []):
        handler_label = f"{label}.hooks[{index}]"
        if not isinstance(handler, dict):
            errors.append(f"{handler_label} is not an object")
            continue
        if handler.get("type") != "command":
            errors.append(
                f"{handler_label} has unsupported type {handler.get('type')!r}; "
                "its enforcement bytes cannot be attested"
            )
            continue
        command = handler.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append(f"{handler_label}.command is not a non-empty string")
            continue
        commands.append((f"{handler_label}.command", command))
    if not commands and not errors:
        errors.append(f"{label} has no statically inspectable command handler")
    return commands, errors


def _foreign_commands(config: dict) -> tuple[list[tuple[str, str]], list[str]]:
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return [], [".codex/hooks.json has no object-valued `hooks` block"]

    foreign: list[tuple[str, str]] = []
    errors: list[str] = []
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            errors.append(f"hooks.{event} is not an array")
            continue
        for index, group in enumerate(groups):
            label = f"hooks.{event}[{index}]"
            if not isinstance(group, dict):
                errors.append(f"{label} is not an object")
                continue
            commands, group_errors = _group_commands(group, label)
            # ``apply`` owns an entire group when any handler points into its managed hook
            # directory.  Use the same boundary here so mixed legacy shapes cannot be hashed
            # once as managed and again as foreign.
            if any(MANAGED_COMMAND_FRAGMENT in command for _, command in commands):
                continue
            foreign.extend(commands)
            errors.extend(group_errors)
    return foreign, errors


def _dependency_path(repo: Path, token: str, label: str) -> tuple[str | None, str | None]:
    """Resolve one path-looking token without executing or expanding shell syntax."""
    if token.startswith(REPO_ROOT_TOKEN):
        suffix = token[len(REPO_ROOT_TOKEN):]
        if not suffix.startswith("/"):
            return None, f"{label} has a malformed repository-root path {token!r}"
        relative = suffix[1:]
    elif token.startswith("/"):
        return None, (
            f"{label} references external or absolute path {token!r}; use the relocatable "
            f"{REPO_ROOT_EXPR}/... form for a checkout-local hook"
        )
    elif token.startswith("./") or token.startswith("../") or "/" in token:
        relative = token[2:] if token.startswith("./") else token
    elif (repo / token).is_file() or Path(token).suffix in SCRIPT_SUFFIXES:
        relative = token
    else:
        return None, None

    lexical = Path(relative)
    if not relative or lexical.is_absolute() or ".." in lexical.parts:
        return None, f"{label} has a non-contained checkout path {token!r}"
    candidate = repo / lexical
    cursor = repo
    for part in lexical.parts:
        cursor /= part
        if cursor.is_symlink():
            return None, (
                f"{label} dependency {relative!r} traverses a symlink; use a real "
                "checkout-local file so the probe runs the bytes that are attested"
            )
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, f"{label} references missing checkout-local dependency {relative!r}"
    try:
        resolved.relative_to(repo.resolve())
    except ValueError:
        return None, f"{label} dependency {relative!r} resolves outside the checkout"
    if not resolved.is_file():
        return None, f"{label} dependency {relative!r} is not a regular file"
    return lexical.as_posix(), None


def _external_file_launcher(token: str) -> bool:
    """Whether a static system/PATH command is known to execute a following file."""
    if token.startswith(("./", "../", REPO_ROOT_TOKEN)):
        return False
    path = Path(token)
    if path.is_absolute() and path.parent.as_posix() not in {"/bin", "/usr/bin"}:
        return False
    if not path.is_absolute() and "/" in token:
        return False
    return EXTERNAL_FILE_LAUNCHERS.fullmatch(path.name) is not None


def _external_env_launcher(token: str) -> bool:
    return token == "env" or token in {"/bin/env", "/usr/bin/env"}


def _command_dependencies(repo: Path, label: str, command: str) -> tuple[set[str], list[str]]:
    if "\0" in command:
        return set(), [f"{label} contains a NUL byte and cannot be parsed safely"]
    scrubbed = command.replace(REPO_ROOT_EXPR, REPO_ROOT_TOKEN)
    if REPO_ROOT_EXPR in scrubbed or "$" in scrubbed or "`" in scrubbed:
        return set(), [
            f"{label} is dynamic and cannot be safely resolved: {command!r}"
        ]
    if any(mark in scrubbed for mark in (";", "&", "|", "<", ">", "\n", "\r")):
        return set(), [
            f"{label} uses shell control or redirection and cannot be safely resolved: {command!r}"
        ]
    if any(mark in scrubbed for mark in ("*", "?", "[", "]")):
        return set(), [f"{label} uses a glob and cannot be safely resolved: {command!r}"]
    try:
        tokens = shlex.split(scrubbed, posix=True)
    except ValueError as exc:
        return set(), [f"{label} cannot be parsed safely ({exc}): {command!r}"]
    if not tokens:
        return set(), [f"{label} is empty"]

    index = 0

    def skip_assignments(start: int) -> tuple[int, str | None]:
        while start < len(tokens):
            match = ASSIGNMENT.match(tokens[start])
            if not match:
                break
            value = match.group(1)
            if "/" in value or REPO_ROOT_TOKEN in value:
                return start, (
                    f"{label} has a path-valued environment assignment that cannot be safely "
                    f"resolved: {tokens[start]!r}"
                )
            start += 1
        return start, None

    index, assignment_error = skip_assignments(index)
    if assignment_error:
        return set(), [assignment_error]
    if index < len(tokens) and _external_env_launcher(tokens[index]):
        index += 1
        if index < len(tokens) and tokens[index].startswith("-"):
            return set(), [
                f"{label} uses unsupported env options and cannot be safely resolved: "
                f"{tokens[index]!r}"
            ]
        index, assignment_error = skip_assignments(index)
        if assignment_error:
            return set(), [assignment_error]
    if index >= len(tokens):
        return set(), [f"{label} contains assignments but no command"]

    dependencies: set[str] = set()
    errors: list[str] = []
    launcher = tokens[index]
    launcher_dep, launcher_error = _dependency_path(repo, launcher, label)
    if launcher_error:
        # A small known set of file interpreters may remain external. Their availability is
        # proved by the live run; arbitrary external executables are not mistaken for the
        # checkout-local implementation whose bytes the attestation must bind.
        if not _external_file_launcher(launcher):
            errors.append(launcher_error)
    elif launcher_dep:
        dependencies.add(launcher_dep)
    elif not _external_file_launcher(launcher):
        errors.append(
            f"{label} uses unsupported external launcher {launcher!r}; its enforcement "
            "implementation cannot be safely attested"
        )

    for token in tokens[index + 1:]:
        if token.startswith(("-c", "-e", "-m", "--eval", "--evaluate")):
            errors.append(
                f"{label} uses inline code/module execution {token!r}; no static hook file "
                "can be attested"
            )
            continue
        if token.startswith("-"):
            if "=" in token:
                option_value = token.split("=", 1)[1]
                dependency, error = _dependency_path(repo, option_value, label)
                if error:
                    errors.append(error)
                elif dependency:
                    dependencies.add(dependency)
            continue
        dependency, error = _dependency_path(repo, token, label)
        if error:
            errors.append(error)
        elif dependency:
            dependencies.add(dependency)

    if not dependencies and not errors:
        errors.append(
            f"{label} invokes {launcher!r} without a statically referenced checkout-local "
            "implementation"
        )
    return dependencies, errors


def discover(repo: Path) -> CodexHookClosure:
    """Find the relocatable checkout-local files used by preserved Codex hook groups."""
    path = repo / ".codex" / "hooks.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return CodexHookClosure((), (f"cannot read .codex/hooks.json ({exc})",))
    except json.JSONDecodeError as exc:
        return CodexHookClosure((), (f".codex/hooks.json is invalid JSON ({exc})",))
    if not isinstance(config, dict):
        return CodexHookClosure((), (".codex/hooks.json is not an object",))

    commands, errors = _foreign_commands(config)
    dependencies: set[str] = set()
    for label, command in commands:
        found, command_errors = _command_dependencies(repo, label, command)
        dependencies.update(found)
        errors.extend(command_errors)
    return CodexHookClosure(tuple(sorted(dependencies)), tuple(errors))


def copy_dependencies(repo: Path, probe: Path, closure: CodexHookClosure) -> None:
    """Copy an already-validated closure to identical relative paths in a probe checkout."""
    if closure.errors:
        raise ValueError("cannot copy an invalid Codex hook closure")
    for relative in closure.dependencies:
        source = repo / relative
        destination = probe / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
