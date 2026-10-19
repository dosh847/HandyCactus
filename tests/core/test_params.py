# SPDX-License-Identifier: GPL-3.0-or-later
import dataclasses

import pytest

from handycactus.core.params import (
    DEFAULT_PRESET_ID,
    GROUPS,
    PARAM_SPECS,
    PRESETS,
    PRESETS_BY_ID,
    SPECS_BY_NAME,
    Params,
)


def test_every_dataclass_field_has_a_spec_and_vice_versa():
    assert [f.name for f in dataclasses.fields(Params)] == [s.name for s in PARAM_SPECS]


def test_specs_are_well_formed():
    group_ids = {g for g, _ in GROUPS}
    for spec in PARAM_SPECS:
        assert spec.group in group_ids, spec.name
        assert spec.min <= spec.default <= spec.max, spec.name
        assert type(spec.min) is type(spec.default) is type(spec.max), spec.name


def test_defaults_are_the_42mm_02_nozzle_preset():
    p = Params()
    assert p.nozzle_diameter == 0.2
    assert p.layer_height == 0.08
    assert p.tip_spacing == 1.5
    assert p.contact_diameter == 0.3
    assert p.tip_length == 1.5
    assert p.knuckle_diameter == 0.6
    assert p.max_radius == 2.0
    assert p.foot_radius == 1.5
    assert PRESETS_BY_ID[DEFAULT_PRESET_ID].params() == p


def test_defaults_have_no_problems():
    assert Params().problems() == []


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.id)
def test_presets_are_valid(preset):
    assert preset.params().problems() == []


def test_preset_table_values():
    assert PRESETS_BY_ID["MINI_32"].params().tip_spacing == 1.3
    assert PRESETS_BY_ID["MINI_50"].params().knuckle_diameter == 0.7
    p04 = PRESETS_BY_ID["MINI_42_NOZZLE_04"].params()
    assert (p04.nozzle_diameter, p04.min_radius, p04.contact_diameter) == (0.4, 0.4, 0.4)


def test_problems_reports_ranges_and_inconsistencies():
    p = Params().with_values({"tip_spacing": 50.0, "min_radius": 3.0, "min_tip_length": 2.0})
    problems = p.problems()
    assert any("Tip Spacing" in m for m in problems)
    assert "Min Radius is larger than Max Radius" in problems
    assert "Min Tip Length is longer than Tip Length" in problems


def test_min_radius_below_half_nozzle_is_reported():
    p = Params().with_values({"nozzle_diameter": 0.4})  # min_radius stays 0.2
    assert p.problems() == []
    p = Params().with_values({"nozzle_diameter": 0.6})
    assert "Min Radius is below half the nozzle diameter" in p.problems()


def test_with_values_rejects_unknown_names():
    with pytest.raises(KeyError):
        Params().with_values({"nope": 1.0})


def test_specs_by_name_covers_all():
    assert set(SPECS_BY_NAME) == {s.name for s in PARAM_SPECS}
