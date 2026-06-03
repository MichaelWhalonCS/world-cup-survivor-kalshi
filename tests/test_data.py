"""Data-layer sanity tests — verify groups.json + matches.json load and
that the fixtures resolver produces the right 3 group matches per nation."""

from src.fixtures import build_group_matches, group_matches_for, opponent_for
from src.teams import NATIONS, get_all_nations, get_group


def test_48_nations_in_12_groups_of_4():
    nations = get_all_nations()
    assert len(nations) == 48, f"expected 48 nations, got {len(nations)}"
    groups = {n.group for n in nations}
    assert len(groups) == 12
    for letter in groups:
        assert len(get_group(letter)) == 4, f"group {letter} should have 4 nations"


def test_fifa_codes_are_unique():
    codes = [n.fifa_code for n in NATIONS]
    assert len(codes) == len(set(codes)), "duplicate FIFA codes detected"


def test_each_nation_has_3_group_matches():
    for nation in get_all_nations():
        matches = group_matches_for(nation)
        assert set(matches.keys()) == {"MD1", "MD2", "MD3"}
        for md, m in matches.items():
            assert m is not None, f"{nation.fifa_code} missing match for {md}"


def test_each_nation_plays_each_groupmate_exactly_once():
    for nation in get_all_nations():
        opponents = {opponent_for(nation, md).fifa_code for md in ("MD1", "MD2", "MD3")}
        groupmates = {n.fifa_code for n in get_group(nation.group) if n != nation}
        assert opponents == groupmates, (
            f"{nation.fifa_code}: opponents {opponents} != groupmates {groupmates}"
        )


def test_total_group_matches_count():
    matches = build_group_matches()
    # 12 groups × 6 matches per group = 72
    assert len(matches) == 72, f"expected 72 group matches, got {len(matches)}"
