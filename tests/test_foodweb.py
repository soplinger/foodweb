import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import foodweb  # noqa: E402
from foodweb import (  # noqa: E402
    connectance,
    energy_path,
    food_chains,
    gate_failures,
    keystone_ranking,
    lint,
    loop_species,
    omnivory,
    parse,
    secondary_extinctions,
    stats,
    to_dot,
    trophic_levels,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "lake.web"


def web_from(text):
    web, issues = parse(text)
    assert issues == [], issues
    return web


def write(tmp_path, text, name="web.web"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_parse_builds_species_links_and_normalised_shares():
    web = web_from("a -> b\nx -> b @ 3\nx -> c")
    assert web.species == ["a", "b", "x", "c"]
    assert len(web.links) == 3
    assert web.diet["b"] == pytest.approx({"a": 0.25, "x": 0.75})
    assert web.diet["c"] == pytest.approx({"x": 1.0})
    assert web.eaten_by["x"] == {"b", "c"}


def test_parse_reports_line_numbered_errors_and_keeps_valid_links():
    text = "a -> b\na -> a\na -> b\nc -> d @ -1\nbad line here\n"
    web, issues = parse(text)
    assert [(i.line, i.severity) for i in issues] == [
        (2, "error"), (3, "error"), (4, "error"), (5, "error"),
    ]
    assert "cannot eat itself" in issues[0].message
    assert "first on line 1" in issues[1].message
    assert "positive" in issues[2].message
    assert web.links == [("a", "b", 1.0)]


def test_parse_rejects_bad_species_declarations_and_names():
    _, issues = parse("species a\nspecies a\nspecies bad!name\n")
    assert [i.line for i in issues] == [2, 3]
    assert "already declared on line 1" in issues[0].message


def test_comments_and_blank_lines_are_ignored():
    web, issues = parse("# header\n\na -> b  # trailing note\n")
    assert issues == []
    assert web.links == [("a", "b", 1.0)]


def test_lint_warns_about_declared_species_without_links():
    web, issues = parse("species otter\na -> b\n")
    found = lint(web, issues)
    warnings = [i for i in found if i.severity == "warning"]
    assert len(warnings) == 1
    assert warnings[0].line == 1
    assert "has no links" in warnings[0].message


def test_lint_warns_about_feeding_loops():
    web = web_from("a -> b\nb -> c\nc -> b")
    found = lint(web, [])
    assert any("feeding loop" in i.message for i in found)


def test_trophic_levels_follow_the_chain():
    levels = trophic_levels(web_from("a -> b\nb -> c"))
    assert levels == {"a": 1.0, "b": 2.0, "c": 3.0}


def test_trophic_level_is_diet_weighted():
    levels = trophic_levels(web_from("a -> b\na -> c @ 1\nb -> c @ 3"))
    assert levels["b"] == pytest.approx(2.0)
    assert levels["c"] == pytest.approx(2.75)


def test_feeding_loops_leave_levels_undefined():
    web = web_from("a -> b\nb -> c\nc -> b")
    levels = trophic_levels(web)
    assert levels["a"] == 1.0
    assert levels["b"] is None
    assert levels["c"] is None
    assert loop_species(web) == {"b", "c"}


def test_unlinked_species_has_no_trophic_level():
    web, _ = parse("species otter\na -> b\n")
    assert trophic_levels(web)["otter"] is None


def test_connectance_is_links_over_species_squared():
    assert connectance(web_from("a -> b\nb -> c")) == pytest.approx(2 / 9)


def test_omnivory_is_weighted_variance_of_prey_levels():
    web = web_from("a -> b\na -> c\nb -> c")
    levels = trophic_levels(web)
    assert omnivory(web, levels, "c") == pytest.approx(0.25)
    assert omnivory(web, levels, "b") is None


def test_secondary_extinctions_cascade_up_the_chain():
    web = web_from("a -> b\nb -> c\na -> d")
    assert secondary_extinctions(web, "a") == ["a", "b", "c", "d"]
    assert secondary_extinctions(web, "b") == ["b", "c"]


def test_consumer_with_surviving_prey_does_not_go_extinct():
    web = web_from("a -> c\nb -> c")
    assert secondary_extinctions(web, "a") == ["a"]


def test_keystone_ranking_puts_biggest_cascade_first():
    rows = keystone_ranking(web_from("a -> b\nb -> c\na -> d"))
    assert rows[0] == (3, "a")
    assert rows[1] == (1, "b")


def test_food_chains_list_every_simple_path():
    chains, total = food_chains(web_from("a -> b\na -> c\nb -> d\nc -> d"))
    assert chains == [["a", "b", "d"], ["a", "c", "d"]]
    assert total == 2


def test_food_chain_limit_caps_output_but_keeps_total():
    chains, total = food_chains(web_from("a -> b\na -> c\nb -> d\nc -> d"), limit=1)
    assert len(chains) == 1
    assert total == 2


def test_energy_path_is_shortest_prey_to_predator_route():
    web = web_from("a -> b\na -> c\nb -> d\nc -> d\nc -> e")
    assert energy_path(web, "a", "d") == ["a", "b", "d"]
    assert energy_path(web, "d", "a") is None


def test_dot_output_labels_shared_diets_and_marks_producers():
    dot = to_dot(web_from("a -> b\nc -> b"), trophic_levels(web_from("a -> b\nc -> b")))
    assert dot.startswith("digraph foodweb {")
    assert '"a" -> "b" [label="50%"];' in dot
    assert "#cde8c0" in dot


def test_gate_failures_report_each_broken_limit():
    web = web_from("a -> b\nb -> c\nc -> d")
    failures = gate_failures(web, max_tl=3, min_connectance=0.5)
    assert len(failures) == 2
    assert any("exceeds 3" in f for f in failures)
    assert any("below 0.5" in f for f in failures)
    assert gate_failures(web, max_tl=10, min_connectance=0.1) == []


def test_stats_on_example_web_match_hand_calculation():
    web, issues = parse(EXAMPLE.read_text(encoding="utf-8"))
    assert issues == []
    st = stats(web)
    assert st["species"] == 9
    assert st["links"] == 11
    assert st["connectance"] == pytest.approx(11 / 81, abs=1e-4)
    assert st["levels_defined"] == 8
    assert st["max_trophic_level"] == pytest.approx(4.25)
    assert st["producers"] == ["algae", "detritus"]
    assert st["top_predators"] == ["pike"]
    assert st["loop_species"] == []
    assert st["mean_omnivory"] == pytest.approx(0.0608, abs=1e-4)


def test_cli_lint_exits_1_on_errors_and_prints_file_line(tmp_path, capsys):
    path = write(tmp_path, "a -> b\na -> a\n")
    assert foodweb.main(["lint", path]) == 1
    out = capsys.readouterr().out
    assert f"{path}:2: error:" in out
    assert "1 error(s)" in out


def test_cli_lint_exits_0_on_warnings_only(tmp_path, capsys):
    path = write(tmp_path, "species otter\na -> b\n")
    assert foodweb.main(["lint", path]) == 0
    assert "warning" in capsys.readouterr().out


def test_cli_check_gate_exit_codes(tmp_path, capsys):
    path = write(tmp_path, "a -> b\nb -> c\nc -> d\n")
    assert foodweb.main(["check", path, "--max-tl", "3"]) == 1
    assert "FAIL" in capsys.readouterr().out
    assert foodweb.main(["check", path, "--max-tl", "4"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_levels_json_output(tmp_path, capsys):
    path = write(tmp_path, "a -> b\n")
    assert foodweb.main(["levels", path, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0] == {"species": "a", "trophic_level": 1.0, "prey": [], "predators": ["b"]}
    assert rows[1]["trophic_level"] == 2.0


def test_cli_keystone_json_lists_only_species_with_cascades(tmp_path, capsys):
    path = write(tmp_path, "a -> b\nb -> c\na -> d\n")
    assert foodweb.main(["keystone", path, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == [
        {"species": "a", "extra_extinctions": 3},
        {"species": "b", "extra_extinctions": 1},
    ]


def test_cli_missing_file_and_bad_input_exit_2(tmp_path, capsys):
    assert foodweb.main(["stats", str(tmp_path / "nope.web")]) == 2
    bad = write(tmp_path, "this is not a link\n")
    assert foodweb.main(["stats", bad]) == 2
    assert "error" in capsys.readouterr().err
    good = write(tmp_path, "a -> b\n")
    assert foodweb.main(["extinct", good, "zzz"]) == 2


def test_cli_extinct_reports_cascade(tmp_path, capsys):
    path = write(tmp_path, "a -> b\nb -> c\n")
    assert foodweb.main(["extinct", path, "b"]) == 0
    out = capsys.readouterr().out
    assert "causes 2 extinction(s)" in out
    assert "  c" in out


def test_cli_dot_writes_output_file(tmp_path):
    path = write(tmp_path, "a -> b\n")
    out_path = tmp_path / "web.dot"
    assert foodweb.main(["dot", path, "-o", str(out_path)]) == 0
    assert out_path.read_text(encoding="utf-8").startswith("digraph foodweb {")
