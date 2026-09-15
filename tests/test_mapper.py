"""End-to-end tests on the synthetic scene."""

import os

import numpy as np
import pytest

from burnmapper import indices, map_burn, plot_burn_map, synthetic_scene
from burnmapper.scene import write_geotiff


@pytest.fixture(scope="module")
def scene_and_truth():
    return synthetic_scene(size=128, seed=7, burn_fraction=0.25)


def test_synthetic_scene_is_flagged_synthetic(scene_and_truth):
    scene, _ = scene_and_truth
    assert scene.synthetic is True


def test_synthetic_scene_reflectance_is_physical(scene_and_truth):
    scene, _ = scene_and_truth
    for stack in (scene.pre, scene.post):
        for band, arr in stack.items():
            assert arr.min() >= 0.0, band
            assert arr.max() <= 1.0, band


def test_synthetic_scene_is_reproducible():
    a, ta = synthetic_scene(size=64, seed=99)
    b, tb = synthetic_scene(size=64, seed=99)
    np.testing.assert_array_equal(ta, tb)
    np.testing.assert_allclose(a.pre["B8"], b.pre["B8"])


def test_burn_scar_is_roughly_the_requested_fraction():
    _scene, truth = synthetic_scene(size=192, seed=11, burn_fraction=0.30)
    assert truth.mean() == pytest.approx(0.30, abs=0.02)


def test_dnbr_is_higher_inside_the_scar_than_outside(scene_and_truth):
    scene, truth = scene_and_truth
    result = map_burn(scene, truth=truth)
    inside = np.nanmean(result.dnbr[truth])
    outside = np.nanmean(result.dnbr[~truth])
    assert inside > outside
    # A real separation, not just a nudge.
    assert inside - outside > 0.10


def test_detection_recovers_most_of_the_scar(scene_and_truth):
    scene, truth = scene_and_truth
    result = map_burn(scene, truth=truth)
    assert result.accuracy is not None
    assert result.accuracy.overall_accuracy > 0.85
    assert result.accuracy.kappa > 0.65
    # Producer's accuracy for burnt: most of the real scar was found.
    assert result.accuracy.producers_accuracy[2] > 0.75


def test_burnt_area_is_close_to_the_true_area(scene_and_truth):
    scene, truth = scene_and_truth
    result = map_burn(scene, truth=truth)
    px_ha = (scene.pixel_size_m ** 2) / 10_000.0
    true_ha = float(np.count_nonzero(truth) * px_ha)
    assert result.burnt_area_ha == pytest.approx(true_ha, rel=0.30)


def test_class_areas_sum_to_the_whole_scene(scene_and_truth):
    scene, _ = scene_and_truth
    result = map_burn(scene)
    px_ha = (scene.pixel_size_m ** 2) / 10_000.0
    total = scene.shape[0] * scene.shape[1] * px_ha
    assert sum(result.areas_ha.values()) == pytest.approx(total)


def test_result_carries_the_synthetic_flag_through(scene_and_truth):
    scene, truth = scene_and_truth
    result = map_burn(scene, truth=truth)
    assert result.synthetic is True
    assert "SYNTHETIC" in result.summary()


def test_usgs_scheme_reports_savanna_fire_as_milder(scene_and_truth):
    # Same scene, two schemes. The USGS breakpoints were derived from crown
    # fires, so on a grass fire they push nearly everything into the low bins.
    # This is the reason the savanna scheme exists.
    scene, _ = scene_and_truth
    savanna = map_burn(scene, scheme=indices.SEVERITY_SAVANNA)
    usgs = map_burn(scene, scheme=indices.SEVERITY_USGS)
    top_savanna = savanna.areas_ha["Intense"]
    top_usgs = usgs.areas_ha["High severity"]
    assert top_savanna > top_usgs


def test_truth_shape_mismatch_raises(scene_and_truth):
    scene, _ = scene_and_truth
    with pytest.raises(ValueError):
        map_burn(scene, truth=np.zeros((4, 4), dtype=bool))


def test_missing_band_raises_with_a_useful_message(scene_and_truth):
    scene, _ = scene_and_truth
    del scene.pre["B4"]
    try:
        with pytest.raises(KeyError) as excinfo:
            map_burn(scene)
        assert "B4" in str(excinfo.value)
    finally:
        scene.pre["B4"] = np.zeros(scene.shape)


def test_geotiff_roundtrip_preserves_geometry_and_tags(tmp_path, scene_and_truth):
    rasterio = pytest.importorskip("rasterio")
    scene, _ = scene_and_truth
    arr = np.arange(scene.shape[0] * scene.shape[1], dtype="int16").reshape(scene.shape)
    path = str(tmp_path / "t.tif")
    write_geotiff(path, arr, scene, "int16", nodata=0)
    with rasterio.open(path) as ds:
        assert ds.crs.to_string() == scene.crs
        assert tuple(ds.transform)[:6] == pytest.approx(scene.transform)
        np.testing.assert_array_equal(ds.read(1), arr)
        # The synthetic provenance must survive onto disk.
        assert ds.tags().get("SYNTHETIC_INPUT") == "1"


def test_outputs_are_written(tmp_path, scene_and_truth):
    pytest.importorskip("rasterio")
    scene, truth = scene_and_truth
    result = map_burn(scene, truth=truth, out_dir=str(tmp_path))
    for key in ("dnbr", "rbr", "severity", "burnt"):
        assert os.path.isfile(result.written[key]), key


def test_figure_is_written(tmp_path, scene_and_truth):
    pytest.importorskip("matplotlib")
    scene, truth = scene_and_truth
    result = map_burn(scene, truth=truth)
    path = plot_burn_map(result, scene, str(tmp_path / "fig.png"))
    assert os.path.getsize(path) > 20_000
