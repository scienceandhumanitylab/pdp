import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import ANY, call, patch

import pytest
from expects import *
from ruamel.yaml import YAML
from typer.testing import CliRunner

from pdp.cli import app
from pdp.pdp import RunReport
from tests.helpers import backdate_task_yml, make_fresh, touch, write_task_yml


@pytest.fixture
def runner(fs):
    runner = CliRunner(mix_stderr=False)
    _ = runner.invoke(app, ["init", "--name", "test"])

    return runner


def read_config_file(filename):
    return dict(YAML().load(Path(filename)))


def cli(*args, input=None):
    return CliRunner(mix_stderr=False).invoke(app, list(args), input=input)


def run_dagster_dev(returncode=0, args=()):
    with patch("subprocess.Popen") as popen:
        popen.return_value.wait.return_value = returncode
        result = CliRunner(mix_stderr=False).invoke(app, ["dagster", "dev", *args])
    return result, popen


class TestInitCli:
    def test_init_creates_pdp_yaml(self, runner, fs):
        config_dict = read_config_file("/pdp.yml")
        expect(config_dict["name"]).to(equal("test"))
        expect(config_dict["tasks"]).to(equal([]))

    def test_init_scaffolds_tasks_listed_in_pdp_yml(self, fs):
        Path("/pdp.yml").write_text("name: test\ntasks: [hello]\n")

        expect(cli("init", "--name", "test").exit_code).to(equal(0))
        expect(Path("/hello/task.yml").exists()).to(be_true)


class TestCreateCli:
    def test_create_tasks(self, runner, fs):
        _ = runner.invoke(app, ["create", "hello", "world"])

        expect(Path("/hello/input").exists()).to(be_true)
        expect(Path("/hello/output").exists()).to(be_true)
        expect(Path("/hello/src").exists()).to(be_true)

        expect(Path("/world/input").exists()).to(be_true)
        expect(Path("/world/output").exists()).to(be_true)
        expect(Path("/world/src").exists()).to(be_true)

    def test_create_from_inside_task_errors(self, runner, fs):
        _ = runner.invoke(app, ["create", "hello"])

        os.chdir("hello")

        result = runner.invoke(app, ["create", "world"])

        expect(result.exit_code).to(equal(1))
        expect(" ".join(result.stderr.split())).to(
            contain("Cannot create task: tasks can only be created at the project root")
        )
        expect(Path("/hello/world").exists()).to(be_false)
        expect(Path("/hello/input").exists()).to(be_true)

    def test_create_errs_if_creating_task_from_invalid_location(self, runner, fs):
        Path("/not_a_task").mkdir(parents=True, exist_ok=True)
        os.chdir("not_a_task")

        result = runner.invoke(app, ["create", "hello"])

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Cannot create task"))

    def test_create_rejects_invalid_task_name(self, runner, fs):
        bad_name = "bad.task..name"
        result = runner.invoke(app, ["create", bad_name])

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain(f"invalid task name '{bad_name}'"))
        expect(Path(f"/{bad_name}").exists()).to(be_false)


class TestRunCli:
    def test_runs_fresh_task(self, runner, fs):
        result = runner.invoke(app, ["create", "hello"])

        os.chdir("hello")

        write_task_yml("/hello", entrypoint="echo hello")

        mock_result = subprocess.CompletedProcess(
            args=["echo", "hello"], returncode=0, stdout="world\n"
        )

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(app, ["run"])
            mock_run.assert_called_once_with(
                "echo hello", cwd=Path("/hello"), shell=True, check=False, env=ANY
            )
            expect(result.exit_code).to(equal(0))

    def test_runs_whole_project(self, runner, fs):
        result = runner.invoke(app, ["create", "hello"])
        result = runner.invoke(app, ["create", "world"])

        write_task_yml("/hello", entrypoint="echo hello")

        write_task_yml("/world", entrypoint="echo world")

        mock_hello = subprocess.CompletedProcess(
            args=["echo", "hello"], returncode=0, stdout="world\n"
        )

        mock_world = subprocess.CompletedProcess(
            args=["echo", "hello"], returncode=0, stdout="world\n"
        )

        with patch("subprocess.run", side_effect=[mock_hello, mock_world]) as mock_run:
            result = runner.invoke(app, ["run"])
            mock_run.assert_has_calls(
                [
                    call(
                        "echo hello",
                        cwd=Path("/hello"),
                        shell=True,
                        check=False,
                        env=ANY,
                    ),
                    call(
                        "echo world",
                        cwd=Path("/world"),
                        shell=True,
                        check=False,
                        env=ANY,
                    ),
                ]
            )
            expect(result.exit_code).to(equal(0))

    def test_running_unknown_task_throws_error(self, runner, fs):
        result = runner.invoke(app, ["run", "badtask"])

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Task badtask not found"))
        expect(result.exception).to(be_a(SystemExit))

    def test_run_with_invalid_pdp_yml_throws_error(self, runner, fs):
        Path("/pdp.yml").write_text("name: test\ntasks:\n  - bad.name\n")

        result = runner.invoke(app, ["run"])

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Validation failed."))
        expect(result.stderr).to(contain("invalid task name 'bad.name'"))
        expect(result.exception).to(be_a(SystemExit))

    def test_run_reports_failed_and_skipped_tasks(self, import_and_clean_project):
        write_task_yml("import", entrypoint="exit 1")
        write_task_yml("clean", entrypoint="touch output/ran", depends_on=["import"])

        result = CliRunner(mix_stderr=False).invoke(app, ["run"])

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Failed: import"))
        expect(result.stderr).to(contain("Skipped (upstream failed): clean"))

    def test_force_flag_runs_a_fresh_task(self, real_pdp):
        task = real_pdp.create_task("task1")
        write_task_yml("task1", entrypoint="touch output/ran")
        backdate_task_yml(task)
        (task.output_folder / "result.csv").write_text("done")

        result = CliRunner(mix_stderr=False).invoke(app, ["run", "--force"])

        expect(result.exit_code).to(equal(0))
        expect((task.output_folder / "ran").exists()).to(be_true)

    def test_run_prints_fresh_and_entrypointless_tasks(self, real_pdp):
        fresh = real_pdp.create_task("fresh")
        write_task_yml("fresh", entrypoint="touch output/ran")
        backdate_task_yml(fresh)
        (fresh.output_folder / "result.csv").write_text("done")
        real_pdp.create_task("hand")

        result = CliRunner(mix_stderr=False).invoke(app, ["run"])

        expect(result.exit_code).to(equal(1))
        expect(result.stdout).to(contain("Fresh: fresh"))
        expect(result.stderr).to(contain("No entrypoint (failed): hand"))

    def test_run_warns_when_success_wrote_no_output(self, real_pdp):
        """Cover cases where entrypoint only prints, or writes outside of project"""
        real_pdp.create_task("task1")
        write_task_yml("task1", entrypoint="true")

        result = CliRunner(mix_stderr=False).invoke(app, ["run"])

        expect(result.exit_code).to(equal(0))
        expect(result.stderr).to(contain("Ran but wrote nothing to output/: task1"))

    def test_run_prints_tasks_that_ran(self, import_and_clean_project):
        write_task_yml("import", entrypoint="touch output/data.csv")
        write_task_yml("clean", entrypoint="touch output/ran", depends_on=["import"])

        result = CliRunner(mix_stderr=False).invoke(app, ["run"])

        expect(result.stdout).to(contain("Ran: import, clean"))

    def test_run_recreates_missing_output_folder(self, real_pdp):
        """Git drops empty output/ folders when a task is restored"""
        task = real_pdp.create_task("task1")
        write_task_yml("task1", entrypoint="touch output/ran")
        task.output_folder.rmdir()

        result = CliRunner(mix_stderr=False).invoke(app, ["run"])

        expect(result.exit_code).to(equal(0))
        expect((task.output_folder / "ran").exists()).to(be_true)


class TestRunSelection:
    def tasks_that_ran(self, project):
        return {t.task_id for t in project.tasks if (t.output_folder / "ran").exists()}

    @pytest.mark.parametrize(
        "args, ran",
        [
            (["import", "other", "--only"], {"import", "other"}),
            (["--from", "import"], {"import", "clean", "report"}),
            (["--to", "clean"], {"import", "clean"}),
            (["clean", "--only", "--allow-stale"], {"clean"}),
        ],
    )
    def test_runs_selection(self, stale_dag, args, ran):
        result = CliRunner(mix_stderr=False).invoke(app, ["run", *args])

        expect(result.exit_code).to(equal(0))
        expect(self.tasks_that_ran(stale_dag)).to(equal(ran))

    def test_prints_files_written(self, stale_dag):
        result = CliRunner(mix_stderr=False).invoke(app, ["run", "--to", "clean"])

        expect(result.stdout).to(
            contain("Wrote:\n  import/output/ran\n  clean/output/ran\n")
        )

    @pytest.mark.parametrize(
        "args",
        [["clean", "--only"], ["--from", "clean"], ["clean", "--only", "--force"]],
    )
    def test_refuses_stale_upstream(self, stale_dag, args):
        result = CliRunner(mix_stderr=False).invoke(app, ["run", *args])

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(
            contain("Stale upstream outside the selection: import")
        )
        expect(result.stderr).to(contain("--allow-stale"))
        expect(self.tasks_that_ran(stale_dag)).to(equal(set()))

    @pytest.mark.usefixtures("stale_dag")
    def test_bad_selection_prints_error(self):
        result = CliRunner(mix_stderr=False).invoke(
            app, ["run", "clean", "--from", "import"]
        )

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("--from/--to"))


@pytest.mark.usefixtures("stale_dag")
class TestRunner:
    def invoke_run(self, *args, report=None):
        with patch(
            "pdp.dagster_run.run_dagster", return_value=report or RunReport()
        ) as run_dagster:
            result = CliRunner(mix_stderr=False).invoke(app, ["run", *args])
        return result, run_dagster

    @pytest.mark.parametrize(
        "pdp_yml, args, selection, flags",
        [
            (True, ["clean"], {"import", "clean"}, {}),
            (
                False,
                ["--runner", "dagster", "import", "other", "--only", "-f", "-v"],
                {"import", "other"},
                {"force": True, "verbose": True},
            ),
            (
                False,
                ["--runner", "dagster", "--from", "clean", "--allow-stale"],
                {"clean", "report"},
                {},
            ),
        ],
        ids=["pdp_yml_runner", "only_and_flags", "from"],
    )
    def test_selection_and_flags_reach_dagster(
        self, stale_dag, pdp_yml, args, selection, flags
    ):
        if pdp_yml:
            stale_dag.config.update_config_key("runner", "dagster")

        result, run_dagster = self.invoke_run(*args)

        expect(result.exit_code).to(equal(0))
        defaults = {"force": False, "slurm": False, "verbose": False}
        run_dagster.assert_called_once_with(ANY, selection, **(defaults | flags))

    def test_runner_flag_overrides_pdp_yml(self, stale_dag):
        stale_dag.config.update_config_key("runner", "dagster")

        result, run_dagster = self.invoke_run("--runner", "pdp", "clean")

        expect(result.exit_code).to(equal(0))
        run_dagster.assert_not_called()
        expect(Path("clean/output/ran").exists()).to(be_true)

    @pytest.mark.parametrize(
        "args, env_file, slurm",
        [
            (["--slurm"], "", True),
            ([], "PDP_SLURM=1\n", True),
            ([], "PDP_SLURM=0\n", False),
        ],
        ids=["flag", "env_file", "env_file_off"],
    )
    def test_slurm_mode_uses_dagster_with_slurm(self, args, env_file, slurm):
        Path(".env").write_text(env_file)

        result, run_dagster = self.invoke_run(*args)

        expect(result.exit_code).to(equal(0))
        expect(run_dagster.called).to(equal(slurm))
        if slurm:
            expect(run_dagster.call_args.kwargs["slurm"]).to(be_true)

    def test_slurm_with_pdp_runner_is_refused(self):
        result, run_dagster = self.invoke_run("--runner", "pdp", "--slurm")

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("needs the dagster runner"))
        run_dagster.assert_not_called()

    def test_dagster_report_is_printed_with_its_exit_code(self):
        report = RunReport(ran=["import"], failed=["clean"])

        result, _ = self.invoke_run("--runner", "dagster", report=report)

        expect(result.exit_code).to(equal(1))
        expect(result.stdout).to(contain("Ran: import"))
        expect(result.stderr).to(contain("Failed: clean"))

    def test_written_files_are_capped_per_task(self):
        files = [f"import/output/{i:02}.csv" for i in range(12)]
        report = RunReport(ran=["import"], written={"import": files})

        result, _ = self.invoke_run("--runner", "dagster", report=report)

        expect(result.stdout).to(contain("  import/output/09.csv\n"))
        expect(result.stdout).not_to(contain("import/output/10.csv"))
        expect(result.stdout).to(contain("  ... and 2 more in import/output\n"))

    def test_dagster_runner_needs_dagster(self):
        with patch("importlib.util.find_spec", return_value=None):
            result, run_dagster = self.invoke_run("--runner", "dagster")

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Dagster must be installed: `pdp-helper[dagster]`"))
        run_dagster.assert_not_called()


class TestValidateCli:
    def test_validate_exits_zero_and_prints_warnings(self, import_and_clean_project):
        make_fresh(import_and_clean_project, "import")
        # a fresh output with no CSVs, so import/*.csv matches nothing
        Path("import/output/result.csv").replace(Path("import/output/notes.txt"))
        write_task_yml("clean", depends_on=["import/*.csv"])

        result = CliRunner(mix_stderr=False).invoke(app, ["validate"])

        expect(result.exit_code).to(equal(0))
        expect(result.stdout).to(contain("Warning: clean/task.yml: import/*.csv"))
        expect(result.stdout).to(contain("Valid."))


class TestTreeCli:
    def test_tree_enumerates_tasks_as_flat_list(self, runner, fs):
        _ = runner.invoke(app, ["create", "hello", "world"])

        result = runner.invoke(app, ["tree"])

        expect(result.stdout).to(
            equal(
                """test
├── 1. hello
└── 2. world
"""
            )
        )

    def test_tree_shows_dependency_entries_as_written(self, runner, fs):
        _ = runner.invoke(app, ["create", "import", "clean"])
        write_task_yml("/clean", depends_on=["import/*.csv"])

        result = runner.invoke(app, ["tree"])

        expect(result.stdout).to(contain("<- import/*.csv"))


class TestStatusCli:
    def test_status_prints_more_when_verbose_task(self, import_and_clean_project):
        write_task_yml("import", description="Load data")

        result = CliRunner(mix_stderr=False).invoke(app, ["status"])

        expect(result.exit_code).to(equal(0))
        expect(result.stdout).to(contain("Stale"))
        expect(result.stdout).to_not(contain("description: Load data"))

        result = CliRunner(mix_stderr=False).invoke(app, ["status", "-v"])

        expect(result.exit_code).to(equal(0))
        expect(result.stdout).to(contain("status: Stale"))
        expect(result.stdout).to(contain("description: Load data"))
        expect(result.stdout).to(contain("depends on: import"))

    def test_status_json(self, import_and_clean_project):
        write_task_yml("import", description="Load data")

        result = CliRunner(mix_stderr=False).invoke(app, ["status", "--json"])

        expect(result.exit_code).to(equal(0))
        status = json.loads(result.stdout)
        expect(status["project"]).to(equal(import_and_clean_project.project_name))
        expect([t["id"] for t in status["tasks"]]).to(equal(["import", "clean"]))
        imp, clean = status["tasks"]
        expect(imp).to(have_keys(stale=True, depends_on=[], description="Load data"))
        expect(clean).to(have_keys(stale=True, depends_on=["import"], slurm={}))

    def test_status_shows_dependency_entries_as_written(self, import_and_clean_project):
        write_task_yml("clean", depends_on=["import/*.csv"])

        result = CliRunner(mix_stderr=False).invoke(app, ["status", "-v"])

        expect(result.stdout).to(contain("depends on: import/*.csv"))

        # JSON keeps depends_on as Task IDs for tools like pdp-vscode
        result = CliRunner(mix_stderr=False).invoke(app, ["status", "--json"])
        clean = json.loads(result.stdout)["tasks"][1]
        expect(clean).to(
            have_keys(depends_on=["import"], dependency_entries=["import/*.csv"])
        )

    def status_by_id(self):
        result = cli("status", "--json")
        expect(result.exit_code).to(equal(0))
        return {t["id"]: t for t in json.loads(result.stdout)["tasks"]}

    def test_missing_task_folder_is_stale_and_stays_missing(self, other_deleted):
        expect(self.status_by_id()["other"]).to(have_keys(stale=True, depends_on=[]))
        expect(Path("other").exists()).to(be_false)

    def test_depends_on_task_missing_from_pdp_yml(self, dag_project):
        """clean still depends on import, but import is gone from pdp.yml"""
        Path("pdp.yml").write_text("name: test\ntasks: [clean, report, other]\n")

        tasks = self.status_by_id()

        expect(list(tasks)).to(contain_only("clean", "report", "other"))
        expect(list(tasks).index("clean")).to(be_below(list(tasks).index("report")))
        expect(tasks["clean"]).to(have_keys(stale=True, depends_on=["import"]))
        expect(cli("status").exit_code).to(equal(0))

    @pytest.mark.parametrize("command", ["status", "tree"])
    def test_cycle_fails_validation(self, dag_project, command):
        write_task_yml("import", depends_on=["report"])

        result = cli(command)

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Validation failed."))
        expect(result.stderr).to(contain("depends_on contains a cycle."))


class TestDeleteCli:
    """dag_project: import -> clean -> report, plus other"""

    def listed(self):
        return read_config_file("pdp.yml")["tasks"]

    def test_yes_deletes_folder_and_pdp_yml_entry_without_asking(self, dag_project):
        result = cli("delete", "report", "--yes")

        expect(result.exit_code).to(equal(0))
        expect(result.stdout).to_not(contain("Delete task"))
        expect(Path("report").exists()).to(be_false)
        expect(self.listed()).to(equal(["import", "clean", "other"]))

    @pytest.mark.parametrize(
        "answer, exit_code, kept", [("y", 0, False), ("n", 1, True)]
    )
    def test_asks_before_deleting(self, dag_project, answer, exit_code, kept):
        result = cli("delete", "report", input=f"{answer}\n")

        expect(result.exit_code).to(equal(exit_code))
        expect(result.stdout).to(contain("Delete task report"))
        expect(Path("report").exists()).to(equal(kept))
        expect("report" in self.listed()).to(equal(kept))

    def test_missing_folder_still_removes_entry(self, other_deleted):
        expect(cli("delete", "other", "--yes").exit_code).to(equal(0))
        expect(self.listed()).to(equal(["import", "clean", "report"]))

    def test_symlinked_folder_loses_the_link_not_its_target(
        self, dag_project, tmp_path_factory
    ):
        target = tmp_path_factory.mktemp("elsewhere") / "other"
        shutil.move("other", target)
        Path("other").symlink_to(target)

        expect(cli("delete", "other", "--yes").exit_code).to(equal(0))
        expect(Path("other").is_symlink()).to(be_false)
        expect((target / "task.yml").exists()).to(be_true)

    @pytest.mark.parametrize(
        "task, error",
        [("nope", "Task nope not found"), ("import", "depended on by clean")],
    )
    def test_refuses_before_asking(self, dag_project, task, error):
        result = cli("delete", task)

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain(error))
        expect(result.stdout).to_not(contain("Delete task"))
        expect(self.listed()).to(equal(["import", "clean", "report", "other"]))


class TestDagsterCli:
    def test_pdp_dagster_dev_calls_module(self, real_pdp):
        result, popen = run_dagster_dev()
        root = real_pdp.project_root

        popen.assert_called_once_with(
            [sys.executable, "-m", "dagster", "dev", "-m", "pdp.dagster_defs"],
            cwd=root,
            env=ANY,
        )

        env = popen.call_args.kwargs["env"]
        expect(env["PDP_PROJECT_ROOT"]).to(equal(str(root)))
        expect(env["DAGSTER_HOME"]).to(equal(str(root / ".dagster")))
        expect((root / ".dagster").is_dir()).to(be_true)
        expect(result.exit_code).to(equal(0))

    def test_default_dagster_home_gets_config_without_telemetry(self, real_pdp):
        run_dagster_dev()

        config = real_pdp.project_root / ".dagster" / "dagster.yaml"
        expect(config.read_text()).to(equal("telemetry:\n  enabled: false\n"))

    def test_existing_dagster_config_is_kept(self, real_pdp):
        home = real_pdp.project_root / ".dagster"
        home.mkdir()
        (home / "dagster.yaml").write_text("mine: true\n")

        run_dagster_dev()

        expect((home / "dagster.yaml").read_text()).to(equal("mine: true\n"))

    def test_dagster_dev_honors_existing_dagster_home(
        self, real_pdp, monkeypatch, tmp_path_factory
    ):
        elsewhere = tmp_path_factory.mktemp("elsewhere")
        monkeypatch.setenv("DAGSTER_HOME", str(elsewhere))
        _, popen = run_dagster_dev()

        expect(popen.call_args.kwargs["env"]["DAGSTER_HOME"]).to(equal(str(elsewhere)))
        expect(list(elsewhere.iterdir())).to(equal([]))

    def test_dagster_dev_fails_when_run_outside_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result, popen = run_dagster_dev()

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("No project detected"))
        popen.assert_not_called()

    def test_dagster_dev_throws_error_when_dagster_missing(self, real_pdp):
        with patch("importlib.util.find_spec", return_value=None):
            result, popen = run_dagster_dev()

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Dagster must be installed: `pdp-helper[dagster]`"))
        popen.assert_not_called()

    @pytest.mark.usefixtures("real_pdp")
    def test_dagster_dev_slurm_sets_pdp_slurm(self):
        with patch("shutil.which", return_value="/usr/bin/srun"):
            result, popen = run_dagster_dev(args=["--slurm"])

        expect(result.exit_code).to(equal(0))
        expect(popen.call_args.kwargs["env"]["PDP_SLURM"]).to(equal("1"))

    @pytest.mark.usefixtures("real_pdp")
    def test_dagster_dev_slurm_without_srun_fails(self):
        with patch("shutil.which", return_value=None):
            result, popen = run_dagster_dev(args=["--slurm"])

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(
            contain(
                "--slurm needs Slurm's srun on PATH. Run this on a cluster login node"
            )
        )
        popen.assert_not_called()


class TestClean:
    @pytest.fixture
    def cleanable_dag(self, stale_dag):
        """stale_dag where every task has output/ran"""
        for task in stale_dag.tasks:
            write_task_yml(task.task_id, depends_on=task.depends_on)
            (task.output_folder / "ran").touch()
        return stale_dag

    def tasks_with_output(self, project):
        return {t.task_id for t in project.tasks if (t.output_folder / "ran").exists()}

    @pytest.mark.parametrize(
        "args, left",
        [
            ([], set()),
            (["clean"], {"import", "report", "other"}),
            (["--from", "clean"], {"import", "other"}),
            (["--to", "clean"], {"report", "other"}),
        ],
    )
    def test_cleans_selection(self, cleanable_dag, args, left):
        result = CliRunner(mix_stderr=False).invoke(app, ["clean", *args])

        expect(result.exit_code).to(equal(0))
        expect(self.tasks_with_output(cleanable_dag)).to(equal(left))

    def test_missing_task_folder_fails_validation(self, cleanable_dag, other_deleted):
        result = cli("clean", "import")

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Validation failed."))

    @pytest.mark.usefixtures("cleanable_dag")
    def test_prints_what_was_removed_then_nothing_to_clean(self):
        first = CliRunner(mix_stderr=False).invoke(app, ["clean", "import"])
        second = CliRunner(mix_stderr=False).invoke(app, ["clean", "import"])

        expect(first.stdout).to(equal("import: removed output/ran\n"))
        expect(second.stdout).to(equal("Nothing to clean.\n"))


@pytest.mark.usefixtures("fresh_dag")
class TestFreshenCli:
    """fresh_dag: import -> clean -> report, plus other. Outputs dated 100"""

    def freshen(self, *args):
        return CliRunner(mix_stderr=False).invoke(app, ["freshen", *args])

    @pytest.mark.parametrize(
        "args, mtime, code, stdout, stderr",
        [
            (["clean"], 200, 0, "Freshened: clean, report\n", ""),
            (["clean", "--only"], 200, 0, "Freshened: clean\n", "--from report"),
            (["clean", "--only"], 4e9, 1, "", "Still stale: clean"),
        ],
    )
    def test_prints_report(self, args, mtime, code, stdout, stderr):
        touch(Path("clean/src/script.py"), mtime)

        result = self.freshen(*args)

        expect(result.exit_code).to(equal(code))
        expect(result.stdout).to(equal(stdout))
        expect(result.stderr).to(contain(stderr) if stderr else equal(""))

    def test_refusal_prints_reason(self):
        touch(Path("import/src/script.py"), 200)

        result = self.freshen("clean")

        expect(result.exit_code).to(equal(1))
        expect(result.stderr).to(contain("Stale upstream outside the selection"))

    def test_from_inside_task_directory_freshens_it(self):
        touch(Path("clean/src/script.py"), 200)
        os.chdir("clean")

        expect(self.freshen().stdout).to(contain("Freshened: clean"))

    def test_all_fresh_prints_nothing_to_freshen(self):
        expect(self.freshen().stdout).to(equal("Nothing to freshen.\n"))
