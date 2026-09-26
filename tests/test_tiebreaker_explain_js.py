"""static/js/tiebreaker_explain.js -- decisive tiebreaker logic. Pure ES
module driven through Node (skips when Node is missing)."""
import json
import pathlib
import shutil
import subprocess

import pytest

MODULE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "tiebreaker_explain.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(expr: str):
    script = (
        f"import * as m from {json.dumps(MODULE.as_uri())};"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
    )
    return json.loads(out.stdout)


def test_six_tiers_in_cascade_order():
    keys = _run("m.TIERS.map(t => t.key)")
    assert keys == ["tb1", "tb2", "tb3", "tb4", "tb5", "tb6"]


def test_decisive_tier_is_first_differing_tier():
    prev = "{total: 9, tb: [3, 3, 3, 10, 5, 1]}"
    cur = "{total: 9, tb: [3, 2, 4, 20, 5, 1]}"
    assert _run(f"m.decisiveTier({prev}, {cur})") == {"tier": 2, "key": "tb2"}


def test_first_tier_can_decide():
    assert _run("m.decisiveTier({total: 5, tb: [2,0,0,0,0,0]}, {total: 5, tb: [1,9,9,9,9,9]})") == {"tier": 1, "key": "tb1"}


def test_point_differential_tier_decides_when_wins_tiers_equal():
    assert _run("m.decisiveTier({total: 5, tb: [1,1,3,12,0,0]}, {total: 5, tb: [1,1,3,-4,0,0]})") == {"tier": 4, "key": "tb4"}


def test_different_totals_have_no_decisive_tier():
    assert _run("m.decisiveTier({total: 6, tb: [1,1,1,1,1,1]}, {total: 5, tb: [0,0,0,0,0,0]})") is None


def test_fully_identical_tiers_have_no_decisive_tier():
    assert _run("m.decisiveTier({total: 5, tb: [1,1,3,4,0,0]}, {total: 5, tb: [1,1,3,4,0,0]})") is None


def test_explain_names_both_players_and_the_tier():
    text = _run("m.explainTie({total: 5, tb: [1,1,3,12,0,0]}, {total: 5, tb: [1,1,3,-4,0,0]}, 'Ann', 'Bo')")
    assert "Ann" in text and "Bo" in text and "worst team point differential" in text.lower()


def test_explain_unresolved_tie_says_so():
    text = _run("m.explainTie({total: 5, tb: [1,1,3,4,0,0]}, {total: 5, tb: [1,1,3,4,0,0]}, 'Ann', 'Bo')")
    assert "all six" in text.lower()


def test_parse_cell_handles_signed_differentials():
    assert _run("[m.parseCell('+12'), m.parseCell('-3'), m.parseCell('7'), m.parseCell(''), m.parseCell(null)]") == [12, -3, 7, 0, 0]


ENTRIES_MIDDLE_TWO_TIERS = (
    "[{id:'a', name:'Ann', total:5, tb:[2,0,0,0,0,0]},"
    " {id:'b', name:'Bo',  total:5, tb:[1,3,0,0,0,0]},"
    " {id:'c', name:'Cy',  total:5, tb:[1,1,0,0,0,0]}]"
)


def test_three_way_tie_middle_player_carries_two_tiers():
    hits = _run(f"m.computeHighlights({ENTRIES_MIDDLE_TWO_TIERS})")
    by_player = {}
    for h in hits:
        by_player.setdefault(h["id"], []).append(h["key"])
    assert by_player == {"a": ["tb1"], "b": ["tb1", "tb2"], "c": ["tb2"]}


def test_middle_player_keeps_both_explanations_when_same_tier_decides_twice():
    entries = (
        "[{id:'a', name:'Ann', total:5, tb:[3,0,0,0,0,0]},"
        " {id:'b', name:'Bo',  total:5, tb:[2,0,0,0,0,0]},"
        " {id:'c', name:'Cy',  total:5, tb:[1,0,0,0,0,0]}]"
    )
    grouped = _run(f"m.groupHighlights({entries})")
    assert grouped["b"]["tb1"] == [
        _run(f"m.explainTie({{total:5,tb:[3,0,0,0,0,0]}}, {{total:5,tb:[2,0,0,0,0,0]}}, 'Ann', 'Bo')"),
        _run(f"m.explainTie({{total:5,tb:[2,0,0,0,0,0]}}, {{total:5,tb:[1,0,0,0,0,0]}}, 'Bo', 'Cy')"),
    ]
    assert len(grouped["a"]["tb1"]) == 1 and len(grouped["c"]["tb1"]) == 1


def test_no_highlights_for_unequal_totals_or_unresolved_ties():
    entries = (
        "[{id:'a', name:'A', total:6, tb:[1,1,1,1,1,1]},"
        " {id:'b', name:'B', total:5, tb:[1,1,1,1,1,1]},"
        " {id:'c', name:'C', total:5, tb:[1,1,1,1,1,1]}]"
    )
    assert _run(f"m.computeHighlights({entries})") == []
