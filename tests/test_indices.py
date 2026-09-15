"""Tests for the spectral index maths.

These assert things that must be true about the physics and the arithmetic, not
merely that the functions return without raising.
"""

import numpy as np
import pytest

from burnmapper import indices


def test_normalised_difference_matches_hand_calculation():
    a = np.array([[0.4, 0.1]])
    b = np.array([[0.2, 0.3]])
    out = indices.normalised_difference(a, b)
    np.testing.assert_allclose(out, [[0.2 / 0.6, -0.2 / 0.4]])


def test_normalised_difference_is_bounded():
    rng = np.random.default_rng(0)
    a = rng.random((64, 64))
    b = rng.random((64, 64))
    out = indices.normalised_difference(a, b)
    assert np.all(out >= -1.0 - 1e-12)
    assert np.all(out <= 1.0 + 1e-12)


def test_zero_denominator_is_nan_not_zero():
    # A zero index value is a real measurement, so no-data must not be
    # collapsed into it.
    out = indices.normalised_difference(np.array([0.0]), np.array([0.0]))
    assert np.isnan(out[0])


def test_mismatched_shapes_raise():
    with pytest.raises(ValueError):
        indices.normalised_difference(np.zeros((4, 4)), np.zeros((4, 5)))


def test_burnt_pixel_has_lower_nbr_than_vegetated():
    # Healthy vegetation: NIR-bright, SWIR-dark. Char: the reverse.
    veg = indices.nbr(np.array([0.34]), np.array([0.13]))
    char = indices.nbr(np.array([0.11]), np.array([0.32]))
    assert veg[0] > char[0]
    assert char[0] < 0 < veg[0]


def test_dnbr_is_positive_when_fire_reduces_nbr():
    pre = np.array([0.55])
    post = np.array([-0.15])
    assert indices.dnbr(pre, post)[0] == pytest.approx(0.70)


def test_dnbr_is_negative_for_greening():
    assert indices.dnbr(np.array([0.10]), np.array([0.40]))[0] < 0


def test_rbr_reduces_dependence_on_pre_fire_cover():
    # Same absolute NBR drop over dense and sparse pre-fire cover. dNBR cannot
    # tell them apart; RBR scales by pre-fire condition, which is the whole
    # reason Parks et al. proposed it.
    drop = 0.30
    dense_pre, sparse_pre = 0.70, 0.10
    d_dense = indices.dnbr(np.array([dense_pre]), np.array([dense_pre - drop]))
    d_sparse = indices.dnbr(np.array([sparse_pre]), np.array([sparse_pre - drop]))
    assert d_dense[0] == pytest.approx(d_sparse[0])

    r_dense = indices.rbr(np.array([dense_pre]), np.array([dense_pre - drop]))
    r_sparse = indices.rbr(np.array([sparse_pre]), np.array([sparse_pre - drop]))
    assert r_sparse[0] > r_dense[0]


def test_severity_schemes_are_contiguous_and_ordered():
    for scheme in (indices.SEVERITY_USGS, indices.SEVERITY_SAVANNA):
        assert scheme[0][2] == -np.inf
        assert scheme[-1][3] == np.inf
        for lower, upper in zip(scheme, scheme[1:]):
            # No gaps and no overlaps: one bin's top is the next bin's bottom.
            assert lower[3] == upper[2]
        ids = [c[0] for c in scheme]
        assert ids == sorted(ids)
        assert 0 not in ids, "class id 0 is reserved for no-data"


def test_classify_severity_bins_by_scheme():
    scheme = indices.SEVERITY_SAVANNA
    d = np.array([[-0.5, 0.0, 0.12, 0.25, 0.9]])
    out = indices.classify_severity(d, scheme)
    assert out.tolist() == [[1, 2, 3, 4, 5]]


def test_classify_severity_maps_nan_to_zero():
    out = indices.classify_severity(np.array([np.nan, 0.2]))
    assert out[0] == 0
    assert out[1] != 0


def test_every_finite_pixel_gets_a_class():
    rng = np.random.default_rng(3)
    d = rng.normal(0.1, 0.5, (100, 100))
    out = indices.classify_severity(d)
    assert np.count_nonzero(out == 0) == 0


def test_burnt_mask_threshold_is_inclusive():
    d = np.array([0.079, 0.080, 0.081])
    m = indices.burnt_mask(d, threshold=0.08, max_nbr_post=None)
    assert m.tolist() == [False, True, True]


def test_burnt_mask_second_condition_rejects_bright_post_fire_pixels():
    # High dNBR but the post-fire pixel is still green: this is the classic
    # false positive the NBR ceiling exists to remove.
    d = np.array([0.5, 0.5])
    nbr_post = np.array([0.60, -0.20])
    loose = indices.burnt_mask(d, threshold=0.08, max_nbr_post=None)
    strict = indices.burnt_mask(d, threshold=0.08, nbr_post=nbr_post,
                                max_nbr_post=0.25)
    assert loose.tolist() == [True, True]
    assert strict.tolist() == [False, True]


def test_burnt_mask_ignores_nan():
    m = indices.burnt_mask(np.array([np.nan, 0.5]), max_nbr_post=None)
    assert m.tolist() == [False, True]


def test_class_areas_convert_pixels_to_hectares():
    # 25 pixels of 20 m is 25 * 400 m2 = 10000 m2 = exactly 1 ha.
    classified = np.full((5, 5), 3, dtype="int16")
    areas = indices.class_areas(classified, 20.0, indices.SEVERITY_SAVANNA)
    assert areas["Patchy / light"] == pytest.approx(1.0)
    assert areas["Unburnt"] == pytest.approx(0.0)


def test_class_areas_report_nodata_separately():
    classified = np.zeros((10, 10), dtype="int16")
    areas = indices.class_areas(classified, 20.0)
    assert areas["No data"] == pytest.approx(4.0)
