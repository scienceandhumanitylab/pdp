from expects import *

from pdp.pdp import PDP
from pdp.utils import find_project_root, parse_dependency, parsing_errors


class TestFindProjectRoot:
    def test_find_project_root(self, fs):
        pdp = PDP("test")
        pdp.initialize()

        root = find_project_root("pdp.yml", start=pdp.project_root)
        assert root == pdp.project_root


class TestParseDependency:
    def test_task(self):
        expect(parse_dependency("clean")).to(equal(("clean", None, None)))

    def test_wildcard(self):
        expect(parse_dependency("clean/*.csv")).to(equal(("clean", "*.csv", None)))

    def test_group(self):
        expect(parse_dependency("clean:csvs")).to(equal(("clean", None, "csvs")))

    def test_slash_empty(self):
        expect(parse_dependency("clean/")).to(equal(("clean", "", None)))


class TestParsingErrors:
    def test_valid(self):
        expect(parsing_errors("clean/*.csv")).to(equal([]))
        
    def test_empty(self):
        expect(parsing_errors("")).to(contain(contain("empty pattern")))

    def test_slash(self):
        expect(parsing_errors("/clean")).to(contain(contain("should be relative to output")))