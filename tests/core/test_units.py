# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from handycactus.core.units import bu_to_mm, mm_per_bu, mm_to_bu


def test_metre_scene():
    assert mm_per_bu(1.0) == 1000.0
    assert mm_to_bu(1.5, 1.0) == pytest.approx(0.0015)


def test_millimetre_scene():
    assert mm_to_bu(1.5, 0.001) == pytest.approx(1.5)
    assert bu_to_mm(42.0, 0.001) == pytest.approx(42.0)


def test_round_trip():
    assert bu_to_mm(mm_to_bu(3.7, 0.01), 0.01) == pytest.approx(3.7)


def test_rejects_non_positive_scale():
    with pytest.raises(ValueError):
        mm_per_bu(0.0)
