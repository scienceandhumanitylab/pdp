import pytest
from expects import *

from pdp.pdp import PDP
from pdp.utils import (
    changed_env_var,
    find_project_root,
    parse_dependency,
    parsing_errors,
)


def test_changed_env_var_mangles_like_input_env_var():
    expect(changed_env_var("my-task")).to(equal("PDP_CHANGED_MY_TASK"))


class TestFindProjectRoot:
    def test_find_project_root(self, fs):
        pdp = PDP("test")
        pdp.initialize()

        root = find_project_root("pdp.yml", start=pdp.project_root)
        assert root == pdp.project_root


class TestParseDependency:
    # Parametrize entry and parsed vars. Run test once for each of argvalues
    @pytest.mark.parametrize(
        ("entry", "parsed"),
        [
            ("clean", ("clean", None, None)),
            ("clean/*.csv", ("clean", "*.csv", None)),
            ("clean:csvs", ("clean", None, "csvs")),
            ("clean/", ("clean", "", None)),
        ],
    )
    def test_parses(self, entry, parsed):
        expect(parse_dependency(entry)).to(equal(parsed))


class TestParsingErrors:
    def test_valid(self):
        expect(parsing_errors("clean/*.csv")).to(equal([]))

    @pytest.mark.parametrize(
        ("entry", "error"),
        [("", "empty pattern"), ("/clean", "should be relative to output")],
    )
    def test_invalid(self, entry, error):
        expect(parsing_errors(entry)).to(contain(contain(error)))
