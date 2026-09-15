"""Tests for the accuracy assessment.

The kappa cases matter most: kappa is the metric people quote and the one most
often computed wrongly.
"""

import numpy as np
import pytest

from burnmapper import accuracy


def test_perfect_agreement_gives_oa_one_and_kappa_one():
    ref = np.array([1, 1, 2, 2, 1, 2])
    res = accuracy.assess(ref, ref.copy(), labels=[1, 2])
    assert res.overall_accuracy == pytest.approx(1.0)
    assert res.kappa == pytest.approx(1.0)


def test_kappa_is_zero_for_chance_level_agreement():
    # 2x2 matrix where observed agreement equals expected agreement exactly.
    ref = np.array([1] * 50 + [2] * 50)
    pred = np.array(([1] * 25 + [2] * 25) * 2)
    res = accuracy.assess(ref, pred, labels=[1, 2])
    assert res.overall_accuracy == pytest.approx(0.5)
    assert res.kappa == pytest.approx(0.0, abs=1e-12)


def test_kappa_is_below_oa_on_an_imbalanced_map():
    # 95 percent unburnt. Predicting "unburnt" everywhere scores 95 percent OA
    # while finding no fire at all, and kappa is what exposes that.
    ref = np.array([1] * 95 + [2] * 5)
    pred = np.array([1] * 100)
    res = accuracy.assess(ref, pred, labels=[1, 2])
    assert res.overall_accuracy == pytest.approx(0.95)
    assert res.kappa == pytest.approx(0.0, abs=1e-12)
    assert res.producers_accuracy[2] == pytest.approx(0.0)


def test_matrix_orientation_is_reference_rows_predicted_columns():
    # One reference-burnt pixel predicted unburnt: a missed detection, which
    # must land at [burnt, unburnt], not the transpose.
    ref = np.array([2])
    pred = np.array([1])
    labels, m = accuracy.confusion_matrix(ref, pred, labels=[1, 2])
    assert labels == [1, 2]
    assert m[1, 0] == 1
    assert m[0, 1] == 0


def test_producers_and_users_accuracy_differ_and_are_not_swapped():
    # 2 of 3 reference-burnt found  -> producer's = 2/3
    # 2 of 4 mapped-burnt correct    -> user's     = 2/4
    ref = np.array([2, 2, 2, 1, 1, 1])
    pred = np.array([2, 2, 1, 2, 2, 1])
    res = accuracy.assess(ref, pred, labels=[1, 2])
    assert res.producers_accuracy[2] == pytest.approx(2 / 3)
    assert res.users_accuracy[2] == pytest.approx(2 / 4)


def test_nodata_pixels_are_excluded_from_the_sample():
    ref = np.array([0, 1, 2, 2])
    pred = np.array([1, 1, 2, 0])
    res = accuracy.assess(ref, pred, labels=[1, 2])
    assert res.n == 2
    assert res.overall_accuracy == pytest.approx(1.0)


def test_all_nodata_raises_rather_than_returning_a_fake_score():
    with pytest.raises(ValueError):
        accuracy.assess(np.zeros(5, dtype=int), np.zeros(5, dtype=int))


def test_size_mismatch_raises():
    with pytest.raises(ValueError):
        accuracy.assess(np.array([1, 2]), np.array([1, 2, 1]))


def test_report_is_renderable():
    ref = np.array([1, 2, 1, 2])
    pred = np.array([1, 2, 2, 2])
    res = accuracy.assess(ref, pred, labels=[1, 2])
    text = res.report({1: "unburnt", 2: "burnt"})
    assert "Overall accuracy" in text
    assert "kappa" in text.lower()
    assert "burnt" in text
