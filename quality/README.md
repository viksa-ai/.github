# Fleet architecture evidence

The architecture contract is complemented by
[`application-modernization-evidence.md`](application-modernization-evidence.md),
which records the application security, recovery, frontend, packaging, and
claim boundaries for this modernization wave.

The latest committed machine-readable zero-violation snapshot is
[`backend-architecture-evidence.json`](backend-architecture-evidence.json).

This directory defines the acceptance contract for the Python backend fleet. It
is deliberately independent of an individual service so every repository is
judged by the same rules.

The canonical package shape is:

```text
src/<service_package>/
  domain/          # framework-free entities, values, events, policies, errors
  application/     # commands, queries, handlers, and orchestration
  ports/           # Protocol-based inbound and outbound boundaries
  infrastructure/  # persistence, messaging, HTTP and platform adapters
  entrypoints/     # FastAPI, worker, CLI and event-delivery adapters
  bootstrap/       # the only composition root for concrete dependencies
```

`app.py` and old top-level packages may remain only as compatibility facades
during migration. New behavior belongs under `src/`. The dependency direction
is domain <- application/ports <- infrastructure/entrypoints <- bootstrap.
Domain code cannot import a framework. Application and ports cannot import an
adapter or the service compatibility namespace. Entrypoints cannot construct
repositories, clients, or services.

Run the contract from this repository:

```bash
python3 scripts/verify_backend_architecture.py \
  --workspace /path/to/flagship \
  --json-out architecture-evidence.json
```

The command exits non-zero until every backend has the canonical layout,
strict Mypy configuration, no forbidden layer edges, no resource construction
outside its composition root, no behavior-owning module in the legacy source
directories, and no meaningful exact production duplication within or between
services.
Legacy modules may remain only as import-only compatibility facades. The JSON
output is suitable for CI artifacts and release evidence. It never changes a
service.

The report exposes both fleet-wide duplication and the subset touching a
canonical `src/<service_package>` tree. The
`canonical_duplicate_function_groups` and
`canonical_duplicate_file_groups` counters make it possible to keep newly
migrated code at exact-zero while historical copies are still visible in the
global counters. Canonical zero is a migration invariant, not a waiver: the
command still exits non-zero until the historical global counts also reach
zero.

The duplicate detector intentionally ignores tiny functions and package marker
files. It hashes normalized AST for functions with substantial behavior and
hashes production files of at least ten non-blank lines, including copies in
the same repository. Generated code,
migrations, tests, build output, caches and virtual environments are excluded.
Repositories that package more than one runtime must declare every nested
runtime in `legacy_source_roots`; those roots receive the same legacy-behavior
and duplication checks as conventional top-level source directories. This
prevents independently packaged workers from disappearing from fleet evidence.
