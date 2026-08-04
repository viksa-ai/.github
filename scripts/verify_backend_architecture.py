#!/usr/bin/env python3
"""Verify the common DDD/DI/type/duplication contract across Viksa backends."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "generated",
        "migrations",
        "node_modules",
        "tests",
        "venv",
    }
)


@dataclass(frozen=True)
class SourceLocation:
    repository: str
    path: str
    line: int | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"repository": self.repository, "path": self.path}
        if self.line is not None:
            result["line"] = self.line
        return result


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected an object in {path}")
    return value


def _python_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return ()
    return (
        path
        for path in root.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts)
    )


def _module_name(path: Path, package_root: Path, package: str) -> str:
    relative = path.relative_to(package_root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join([package, *parts])


def _import_targets(
    tree: ast.AST,
    *,
    current_package: str,
) -> Iterable[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative = "." * node.level + (node.module or "")
                try:
                    resolved = importlib.util.resolve_name(relative, current_package)
                except (ImportError, ValueError):
                    resolved = relative
            else:
                resolved = node.module or ""
            if node.module:
                if resolved:
                    yield resolved, node.lineno
            else:
                for alias in node.names:
                    target = f"{resolved}.{alias.name}" if resolved else alias.name
                    yield target, node.lineno


_DYNAMIC_IMPORT_CALLS = frozenset(
    {
        "__import__",
        "_attribute",
        "_legacy_router",
        "import_module",
        "load_attribute",
        "load_module",
    }
)

_DYNAMIC_REGISTRY_MARKERS = (
    "COMPAT",
    "EXPORT",
    "IMPORT",
    "LEGACY",
    "MODULE",
    "REGISTRY",
)
_MODULE_PATH_PATTERN = re.compile(
    r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$"
)


def _dynamic_loader_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Resolve loader aliases, including renamed imports and importlib aliases."""
    loader_names = set(_DYNAMIC_IMPORT_CALLS)
    importlib_names = {"importlib"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _DYNAMIC_IMPORT_CALLS:
                    loader_names.add(alias.asname or alias.name)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            value_name = _expression_name(node.value)
            if not _is_dynamic_loader_name(value_name, loader_names, importlib_names):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in loader_names:
                    loader_names.add(target.id)
                    changed = True
    return loader_names, importlib_names


def _expression_name(expression: ast.expr) -> str:
    names: list[str] = []
    target = expression
    while isinstance(target, ast.Attribute):
        names.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        names.append(target.id)
    return ".".join(reversed(names))


def _is_dynamic_loader_name(
    name: str,
    loader_names: set[str],
    importlib_names: set[str],
) -> bool:
    if name in loader_names or name.rsplit(".", 1)[-1] in loader_names:
        return True
    prefix, separator, leaf = name.rpartition(".")
    return bool(separator and prefix in importlib_names and leaf == "import_module")


def _assignment_target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for element in target.elts:
            names.update(_assignment_target_names(element))
        return names
    return set()


def _loader_registry_names(
    tree: ast.AST,
    loader_names: set[str],
    importlib_names: set[str],
) -> set[str]:
    """Find registries that feed non-literal module names into a loader."""
    loaded_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not _is_dynamic_loader_name(
            _call_name(node), loader_names, importlib_names
        ):
            continue
        if isinstance(node.args[0], ast.Name):
            loaded_names.add(node.args[0].id)

    registry_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        assigned = set().union(*(_assignment_target_names(target) for target in targets))
        if not assigned.intersection(loaded_names):
            continue
        for candidate in ast.walk(node.value):
            if isinstance(candidate, ast.Subscript) and isinstance(
                candidate.value, ast.Name
            ):
                registry_names.add(candidate.value.id)
    return registry_names


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


def _literal_registry_targets(
    tree: ast.AST,
    registry_names: set[str],
) -> Iterable[tuple[str, int]]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        name = _assigned_name(node)
        if name is None:
            continue
        is_loader_registry = name in registry_names or any(
            marker in name.upper() for marker in _DYNAMIC_REGISTRY_MARKERS
        )
        if not is_loader_registry:
            continue
        for value in ast.walk(node.value):
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and _MODULE_PATH_PATTERN.fullmatch(value.value)
            ):
                yield value.value, value.lineno


def _dynamic_import_targets(tree: ast.AST) -> Iterable[tuple[str, int]]:
    """Find literal module paths passed through loaders or loader registries."""
    loader_names, importlib_names = _dynamic_loader_names(tree)
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = _call_name(node)
        if not _is_dynamic_loader_name(name, loader_names, importlib_names):
            continue
        target = node.args[0]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            item = (target.value, node.lineno)
            if item not in seen:
                seen.add(item)
                yield item

    registry_names = _loader_registry_names(tree, loader_names, importlib_names)
    for item in _literal_registry_targets(tree, registry_names):
        if item not in seen:
            seen.add(item)
            yield item


def _call_name(call: ast.Call) -> str:
    target: ast.expr = call.func
    names: list[str] = []
    while isinstance(target, ast.Attribute):
        names.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        names.append(target.id)
    return ".".join(reversed(names))


def _assigned_calls(node: ast.stmt) -> Iterable[ast.Call]:
    value: ast.expr | None = None
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        value = node.value
    if value is None:
        return ()
    return (child for child in ast.walk(value) if isinstance(child, ast.Call))


def _is_resource_constructor(name: str, suffixes: Sequence[str]) -> bool:
    leaf = name.rsplit(".", 1)[-1]
    explicit = {
        "AsyncIOMotorClient",
        "MongoClient",
        "Redis",
        "StrictRedis",
        "AsyncClient",
        "KafkaProducer",
        "KafkaConsumer",
    }
    return leaf in explicit or any(leaf.endswith(suffix) for suffix in suffixes)


def _parse(path: Path) -> tuple[ast.Module | None, str | None]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), None
    except (OSError, SyntaxError, UnicodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _strict_mypy_errors(pyproject: Path) -> list[str]:
    if not pyproject.is_file():
        return ["pyproject.toml is missing"]
    text = pyproject.read_text(encoding="utf-8")
    if "[tool.mypy]" not in text:
        return ["[tool.mypy] is missing"]
    compact = "".join(text.lower().split())
    errors: list[str] = []
    if "strict=true" not in compact:
        errors.append("tool.mypy.strict must be true")
    weakening = {
        "ignore_errors=true": "ignore_errors must not be enabled",
        "follow_imports=\"skip\"": "follow_imports=skip is not permitted",
        "follow_imports=\"silent\"": "follow_imports=silent is not permitted",
        "follow_imports='skip'": "follow_imports=skip is not permitted",
        "follow_imports='silent'": "follow_imports=silent is not permitted",
    }
    errors.extend(message for marker, message in weakening.items() if marker in compact)
    return errors


def _layer_for(path: Path, package_root: Path) -> str | None:
    relative = path.relative_to(package_root)
    return relative.parts[0] if relative.parts else None


def _forbidden_import_reason(
    source_layer: str,
    target: str,
    package: str,
    forbidden_domain_frameworks: Sequence[str],
    legacy_source_directories: Sequence[str],
) -> str | None:
    if target.split(".", 1)[0] in legacy_source_directories:
        return f"{source_layer} depends on legacy source namespace {target.split('.', 1)[0]}"
    target_layer: str | None = None
    prefix = f"{package}."
    if target.startswith(prefix):
        target_layer = target[len(prefix) :].split(".", 1)[0]

    if source_layer == "domain":
        if target_layer in {"application", "ports", "infrastructure", "entrypoints", "bootstrap"}:
            return f"domain depends on {target_layer}"
        if target.split(".", 1)[0] in forbidden_domain_frameworks:
            return f"domain depends on framework {target.split('.', 1)[0]}"
    elif source_layer in {"application", "ports"}:
        if target_layer in {"infrastructure", "entrypoints", "bootstrap"}:
            return f"{source_layer} depends on {target_layer}"
    elif source_layer == "infrastructure" and target_layer in {"entrypoints", "bootstrap"}:
        return f"infrastructure depends on {target_layer}"
    elif source_layer == "entrypoints" and target_layer in {"infrastructure", "bootstrap"}:
        return f"entrypoints depends on {target_layer}"
    return None


def _meaningful_function_hashes(
    repository: str, path: Path, tree: ast.Module, workspace: Path
) -> Iterable[tuple[str, SourceLocation, str]]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        normalized = ast.dump(node, annotate_fields=True, include_attributes=False)
        if len(normalized) < 700 or len(node.body) < 3:
            continue
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        location = SourceLocation(repository, str(path.relative_to(workspace)), node.lineno)
        yield digest, location, node.name


def _legacy_roots(
    repo_root: Path,
    configured: dict[str, Any],
    legacy_dirs: Sequence[str],
) -> tuple[Path, ...]:
    configured_roots = configured.get("legacy_source_roots")
    if configured_roots is None:
        roots = tuple(repo_root / directory for directory in legacy_dirs)
    else:
        if not isinstance(configured_roots, list) or not all(
            isinstance(item, str) and item for item in configured_roots
        ):
            raise TypeError(
                f"legacy_source_roots for {configured.get('path', '<unknown>')} "
                "must be a list of non-empty relative paths"
            )
        roots = tuple(repo_root / item for item in configured_roots)
    for root in roots:
        try:
            root.resolve().relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ValueError(
                f"legacy source root {root} escapes repository {repo_root}"
            ) from exc
    return roots


def _production_roots(
    repo_root: Path,
    package: str,
    legacy_roots: Sequence[Path],
) -> Iterable[Path]:
    canonical = repo_root / "src" / package
    if canonical.exists():
        yield canonical
    for candidate in legacy_roots:
        if candidate.exists():
            yield candidate
    app = repo_root / "app.py"
    if app.exists():
        yield app


def _legacy_behavior_files(
    legacy_roots: Sequence[Path],
    workspace: Path,
) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for source in legacy_roots:
        if not source.exists():
            continue
        candidates = [source] if source.is_file() else list(_python_files(source))
        for path in candidates:
            if path.name == "__init__.py":
                continue
            tree, _ = _parse(path)
            if tree is None:
                continue
            declarations = [
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            if declarations:
                violations.append(
                    {
                        "path": str(path.relative_to(workspace)),
                        "declarations": [node.name for node in declarations],
                        "reason": "legacy module still owns behavior instead of being an import-only facade",
                    }
                )
    return violations


def verify(workspace: Path, contract: dict[str, Any]) -> dict[str, Any]:
    required_layers = tuple(str(item) for item in contract["required_layers"])
    legacy_dirs = tuple(str(item) for item in contract["legacy_source_directories"])
    forbidden_frameworks = tuple(
        str(item) for item in contract["framework_imports_forbidden_in_domain"]
    )
    constructor_suffixes = tuple(str(item) for item in contract["resource_constructor_suffixes"])

    repositories: list[dict[str, object]] = []
    function_hashes: dict[str, list[tuple[SourceLocation, str]]] = {}
    file_hashes: dict[str, list[SourceLocation]] = {}
    canonical_prefixes = {
        str(configured["path"]): (
            f"{configured['path']}/src/{configured['package']}/"
        )
        for configured in contract["repositories"]
    }

    for configured in contract["repositories"]:
        repo_name = str(configured["path"])
        package = str(configured["package"])
        repo_root = workspace / repo_name
        package_root = repo_root / "src" / package
        configured_legacy_roots = _legacy_roots(repo_root, configured, legacy_dirs)
        missing = [layer for layer in required_layers if not (package_root / layer).is_dir()]
        mypy_errors = _strict_mypy_errors(repo_root / "pyproject.toml")
        parse_errors: list[dict[str, object]] = []
        import_violations: list[dict[str, object]] = []
        di_violations: list[dict[str, object]] = []
        legacy_behavior = _legacy_behavior_files(configured_legacy_roots, workspace)

        if package_root.exists():
            for path in _python_files(package_root):
                tree, parse_error = _parse(path)
                relative = str(path.relative_to(workspace))
                if parse_error:
                    parse_errors.append({"path": relative, "error": parse_error})
                    continue
                assert tree is not None
                layer = _layer_for(path, package_root)
                if layer:
                    module = _module_name(path, package_root, package)
                    current_package = module if path.name == "__init__.py" else module.rpartition(".")[0]
                    for target, line in _import_targets(
                        tree,
                        current_package=current_package,
                    ):
                        reason = _forbidden_import_reason(
                            layer,
                            target,
                            package,
                            forbidden_frameworks,
                            legacy_dirs,
                        )
                        if reason:
                            import_violations.append(
                                {"path": relative, "line": line, "target": target, "reason": reason}
                            )
                    for target, line in _dynamic_import_targets(tree):
                        reason = _forbidden_import_reason(
                            layer,
                            target,
                            package,
                            forbidden_frameworks,
                            legacy_dirs,
                        )
                        if reason:
                            import_violations.append(
                                {
                                    "path": relative,
                                    "line": line,
                                    "target": target,
                                    "reason": f"dynamic {reason}",
                                }
                            )
                if layer != "bootstrap":
                    for statement in tree.body:
                        for call in _assigned_calls(statement):
                            name = _call_name(call)
                            if _is_resource_constructor(name, constructor_suffixes):
                                di_violations.append(
                                    {
                                        "path": relative,
                                        "line": call.lineno,
                                        "constructor": name,
                                        "reason": "resource constructed outside bootstrap composition root",
                                    }
                                )

        seen_paths: set[Path] = set()
        for source_root in _production_roots(repo_root, package, configured_legacy_roots):
            candidates = [source_root] if source_root.is_file() else list(_python_files(source_root))
            for path in candidates:
                resolved = path.resolve()
                if resolved in seen_paths or path.name == "__init__.py":
                    continue
                seen_paths.add(resolved)
                tree, _ = _parse(path)
                if tree is None:
                    continue
                for digest, location, name in _meaningful_function_hashes(
                    repo_name, path, tree, workspace
                ):
                    function_hashes.setdefault(digest, []).append((location, name))
                try:
                    significant = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                except (OSError, UnicodeError):
                    continue
                if len(significant) >= 10:
                    digest = hashlib.sha256("\n".join(significant).encode("utf-8")).hexdigest()
                    file_hashes.setdefault(digest, []).append(
                        SourceLocation(repo_name, str(path.relative_to(workspace)))
                    )

        repositories.append(
            {
                "repository": repo_name,
                "package": package,
                "missing_layers": missing,
                "mypy_configuration_errors": mypy_errors,
                "parse_errors": parse_errors,
                "dependency_violations": import_violations,
                "di_violations": di_violations,
                "legacy_behavior_modules": legacy_behavior,
                "passed": not any(
                    (
                        missing,
                        mypy_errors,
                        parse_errors,
                        import_violations,
                        di_violations,
                        legacy_behavior,
                    )
                ),
            }
        )

    duplicated_functions: list[dict[str, object]] = []
    for digest, entries in sorted(function_hashes.items()):
        distinct_locations = {(location.repository, location.path, location.line) for location, _ in entries}
        if len(distinct_locations) < 2:
            continue
        duplicated_functions.append(
            {
                "sha256": digest,
                "name": entries[0][1],
                "locations": [location.as_dict() for location, _ in entries],
            }
        )

    duplicated_files: list[dict[str, object]] = []
    for digest, locations in sorted(file_hashes.items()):
        if len({(location.repository, location.path) for location in locations}) < 2:
            continue
        duplicated_files.append(
            {"sha256": digest, "locations": [location.as_dict() for location in locations]}
        )

    def touches_canonical(group: dict[str, object]) -> bool:
        locations = group.get("locations")
        if not isinstance(locations, list):
            return False
        for location in locations:
            if not isinstance(location, dict):
                continue
            repository = location.get("repository")
            path = location.get("path")
            if isinstance(repository, str) and isinstance(path, str):
                prefix = canonical_prefixes.get(repository)
                if prefix is not None and path.startswith(prefix):
                    return True
        return False

    canonical_duplicated_functions = [
        group for group in duplicated_functions if touches_canonical(group)
    ]
    canonical_duplicated_files = [
        group for group in duplicated_files if touches_canonical(group)
    ]

    passed = all(bool(repo["passed"]) for repo in repositories)
    passed = passed and not duplicated_functions and not duplicated_files
    return {
        "schema_version": contract["schema_version"],
        "workspace": str(workspace),
        "repositories": repositories,
        "duplication": {
            "meaningful_exact_function_groups": duplicated_functions,
            "exact_production_file_groups": duplicated_files,
            "canonical_meaningful_exact_function_groups": canonical_duplicated_functions,
            "canonical_exact_production_file_groups": canonical_duplicated_files,
        },
        "summary": {
            "repository_count": len(repositories),
            "repositories_passing": sum(bool(repo["passed"]) for repo in repositories),
            "duplicate_function_groups": len(duplicated_functions),
            "duplicate_file_groups": len(duplicated_files),
            "canonical_duplicate_function_groups": len(canonical_duplicated_functions),
            "canonical_duplicate_file_groups": len(canonical_duplicated_files),
            "passed": passed,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    default_contract = Path(__file__).resolve().parents[1] / "quality" / "backend-architecture-contract.json"
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=default_contract)
    parser.add_argument("--json-out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = verify(args.workspace.resolve(), _read_json(args.contract.resolve()))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0 if result["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
