#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 PATH_TO_RELEASE_WORKFLOW" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

python3 - "$1" "${repo_root}" <<'PY'
from __future__ import annotations

import copy
import pathlib
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)


class WorkflowValidationError(ValueError):
    pass


MANDATORY_JOBS = frozenset(
    {"backend", "frontend", "containers", "end-to-end", "deployment-validation"}
)
OPTIONAL_DEPLOYMENT_JOB = "deploy-vercel-frontend"
PUBLISH_REF_CONDITION = (
    "github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')"
)
COMPONENT_CONDITION = (
    "startsWith(github.ref, 'refs/tags/v') || matrix.component == 'backend'"
)
SYNTHETIC_PASSWORD = "task24-synthetic"
CI_DATABASE = "apex_e2e_ci"
SYNC_DATABASE_URL = (
    f"postgresql://apex:{SYNTHETIC_PASSWORD}@localhost:5432/{CI_DATABASE}"
)
ASYNC_DATABASE_URL = (
    f"postgresql+asyncpg://apex:{SYNTHETIC_PASSWORD}@localhost:5432/{CI_DATABASE}"
)


def load_workflow(text: str) -> Mapping[str, Any]:
    try:
        loaded = yaml.load(text, Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise WorkflowValidationError(f"invalid or ambiguous YAML: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise WorkflowValidationError("workflow root must be a mapping")
    return loaded


def normalize_needs(value: object, *, job_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if all(isinstance(item, str) for item in value):
            return tuple(value)
    raise WorkflowValidationError(
        f"job {job_id!r} needs must be a job id or a sequence of job ids"
    )


def require_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise WorkflowValidationError(f"{label} must be a string-keyed mapping")
    return value


def named_steps(job: Mapping[str, Any], *, job_id: str) -> Mapping[str, Mapping[str, Any]]:
    steps = job.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        raise WorkflowValidationError(f"job {job_id!r} steps must be a sequence")
    result: dict[str, Mapping[str, Any]] = {}
    for step in steps:
        item = require_mapping(step, label=f"job {job_id!r} step")
        name = item.get("name")
        if isinstance(name, str):
            if name in result:
                raise WorkflowValidationError(
                    f"job {job_id!r} has duplicate step name {name!r}"
                )
            result[name] = item
    return result


def validate_workflow(data: Mapping[str, Any], repo_root: pathlib.Path) -> None:
    jobs = require_mapping(data.get("jobs"), label="jobs")
    for job_id, job in jobs.items():
        require_mapping(job, label=f"job {job_id!r}")

    missing_jobs = (MANDATORY_JOBS | {OPTIONAL_DEPLOYMENT_JOB, "publish"}) - jobs.keys()
    if missing_jobs:
        raise WorkflowValidationError(
            f"workflow is missing required job definitions: {sorted(missing_jobs)}"
        )

    dependency_graph: dict[str, tuple[str, ...]] = {}
    for job_id, raw_job in jobs.items():
        job = require_mapping(raw_job, label=f"job {job_id!r}")
        dependency_graph[job_id] = normalize_needs(job.get("needs"), job_id=job_id)
    nonexistent = {
        dependency
        for dependencies in dependency_graph.values()
        for dependency in dependencies
        if dependency not in jobs
    }
    if nonexistent:
        raise WorkflowValidationError(
            f"workflow references nonexistent dependencies: {sorted(nonexistent)}"
        )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(job_id: str) -> None:
        if job_id in visiting:
            raise WorkflowValidationError(f"dependency cycle reaches job {job_id!r}")
        if job_id in visited:
            return
        visiting.add(job_id)
        for dependency in dependency_graph[job_id]:
            visit(dependency)
        visiting.remove(job_id)
        visited.add(job_id)

    for job_id in jobs:
        visit(job_id)

    publish = require_mapping(jobs["publish"], label="publish job")
    publish_needs = frozenset(dependency_graph["publish"])
    if len(dependency_graph["publish"]) != len(publish_needs):
        raise WorkflowValidationError("publish needs contains duplicate job ids")
    if OPTIONAL_DEPLOYMENT_JOB in publish_needs:
        raise WorkflowValidationError("optional deployment must not gate publish")
    if publish_needs != MANDATORY_JOBS:
        raise WorkflowValidationError(
            "publish must need exactly the mandatory jobs: "
            f"expected {sorted(MANDATORY_JOBS)}, got {sorted(publish_needs)}"
        )

    for mandatory in MANDATORY_JOBS:
        stack = list(dependency_graph[mandatory])
        ancestors: set[str] = set()
        while stack:
            dependency = stack.pop()
            if dependency in ancestors:
                continue
            ancestors.add(dependency)
            stack.extend(dependency_graph[dependency])
        if OPTIONAL_DEPLOYMENT_JOB in ancestors:
            raise WorkflowValidationError(
                f"mandatory job {mandatory!r} transitively depends on optional deployment"
            )
        mandatory_job = require_mapping(jobs[mandatory], label=f"job {mandatory!r}")
        if mandatory_job.get("continue-on-error") not in (None, False):
            raise WorkflowValidationError(
                f"mandatory job {mandatory!r} must fail closed"
            )

    publish_condition = publish.get("if")
    if not isinstance(publish_condition, str):
        raise WorkflowValidationError("publish if must be a string expression")
    if publish_condition.strip() != PUBLISH_REF_CONDITION:
        raise WorkflowValidationError(
            "publish must preserve the main/tag condition without a status override"
        )
    if re.search(r"\b(always|cancelled|failure)\s*\(", publish_condition):
        raise WorkflowValidationError(
            "publish condition must not bypass failed or cancelled mandatory jobs"
        )

    strategy = require_mapping(publish.get("strategy"), label="publish strategy")
    matrix = require_mapping(strategy.get("matrix"), label="publish matrix")
    include = matrix.get("include")
    if not isinstance(include, Sequence) or isinstance(include, (str, bytes)):
        raise WorkflowValidationError("publish matrix include must be a sequence")
    components: dict[str, Mapping[str, Any]] = {}
    for raw_row in include:
        row = require_mapping(raw_row, label="publish matrix row")
        component = row.get("component")
        if not isinstance(component, str) or component in components:
            raise WorkflowValidationError(
                "publish matrix component must be a unique string"
            )
        components[component] = row
    if components.keys() != {"backend", "frontend"}:
        raise WorkflowValidationError(
            f"publish matrix components must be backend/frontend, got {sorted(components)}"
        )
    expected_builds = {
        "backend": ("backend", "backend/Dockerfile"),
        "frontend": ("frontend", "frontend/Dockerfile"),
    }
    for component, (expected_context, expected_dockerfile) in expected_builds.items():
        row = components[component]
        context = row.get("context")
        dockerfile = row.get("dockerfile")
        if context != expected_context or dockerfile != expected_dockerfile:
            raise WorkflowValidationError(
                f"{component} publish build must use context={expected_context!r} "
                f"and workspace-relative dockerfile={expected_dockerfile!r}"
            )
        if not (repo_root / expected_context).is_dir():
            raise WorkflowValidationError(f"missing build context: {expected_context}")
        if not (repo_root / expected_dockerfile).is_file():
            raise WorkflowValidationError(f"missing Dockerfile: {expected_dockerfile}")

    publish_steps = named_steps(publish, job_id="publish")
    expected_policy_steps = (
        "Sign in to GitHub Container Registry",
        "Generate container metadata",
    )
    for name in expected_policy_steps:
        step = publish_steps.get(name)
        if step is None or step.get("if") != COMPONENT_CONDITION:
            raise WorkflowValidationError(
                f"publish step {name!r} must preserve the backend-on-main/all-on-tag policy"
            )
    image_steps = [
        step
        for step in publish_steps.values()
        if isinstance(step.get("name"), str)
        and str(step["name"]).startswith("Publish ")
    ]
    if len(image_steps) != 1 or image_steps[0].get("if") != COMPONENT_CONDITION:
        raise WorkflowValidationError(
            "image publish step must preserve the backend-on-main/all-on-tag policy"
        )
    build_step = image_steps[0]
    if build_step.get("uses") != "docker/build-push-action@v7":
        raise WorkflowValidationError("publish must use docker/build-push-action@v7")
    build_inputs = require_mapping(build_step.get("with"), label="publish build inputs")
    if build_inputs.get("context") != "${{ matrix.context }}":
        raise WorkflowValidationError("publish build context must come from the matrix")
    if build_inputs.get("file") != "${{ matrix.dockerfile }}":
        raise WorkflowValidationError("publish Dockerfile must come from the matrix")
    if build_inputs.get("push") is not True:
        raise WorkflowValidationError("publish build must push images")

    backend = require_mapping(jobs["backend"], label="backend job")
    backend_env = require_mapping(backend.get("env"), label="backend env")
    expected_backend_env = {
        "APP_ENV": "test",
        "DATABASE_URL": SYNC_DATABASE_URL,
        "POSTGRES_PASSWORD": SYNTHETIC_PASSWORD,
        "REDIS_URL": "redis://localhost:6379/15",
        "TEST_REPLAY_POSTGRES_URL": ASYNC_DATABASE_URL,
        "TEST_INGESTION_POSTGRES_URL": ASYNC_DATABASE_URL,
        "TEST_E2E_DATABASE_URL": SYNC_DATABASE_URL,
    }
    for key, expected in expected_backend_env.items():
        if backend_env.get(key) != expected:
            raise WorkflowValidationError(
                f"backend env {key} must equal the synthetic CI value {expected!r}"
            )
    if "LIVE_REPAIR_INTEGRATION" in backend_env:
        raise WorkflowValidationError(
            "destructive live SQL/Redis integration must remain opt-in outside this CI service"
        )
    services = require_mapping(backend.get("services"), label="backend services")
    postgres = require_mapping(services.get("postgres"), label="backend postgres service")
    if postgres.get("image") != "postgres:17-alpine":
        raise WorkflowValidationError("backend PostgreSQL service must use postgres:17-alpine")
    postgres_env = require_mapping(postgres.get("env"), label="backend postgres env")
    expected_postgres_env = {
        "POSTGRES_DB": CI_DATABASE,
        "POSTGRES_USER": "apex",
        "POSTGRES_PASSWORD": SYNTHETIC_PASSWORD,
    }
    for key, expected in expected_postgres_env.items():
        if postgres_env.get(key) != expected:
            raise WorkflowValidationError(
                f"backend postgres env {key} must equal {expected!r}"
            )
    ports = postgres.get("ports")
    if not isinstance(ports, Sequence) or isinstance(ports, (str, bytes)):
        raise WorkflowValidationError("backend postgres ports must be a sequence")
    if "5432:5432" not in {str(port) for port in ports}:
        raise WorkflowValidationError("backend PostgreSQL must publish localhost port 5432")
    options = postgres.get("options")
    if not isinstance(options, str) or "pg_isready" not in options:
        raise WorkflowValidationError("backend PostgreSQL service needs a health check")

    deployment = require_mapping(
        jobs["deployment-validation"], label="deployment-validation job"
    )
    deployment_steps = named_steps(deployment, job_id="deployment-validation")
    managed = deployment_steps.get("Validate managed datastore URL handling", {}).get("run")
    unsafe = deployment_steps.get("Reject unsafe production configuration", {}).get("run")
    roles = deployment_steps.get("Verify process roles start", {}).get("run")
    if not all(isinstance(script, str) for script in (managed, unsafe, roles)):
        raise WorkflowValidationError("deployment Settings validation steps must remain present")
    assert isinstance(managed, str) and isinstance(unsafe, str) and isinstance(roles, str)
    if 'app_process_role="api"' not in managed or "apex_arena_proxy_token=" not in managed:
        raise WorkflowValidationError(
            "managed datastore Settings must explicitly model an authenticated API role"
        )
    if "development_fixture_enabled" in unsafe:
        raise WorkflowValidationError("removed development fixture flag remains in workflow")
    if 'app_process_role="api"' not in unsafe or "apex_arena_proxy_token=" not in unsafe:
        raise WorkflowValidationError(
            "unsafe production base must explicitly model an authenticated API role"
        )
    if "app_process_role=role" not in roles or "apex_arena_proxy_token=" not in roles:
        raise WorkflowValidationError(
            "process-role Settings must provide the deployed API proxy token"
        )
    for name, script in (
        ("managed datastore", managed),
        ("unsafe production", unsafe),
        ("process roles", roles),
    ):
        if "_env_file=None" not in script:
            raise WorkflowValidationError(
                f"{name} Settings validation must disable dotenv loading"
            )
    checker = deployment_steps.get("Validate release workflow invariants", {}).get("run")
    if checker != "scripts/test-release-workflow.sh .github/workflows/release.yml":
        raise WorkflowValidationError(
            "deployment-validation must execute the release workflow regression script"
        )


def expect_invalid(
    valid: Mapping[str, Any],
    repo_root: pathlib.Path,
    mutate: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    candidate = copy.deepcopy(valid)
    mutate(candidate)
    try:
        validate_workflow(candidate, repo_root)
    except WorkflowValidationError as exc:
        if expected not in str(exc):
            raise AssertionError(
                f"mutation failed for the wrong reason: expected {expected!r}, got {exc}"
            ) from exc
        return
    raise AssertionError(f"validator accepted invalid mutation: expected {expected!r}")


workflow_path = pathlib.Path(sys.argv[1]).resolve()
repository = pathlib.Path(sys.argv[2]).resolve()
if not workflow_path.is_file():
    raise SystemExit(f"release workflow does not exist: {workflow_path}")

try:
    workflow = load_workflow(workflow_path.read_text(encoding="utf-8"))
    validate_workflow(workflow, repository)

    alternate = load_workflow(
        "jobs:\n"
        "  first:\n"
        "    steps: []\n"
        "  second:\n"
        "    needs:\n"
        "      - first\n"
        "    steps: []\n"
    )
    alternate_jobs = require_mapping(alternate["jobs"], label="alternate jobs")
    second = require_mapping(alternate_jobs["second"], label="alternate second job")
    if normalize_needs(second["needs"], job_id="second") != ("first",):
        raise AssertionError("block-list needs syntax was not normalized")
    if normalize_needs("first", job_id="second") != ("first",):
        raise AssertionError("scalar needs syntax was not normalized")
    try:
        load_workflow("jobs:\n  duplicate: {}\n  duplicate: {}\n")
    except WorkflowValidationError as exc:
        if "duplicate key" not in str(exc):
            raise
    else:
        raise AssertionError("duplicate YAML keys were accepted")

    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"].pop("backend"),
        "missing required job",
    )
    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"]["publish"]["needs"].append("not-a-job"),
        "nonexistent dependencies",
    )
    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"]["publish"]["needs"].append(
            OPTIONAL_DEPLOYMENT_JOB
        ),
        "optional deployment must not gate publish",
    )
    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"]["publish"].update(
            {"if": f"always() && ({PUBLISH_REF_CONDITION})"}
        ),
        "status override",
    )
    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"]["publish"].update(
            {"if": f"!cancelled() && ({PUBLISH_REF_CONDITION})"}
        ),
        "status override",
    )
    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"]["backend"].update({"continue-on-error": True}),
        "must fail closed",
    )
    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"]["containers"].update({"needs": {"backend": True}}),
        "needs must be",
    )
    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"]["publish"]["strategy"]["matrix"].update(
            {"include": {"component": "backend"}}
        ),
        "matrix include must be a sequence",
    )
    expect_invalid(
        workflow,
        repository,
        lambda data: data["jobs"]["publish"]["strategy"]["matrix"]["include"][0].update(
            {"dockerfile": "Dockerfile"}
        ),
        "workspace-relative dockerfile",
    )
except (AssertionError, KeyError, TypeError, WorkflowValidationError) as exc:
    print(f"release workflow validation failed: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

print("release workflow invariants OK")
print("publish is reachable only after all mandatory jobs succeed")
print("failed or cancelled mandatory jobs block publish")
print("negative dependency, status-bypass, YAML-shape, and Dockerfile mutations rejected")
PY
