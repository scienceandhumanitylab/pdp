import os
from pathlib import Path

from ruamel.yaml import YAML

from pdp.task import Task


def write_task_yml(task_dir, *, entrypoint="", depends_on=(), **overrides):
    """Write a valid flat task.yml into task_dir. Compatible with tmp_path"""
    task_dir = Path(task_dir)
    config = {
        "name": task_dir.name,
        "entrypoint": entrypoint,
        "depends_on": list(depends_on),
        **overrides,
    }
    YAML().dump(config, task_dir / "task.yml")


def backdate_task_yml(*tasks: Task) -> None:
    """Make task.yml older than mtimes a test sets"""
    for task in tasks:
        os.utime(task.task_directory / "task.yml", (1, 1))


def touch(path: Path, mtime: float) -> None:
    """Create the file with given mtime"""
    path.touch()
    os.utime(path, (mtime, mtime))


def make_fresh(pdp, task_id, depends_on=(), entrypoint="touch output/ran"):
    """Valid task with an entrypoint that is fresh"""
    task = pdp.create_task(task_id)
    write_task_yml(task_id, entrypoint=entrypoint, depends_on=depends_on)
    backdate_task_yml(task)
    (task.output_folder / "result.csv").write_text("done")
    assert not task.is_stale
    return task


def make_real_task(root: Path, name: str, deps=()) -> Task:
    (root / name).mkdir()
    write_task_yml(root / name, depends_on=deps)
    task = Task(name, root / name)
    task.scaffold()
    backdate_task_yml(task)

    return task
