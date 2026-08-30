from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_backend_architecture.py"
SPEC = importlib.util.spec_from_file_location("verify_backend_architecture", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


LAYERS = ["domain", "application", "ports", "infrastructure", "entrypoints", "bootstrap"]


def _contract(repositories: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "required_layers": LAYERS,
        "repositories": repositories,
        "legacy_source_directories": ["services"],
        "framework_imports_forbidden_in_domain": ["fastapi", "redis"],
        "resource_constructor_suffixes": ["Client", "Repository", "Service"],
    }


def _make_service(root: Path, repo: str, package: str) -> Path:
    service = root / repo
    for layer in LAYERS:
        directory = service / "src" / package / layer
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    (service / "pyproject.toml").write_text(
        "[project]\nname = 'example'\nversion = '1.0.0'\n\n[tool.mypy]\nstrict = true\n",
        encoding="utf-8",
    )
    return service


def test_clean_canonical_service_passes(tmp_path: Path) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    (service / "src" / "maeyr_alpha" / "domain" / "widget.py").write_text(
        "from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Widget:\n    name: str\n",
        encoding="utf-8",
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    assert result["summary"]["passed"] is True


def test_reports_layer_di_and_type_configuration_failures(tmp_path: Path) -> None:
    service = tmp_path / "broken-service"
    domain = service / "src" / "maeyr_broken" / "domain"
    domain.mkdir(parents=True)
    (domain / "bad.py").write_text(
        "from fastapi import FastAPI\nfrom maeyr_broken.infrastructure.db import UserRepository\n\n"
        "repository = UserRepository()\n",
        encoding="utf-8",
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "broken-service", "package": "maeyr_broken"}])
    )
    repository = result["repositories"][0]

    assert repository["missing_layers"]
    assert repository["mypy_configuration_errors"] == ["pyproject.toml is missing"]
    assert len(repository["dependency_violations"]) == 2
    assert len(repository["di_violations"]) == 1
    assert result["summary"]["passed"] is False


def test_mypy_configuration_cannot_silence_imported_production_modules(
    tmp_path: Path,
) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    with (service / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n[[tool.mypy.overrides]]\nmodule = ['services.*']\n"
            "follow_imports = 'silent'\n"
        )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    assert result["repositories"][0]["mypy_configuration_errors"] == [
        "follow_imports=silent is not permitted"
    ]


def test_legacy_modules_must_be_import_only_facades(tmp_path: Path) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    legacy = service / "services"
    legacy.mkdir()
    (legacy / "facade.py").write_text(
        "from maeyr_alpha.application.reconcile import reconcile\n", encoding="utf-8"
    )
    (legacy / "behavior.py").write_text(
        "def legacy_reconcile() -> None:\n    return None\n", encoding="utf-8"
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    violations = result["repositories"][0]["legacy_behavior_modules"]
    assert len(violations) == 1
    assert violations[0]["path"].endswith("services/behavior.py")


def test_relative_imports_cannot_bypass_layer_direction(tmp_path: Path) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    (service / "src" / "maeyr_alpha" / "domain" / "bad.py").write_text(
        "from ..infrastructure import persistence\n", encoding="utf-8"
    )
    (service / "src" / "maeyr_alpha" / "application" / "bad.py").write_text(
        "from .. import infrastructure\n", encoding="utf-8"
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    violations = result["repositories"][0]["dependency_violations"]
    assert {item["target"] for item in violations} == {"maeyr_alpha.infrastructure"}
    assert len(violations) == 2


def test_inner_layers_cannot_depend_on_compatibility_namespace(
    tmp_path: Path,
) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    (service / "src" / "maeyr_alpha" / "domain" / "bad.py").write_text(
        "from maeyr_alpha.compat.types import ResourceType\n",
        encoding="utf-8",
    )
    (service / "src" / "maeyr_alpha" / "application" / "bad.py").write_text(
        "from ..compat.ids import get_resource_id\n",
        encoding="utf-8",
    )
    (service / "src" / "maeyr_alpha" / "ports" / "bad.py").write_text(
        "from maeyr_alpha.compat.repositories import Repository\n",
        encoding="utf-8",
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    violations = result["repositories"][0]["dependency_violations"]
    assert {item["reason"] for item in violations} == {
        "application depends on compat",
        "domain depends on compat",
        "ports depends on compat",
    }
    assert len(violations) == 3


def test_canonical_package_cannot_hide_dependencies_behind_legacy_namespaces(
    tmp_path: Path,
) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    (service / "src" / "maeyr_alpha" / "infrastructure" / "bad.py").write_text(
        "from services.reconcile import LegacyReconciler\n", encoding="utf-8"
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    violations = result["repositories"][0]["dependency_violations"]
    assert len(violations) == 1
    assert "legacy source namespace services" in violations[0]["reason"]


def test_dynamic_loaders_cannot_hide_legacy_namespace_dependencies(
    tmp_path: Path,
) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    (service / "src" / "maeyr_alpha" / "infrastructure" / "bad.py").write_text(
        "import importlib\n\n"
        "from maeyr_alpha.compat import load_attribute\n\n"
        "service = load_attribute('services.reconcile', 'service')\n"
        "factory = importlib.import_module('services.factory')\n",
        encoding="utf-8",
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    violations = result["repositories"][0]["dependency_violations"]
    assert {item["target"] for item in violations} == {
        "services.factory",
        "services.reconcile",
    }
    assert all(item["reason"].startswith("dynamic ") for item in violations)


def test_renamed_loader_imports_cannot_hide_legacy_dependencies(
    tmp_path: Path,
) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    (service / "src" / "maeyr_alpha" / "infrastructure" / "bad.py").write_text(
        "import importlib as _imports\n"
        "from maeyr_alpha.compat import load_attribute as _load_compat_attribute\n\n"
        "service = _load_compat_attribute('services.reconcile', 'service')\n"
        "factory = _imports.import_module('services.factory')\n",
        encoding="utf-8",
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    violations = result["repositories"][0]["dependency_violations"]
    assert {item["target"] for item in violations} == {
        "services.factory",
        "services.reconcile",
    }


def test_literal_loader_registries_cannot_hide_legacy_dependencies(
    tmp_path: Path,
) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    (service / "src" / "maeyr_alpha" / "infrastructure" / "bad.py").write_text(
        "from maeyr_alpha.compat import load_attribute as _load\n\n"
        "_EXPORTS = {'service': ('services.reconcile', 'service')}\n\n"
        "def resolve(name: str) -> object:\n"
        "    module_name, attribute_name = _EXPORTS[name]\n"
        "    return _load(module_name, attribute_name)\n",
        encoding="utf-8",
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    violations = result["repositories"][0]["dependency_violations"]
    assert [item["target"] for item in violations] == ["services.reconcile"]
    assert violations[0]["reason"].startswith("dynamic ")


def test_non_loader_data_maps_are_not_treated_as_module_registries(
    tmp_path: Path,
) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    (service / "src" / "maeyr_alpha" / "infrastructure" / "good.py").write_text(
        "SERVICE_LABELS = {'production': 'services.production'}\n",
        encoding="utf-8",
    )

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    assert result["repositories"][0]["dependency_violations"] == []


def test_detects_cross_repository_exact_behavior(tmp_path: Path) -> None:
    repositories: list[dict[str, str]] = []
    repeated = """
def reconcile(items: list[int]) -> int:
    'Apply the same reconciliation behavior in two services.'
    items = list(items)
    total = 0
    for item in items:
        if item > 0:
            total += item
        else:
            total -= item
    return total
""".lstrip()
    for name in ("alpha", "beta"):
        service = _make_service(tmp_path, f"{name}-service", f"maeyr_{name}")
        (service / "src" / f"maeyr_{name}" / "application" / "reconcile.py").write_text(
            repeated, encoding="utf-8"
        )
        repositories.append({"path": f"{name}-service", "package": f"maeyr_{name}"})

    result = MODULE.verify(tmp_path, _contract(repositories))

    assert result["summary"]["duplicate_function_groups"] == 1
    assert result["summary"]["duplicate_file_groups"] == 1
    assert result["summary"]["canonical_duplicate_function_groups"] == 1
    assert result["summary"]["canonical_duplicate_file_groups"] == 1
    assert result["summary"]["passed"] is False


def test_detects_exact_behavior_copied_inside_one_repository(tmp_path: Path) -> None:
    service = _make_service(tmp_path, "alpha-service", "maeyr_alpha")
    repeated = """
def reconcile(items: list[int]) -> int:
    'Substantial repeated behavior.'
    items = list(items)
    total = 0
    for item in items:
        if item > 0:
            total += item
        else:
            total -= item
    return total
""".lstrip()
    application = service / "src" / "maeyr_alpha" / "application"
    (application / "first.py").write_text(repeated, encoding="utf-8")
    (application / "second.py").write_text(repeated, encoding="utf-8")

    result = MODULE.verify(
        tmp_path, _contract([{"path": "alpha-service", "package": "maeyr_alpha"}])
    )

    assert result["summary"]["duplicate_function_groups"] == 1
    assert result["summary"]["duplicate_file_groups"] == 1
    assert result["summary"]["canonical_duplicate_function_groups"] == 1
    assert result["summary"]["canonical_duplicate_file_groups"] == 1
    assert result["summary"]["passed"] is False


def test_configured_nested_runtime_roots_are_audited(tmp_path: Path) -> None:
    service = _make_service(tmp_path, "images-service", "maeyr_images")
    cloud = service / "cloud-runtime"
    secure = service / "secure-runtime"
    cloud.mkdir()
    secure.mkdir()
    repeated = """
def execute(payload: dict[str, object]) -> dict[str, object]:
    'Substantial behavior copied across two independently packaged runtimes.'
    accepted = dict(payload)
    if 'job_id' not in accepted:
        raise ValueError('job_id is required')
    accepted['job_id'] = str(accepted['job_id'])
    accepted['status'] = 'accepted'
    accepted['attempt'] = int(accepted.get('attempt', 0)) + 1
    accepted['durable'] = True
    return accepted
""".lstrip()
    (cloud / "runner.py").write_text(repeated, encoding="utf-8")
    (secure / "runner.py").write_text(repeated, encoding="utf-8")
    repositories: list[dict[str, object]] = [
        {
            "path": "images-service",
            "package": "maeyr_images",
            "legacy_source_roots": ["cloud-runtime", "secure-runtime"],
        }
    ]

    result = MODULE.verify(tmp_path, _contract(repositories))

    repository = result["repositories"][0]
    assert len(repository["legacy_behavior_modules"]) == 2
    assert result["summary"]["duplicate_function_groups"] == 1
    assert result["summary"]["duplicate_file_groups"] == 1
    assert result["summary"]["canonical_duplicate_function_groups"] == 0
    assert result["summary"]["canonical_duplicate_file_groups"] == 0
    assert result["summary"]["passed"] is False


def test_contract_file_is_valid_json() -> None:
    contract_path = SCRIPT.parents[1] / "quality" / "backend-architecture-contract.json"
    value = json.loads(contract_path.read_text(encoding="utf-8"))
    assert value["schema_version"] == 1
    assert len(value["repositories"]) == 14
