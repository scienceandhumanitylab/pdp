# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Releasing

When merging to main:

1. Bump version with `uv version --bump <major|minor|patch|alpha>`. This updates
   `pyproject.toml` and locks.
2. Retitle `## [Unreleased]` to `## [<version>] - YYYY-MM-DD` and start a fresh
   `## [Unreleased]` above it.
3. Commit `pyproject.toml`, `uv.lock` and `CHANGELOG.md` in their own commit and
   merge to main
4. Tag with `git tag -a v<version>-m "Release x.x.x"`, then
   `git push origin main --follow-tags`.

## [Unreleased]

### Added

- In addition to referencing an entire task in `depends_on`, you can reference
  specific files, or multiple files with a wildcard pattern (`import/*.csv`), or
  a named output group (`import:csvs`). In either new case, staleness is only
  determined by the files referenced.
- `PDP_CHANGED_<ID>` is made available to every entrypoint with a dependency,
  with an entry for every dependency file changed since the last run. This makes
  it possible (but optional) to pull the env var in a source file and use it to
  rerun only parts of the task.
- `PDP_CHANGED` lists a task's own `input/` files that changed since the last
  run. Tasks that loop over raw input files can rerun only those, just like with
  `PDP_CHANGED_<ID>` for tasks with a dependency.
- `pdp validate` warns when a `depends_on` pattern matches no files while the
  dependency it references is fresh.
- `pdp freshen` makes stale tasks fresh without running them by setting their
  output file times to now. Bit hacky, but good for a change you know doesn't
  affect results, a comment in a `src/` file, and for generally making it easier
  to force the framework to your will when needed. `pdp freshen <task>` also
  freshens everything downstream of the task, not its upstream.
- `pdp delete <task>` removes a task's dir and its entry in `pdp.yml`. It asks
  for confirmation and refuses while another task depends on it.
- `pdp scaf` is a shortcut for `pdp scaffold`.

### Changed

- Loading a project no longer scaffolds every task in `pdp.yml`. This way, a
  deleted task folder stays deleted instead of coming back on next command.
  `pdp status` lists it as stale. `pdp scaffold` recreates it.
- `pdp tree` and `pdp status` show `depends_on` entries as written to reflect
  files and globs (`clean <- import/*.csv`). `pdp status --json` keeps
  `depends_on` as Task IDs and adds `dependency_entries` field with raw entries.
- Terminology for "current" task changed to "fresh" throughout for clarity.

### Fixed

- `pdp status` and `pdp tree` no longer crash when a `depends_on` entry names an
  invalid task or a task missing from `pdp.yml`. `pdp validate` reports the
  discrepancy. A dependency cycle makes `pdp status` exit 1 with the
  `pdp validate` message.
- A task missing its `output/` folder no longer fails validation. `pdp run`
  creates it.
- `pdp s` runs `pdp status`, no more conflict with `pdp scaffold`.

## [0.2.4] - 2026-11-02

### Added

- `pdp status --json` and `pdp remote status --json` print machine-readable JSON
  for integrated tools.

### Changed

- Small changes to README.md for ease of copying code chunks.

## [0.2.3] - 2026-10-28

### Added

- `pdp run` accepts several task IDs and selection flags:
  - `--only` runs the given tasks without their upstream.
  - `--from <task>` runs a task and its downstream.
  - `--to <task>` runs a task and its upstream. With `--from`, runs every task
    on a path between the two.
  - `pdp run --runner [pdp|dagster]` and `runner:` in `pdp.yml` choose the
    runner.
  - `pdp run --slurm` runs each task as a Slurm job through Dagster.
- `pdp run` refuses to run when a selected task depends on a stale task outside
  the selection. `--allow-stale` runs anyway.
- `pdp remote run` runs a selection of tasks on the cluster with `--slurm`
- `pdp remote status` shows the run, `pdp remote push` and `pdp remote pull`
  refuse while it runs and `pdp remote stop` stops it.
- `pdp run` lists the `output/` files each task created or modified, up to 10
  per task.
- `pdp clean` deletes files listed under `clean:` in each `task.yml`
- Added `CONTRIBUTING.md`
- Templates for bug reports, feature requests, and pull requests

### Changed

- `pdp run` no longer lists failed tasks under "Ran".
- `pdp dagster dev` writes a `dagster.yaml` that turns off Dagster telemetry

## [0.2.2] - 2026-10-26

### Added

- `pdp dagster dev --slurm` runs from the cluster login node and sets each task
  as Slurm job. Can run concurrently when PDP structure allows for it. Configure
  with `slurm` block in `pdp.yml` or `task.yml`. Tasks override project
  configuration.
- `pdp dagster dev -p/--port` sets the UI port.
- Remote commands:
  - `pdp remote start` - connect to cluster, push project, and start dagster dev
  - `pdp remote push` - push project to cluster
  - `pdp remote pull` - pull outputs from cluster to local
  - `pdp remote stop` - kill dagster and tmux session on remote
- Added `pdp status` and `pdp remote status`
- Added command shortcuts: `-h` for `--help`, `s` for `status`

## [0.2.1] - 2026-10-18

### Added

- `pdp dagster dev` starts local Dagster UI. Dagster assets are created from PDP
  tasks.
- Optional dependencies for dagster and dagster-webserver
- When Dagster loads, tasks with outputs that changed outside of Dagster are
  recorded as asset observations so staleness carries through and only necessary
  tasks are run.
- Dependencies show on `pdp tree` output

### Changed

- A task without an entrypoint now fails the run. This may be revisited when
  subtasks are reintroduced.

## [0.2.0] - 2026-10-04

### Added

- Added `task.is_stale()` based on mtimes of task dirs and staleness of task
  dependencies.
- Added `TopologicalSorter()` to `pdp.run_all()` and `pdp.run_task()` to
  determine task order
- Added `task.depends_on()` for task dependencies
- Dependency symlinks whole `task1/output/` directory to `task2/input/` when
  listed under `depends_on()`
- `PDP_INPUT_<DEP>` env vars set for entrypoints as fallback when symlinks fail
  (i.e. Windows)
- Failure handling:
  - On failed task, downstream tasks are skipped
  - On failed task, unrelated tasks still run
  - Snapshot is taken before task run so that on failure, `pdp` removes what it
    wrote in that run, leaving other outputs untouched.
- Runs create `RunReport`, which prints tasks run successfully, tasks failed,
  and tasks current (i.e. did not need to run)
- Added `--force` flag to invalidate outputs and run all tasks regardless of
  staleness

### Changed

- Switched from poetry to uv, build backend is now `uv_build`. Install with
  `uv sync`, run with `uv run`.

### Removed

- Removed hierarchical subtasks temporarily. Will return sometime.
- Dropped the `graphlib` dependency and moved `pytest-cov` out of the runtime
  dependencies into the dev group.
