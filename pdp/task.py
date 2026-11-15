from __future__ import annotations

import os
import shutil
import subprocess
import warnings
from collections.abc import Collection
from pathlib import Path

from .pdp_config import TaskConfig
from .utils import find_project_root, input_env_var, parse_dependency


def latest_mtime_in_dir(dir: Path, skip: Collection[str] = ()):
    mtimes = []
    for root, dirs, files in os.walk(dir):
        if root == os.fspath(dir):
            dirs[:] = [d for d in dirs if d not in skip]
            files = [f for f in files if f not in skip]
        for file in files:
            path = os.path.join(root, file)
            if os.path.exists(path):  # follows file symlinks, skips broken ones
                mtimes.append(os.path.getmtime(path))

    return max(mtimes, default=None)


def _pdp_owns(entry: Path, task_ids: Collection[str], root: Path) -> bool:
    """Track what pdp owns so that we can prune symlinks, manage staleness, and validate"""
    if entry.name not in task_ids:
        return False
    if entry.is_symlink():
        return entry.resolve().is_relative_to(root)
    if not entry.is_dir():
        return False

    for dirpath, dirs, files in os.walk(entry):
        for name in files + [d for d in dirs if Path(dirpath, d).is_symlink()]:
            link = Path(dirpath, name)
            if not (link.is_symlink() and link.resolve().is_relative_to(root)):
                return False
    return True


class Task:
    def __init__(self, task_name: str, task_directory: str | Path):
        self.task_name = task_name
        self.task_directory = Path(task_directory).resolve()
        self.task_config = TaskConfig(task_name, self.task_directory / "task.yml")
        self.input_folder = self.task_directory / "input"
        self.output_folder = self.task_directory / "output"
        self.src_folder = self.task_directory / "src"
        self.hand_folder = None

    def scaffold(self):
        self.task_directory.mkdir(parents=True, exist_ok=True)

        self.task_config.initialize()

        self.input_folder.mkdir(parents=True, exist_ok=True)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.src_folder.mkdir(parents=True, exist_ok=True)

    def create_symlinks(self, task_ids: set[str]) -> None:
        """Symlink into task input/ the outputs of dependent tasks.

        First, removes any existing symlinks that PDP created, so links
        to folders that no longer exist are pruned. Only removes what
        is demonstrably its own - symlink, named after task, target inside project.
        Then declares new symlinks.

        Args:
            task_ids: Set of task ids as strings
        """
        root = find_project_root(config_name="pdp.yml", start=self.task_directory)
        for entry in self.input_folder.iterdir():
            if (
                # confirm PDP owns it
                entry.name in task_ids
                and entry.is_symlink()
                and entry.resolve().is_relative_to(root)
            ):
                entry.unlink()

        for dep in self.depends_on:
            try:
                (self.input_folder / dep).symlink_to(Path("..", "..", dep, "output"))
            except OSError as e:  # catch Windows - can't symlink without privilege
                warnings.warn(f"{self.task_id}: could not link input/{dep}: {e}")

    def run(self):
        if not self.entrypoint:
            return 0

        # Create env path to dependency outputs in case symlink fails (Windows, Slurm)
        root = find_project_root(config_name="pdp.yml", start=self.task_directory)
        env = os.environ | {
            input_env_var(dep): str(root / dep / "output") for dep in self.depends_on
        }

        # Snapshot to be able to revert changes
        before = self._output_snapshot()

        # Pass env along with subprocess.
        result = subprocess.run(
            self.entrypoint, check=False, cwd=self.task_directory, shell=True, env=env
        )

        # If failure, undo writes - otherwise it will look fresh when it should be stale
        if result.returncode != 0:
            for path, stat in self._output_snapshot().items():
                if before.get(path) != stat:
                    os.remove(path)

        return result.returncode

    def written_since(self, before: dict[str, tuple[int, int]]) -> list[str]:
        """output/ files created or modified since the `before` snapshot,
        relative to the project root"""
        return sorted(
            os.path.relpath(path, self.task_directory.parent)
            for path, stat in self._output_snapshot().items()
            if before.get(path) != stat
        )

    def clean(self) -> list[Path]:
        """Delete files matching task.yml's clean patterns.

        Matches reached through a symlink (like input/<dep>/) are skipped
        so cleaning never touches another task's files.

        Returns:
            removed: Paths removed, relative to the task directory
        """
        removed = []

        for path in (self.task_directory / "output").iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.is_symlink() or path.exists():
                path.unlink()

            removed.append(path.relative_to(self.task_directory))

        return removed

    def _output_snapshot(self) -> dict[str, tuple[int, int]]:
        """Record mtime_ns and size of every output/ file

        Use this to determine what was already there and undo writes
        without sullying outputs from other successful runs.

        Returns:
            snapshot: Dictionary with paths as keys and mtime_ns and size as values
        """
        snapshot = {}
        for root, _, files in os.walk(self.output_folder):
            for file in files:
                path = os.path.join(root, file)
                stat = os.stat(path, follow_symlinks=False)
                snapshot[path] = (stat.st_mtime_ns, stat.st_size)
        return snapshot

    def validation_errors(self) -> list[str]:
        errors: list[str] = self.task_config.validation_errors()

        for folder in (self.input_folder, self.output_folder, self.src_folder):
            if not folder.is_dir():
                errors.append(f"{folder.name} is missing")

        if self.hand_folder is not None and not self.hand_folder.is_dir():
            errors.append("hand/ folder is missing")

        for dep in self.depends_on:
            link = self.input_folder / dep
            if not link.is_symlink() and link.exists():
                errors.append(
                    f"input/{dep} is a real file or directory, not a dependency link"
                )

        return errors

    def validate(self) -> bool:
        return not self.validation_errors()

    @property
    def entrypoint(self) -> str:
        return self.task_config.entrypoint

    @property
    def dependency_entries(self) -> list:
        """Clean dependency entries, after being parsed"""
        return self.task_config.depends_on

    @property
    def depends_on(self) -> list[str]:
        """Handle errors so that PDP.validation_errors doesn't crash here"""
        return list(
            dict.fromkeys(
                parse_dependency(e)[0]
                for e in self.dependency_entries
                if isinstance(e, str)
            )
        )

    def dependency_files(self, root: Path) -> dict[str, list[Path] | None]:
        """Takes clean dependency entries, returns a dict of dependency files"""
        files: dict[str, set[Path] | None] = {}
        for entry in self.dependency_entries:
            if not isinstance(entry, str):
                continue
            dep, pattern, group = parse_dependency(entry)
            if pattern is None and group is None:
                files[dep] = None
                continue

            matched = files.setdefault(dep, set())
            if matched is None:  # whole task covers this
                continue
            if group is not None:
                groups = Task(dep, root / dep).task_config.output_groups
                if group not in groups:
                    raise ValueError(f"{dep} has no output group {group!r}")
                pattern = groups[group]
            matched.update(
                path
                for path in (root / dep / "output").glob(pattern)
                if path.is_file()
            )

        return {
            dep: None if matched is None else sorted(matched)
            for dep, matched in files.items()
        }

    @property
    def description(self) -> str:
        return self.task_config.description

    def slurm_flags(self, project_defaults: dict) -> list[str]:
        """Convert pdp.yml and task.yml slurm configs into srun flags

        Exclusive flags should be added as `exclusive-flag: true`,
        which becomes `--exclusive`. Project defaults are overriden
        by task configs"""
        merged = project_defaults | self.task_config.slurm

        flags = []
        for key, value in sorted(merged.items()):
            if value is False:  # Task override of pdp.yml to set as false
                continue
            if value is True:  # Exclusive flags
                flags.append(f"--{key}")
            else:  # mappings
                flags.append(f"--{key}={value}")
        return flags

    def __repr__(self):
        return f"Task({self.task_name}, {self.task_directory})"

    def __eq__(self, other):
        return repr(self) == repr(other)

    @property
    def task_id(self) -> str:
        """Without subtasks, task_id is just the directory name.
        Revisit when subtasks come back."""
        return self.task_directory.name

    @property
    def is_stale(self) -> bool:
        """Task is stale if it has a stale task dependency, if outputs are missing,
        or if outputs are older than the rest of the task"""

        # stale dependency
        root = find_project_root("pdp.yml", start=self.task_directory)
        if any(Task(dep, root / dep).is_stale for dep in self.depends_on):
            return True

        # missing outputs (no folder, empty, or no readable files)
        task_mtime = latest_mtime_in_dir(self.output_folder)
        if task_mtime is None:
            return True

        # Check outputs older than the files each dependency takes
        for dep, files in self.dependency_files(root).items():
            if files is None:
                mtime = latest_mtime_in_dir(root / dep / "output")
            elif not files:
                return True
            else:
                mtime = max(file.stat().st_mtime for file in files)
            if mtime is not None and mtime > task_mtime:
                return True

        return self._own_files_newer_than(task_mtime, root)

    def _own_files_newer_than(self, mtime: float, root: Path) -> bool:
        """Any file the task owns that are newer than mtime."""
        links = [
            dep
            for dep in self.depends_on
            if _pdp_owns(self.input_folder / dep, self.depends_on, root)
        ]
        for folder_mtime in (
            latest_mtime_in_dir(self.src_folder),
            latest_mtime_in_dir(self.input_folder, skip=links),
            latest_mtime_in_dir(self.task_directory / "hand"),
        ):
            if folder_mtime is not None and folder_mtime > mtime:
                return True

        # Check files at task root. Skip hidden files, swaps
        for file in self.task_directory.iterdir():
            if (
                file.is_file()
                and not file.name.startswith(".")
                and file.stat().st_mtime > mtime
            ):
                return True

        return False
