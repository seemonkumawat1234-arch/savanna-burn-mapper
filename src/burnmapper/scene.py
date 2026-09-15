"""Scene input and output.

Two ways to get a scene:

``load_scene``      reads real Sentinel-2 bands from GeoTIFFs on disk.
``synthetic_scene`` generates a plausible savanna scene with a known burn scar.

The synthetic generator exists so the tests and the demo run on any laptop with
no download, no API key and no network. It is NOT a substitute for real
imagery, and anything it produces is clearly labelled synthetic wherever it is
written or plotted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

__all__ = ["Scene", "synthetic_scene", "load_scene", "write_geotiff"]

# Darwin sits in UTM zone 52 south. Using the projection the area is actually
# worked in means pixel areas are real metres, so hectare figures are correct.
DARWIN_UTM = "EPSG:32752"
DEFAULT_PIXEL_M = 20.0          # Sentinel-2 B8A/B11/B12 native resolution


@dataclass
class Scene:
    """A pre/post band stack on a common grid.

    Reflectance is unscaled, in 0..1. Sentinel-2 L2A ships integers scaled by
    10000, and ``load_scene`` divides that out.
    """

    pre: Dict[str, np.ndarray]
    post: Dict[str, np.ndarray]
    crs: str = DARWIN_UTM
    transform: Optional[tuple] = None
    pixel_size_m: float = DEFAULT_PIXEL_M
    synthetic: bool = False

    @property
    def shape(self) -> Tuple[int, int]:
        return next(iter(self.pre.values())).shape

    def require(self, *bands: str) -> None:
        missing = [b for b in bands
                   if b not in self.pre or b not in self.post]
        if missing:
            raise KeyError("scene is missing band(s) {} in pre and/or post; "
                           "have pre={} post={}".format(
                               missing, sorted(self.pre), sorted(self.post)))


def _smooth(field: np.ndarray, passes: int = 4) -> np.ndarray:
    """Cheap 3x3 box blur, used only to soften edges after upsampling."""
    out = field.astype("float64").copy()
    for _ in range(passes):
        padded = np.pad(out, 1, mode="edge")
        out = (padded[:-2, 1:-1] + padded[2:, 1:-1]
               + padded[1:-1, :-2] + padded[1:-1, 2:]
               + out * 4.0) / 8.0
    return out


def _upsample(small: np.ndarray, size: int) -> np.ndarray:
    """Bilinear upsample a coarse grid to (size, size). Pure numpy."""
    n = small.shape[0]
    coord = np.linspace(0.0, n - 1.0, size)
    i0 = np.floor(coord).astype(int)
    i1 = np.minimum(i0 + 1, n - 1)
    frac = coord - i0

    fy = frac[:, None]
    fx = frac[None, :]
    top = small[i0][:, i0] * (1 - fx) + small[i0][:, i1] * fx
    bot = small[i1][:, i0] * (1 - fx) + small[i1][:, i1] * fx
    return top * (1 - fy) + bot * fy


def _coherent_field(size: int, cells: int, rng: np.random.Generator,
                    octaves: int = 3, persistence: float = 0.5) -> np.ndarray:
    """Multi-octave value noise, normalised to 0..1.

    Blurring white noise does not work for this: a few 3x3 passes give a
    correlation length of only two or three pixels, so the result is speckle
    and a "burn scar" cut from it is thousands of disconnected specks rather
    than a patch. Generating at ``cells`` resolution and upsampling sets the
    dominant feature size to about ``size / cells`` pixels, which is how you
    get landscape-scale structure. Finer octaves then add texture.
    """
    total = np.zeros((size, size), dtype="float64")
    amp, norm, c = 1.0, 0.0, max(2, cells)
    for _ in range(max(1, octaves)):
        total += amp * _upsample(rng.random((c, c)), size)
        norm += amp
        amp *= persistence
        c = min(c * 2, size)
    out = _smooth(total / norm, passes=2)
    return (out - out.min()) / (out.max() - out.min() + 1e-12)


def synthetic_scene(size: int = 256, seed: int = 20260915,
                    burn_fraction: float = 0.22) -> Tuple[Scene, np.ndarray]:
    """Build a synthetic savanna scene and return it with its truth mask.

    The scene mimicks early-dry-season savanna: a mix of grass and open
    woodland, with one irregular burn scar. Post-fire pixels inside the scar
    lose NIR and gain SWIR2, which is the real spectral signature of char.

    Returns ``(scene, truth)`` where ``truth`` is a boolean burnt mask. Having
    ground truth for free is the point: it makes the accuracy assessment path
    testable without any field data.
    """
    rng = np.random.default_rng(seed)

    # Pre-fire cover: a smooth gradient of vegetation density, 0 (bare) to 1.
    cover = _coherent_field(size, cells=7, rng=rng, octaves=4)

    # Pre-fire reflectance. Denser cover means brighter NIR, darker red and
    # SWIR, which is how green vegetation behaves.
    pre = {
        "B4":  0.16 - 0.09 * cover + rng.normal(0, 0.004, cover.shape),
        "B8":  0.16 + 0.26 * cover + rng.normal(0, 0.006, cover.shape),
        "B12": 0.29 - 0.11 * cover + rng.normal(0, 0.005, cover.shape),
    }

    # Burn scar: threshold a second smooth field so the shape is irregular and
    # connected, the way a real fire front leaves a scar.
    scar_field = _coherent_field(size, cells=5, rng=rng, octaves=3)

    # Severity ramps up from zero at the scar edge rather than switching on.
    # This is the single most important realism detail: a step-change scar is
    # perfectly separable and the classifier scores 100 percent, which tells
    # you nothing. Real scar edges are mixed pixels, part burnt and part not,
    # and they are where nearly all the classification error lives.
    footprint = float(np.quantile(scar_field, 1.0 - min(0.95, burn_fraction * 1.6)))
    spread = float(np.quantile(scar_field, 0.995) - footprint) or 1e-6
    severity = np.clip((scar_field - footprint) / spread, 0.0, 1.0)

    # Real fires skip patches. Unburnt islands inside the perimeter are normal
    # in early-dry-season savanna burning and are a genuine source of error.
    islands = _coherent_field(size, cells=16, rng=rng, octaves=2)
    severity = np.where(islands > np.quantile(islands, 0.84),
                        severity * 0.15, severity)

    # "Burnt" for reference purposes means burnt enough to matter. Taking the
    # top burn_fraction of severity puts the truth boundary in the middle of
    # the ramp, so marginal pixels exist on both sides of it.
    truth = severity >= float(np.quantile(severity, 1.0 - burn_fraction))

    post = {}
    for band, arr in pre.items():
        arr = arr.copy()
        if band == "B8":
            arr = arr - 0.20 * severity          # char destroys the NIR signal
        elif band == "B12":
            arr = arr + 0.16 * severity          # char and ash are SWIR-bright
        else:
            arr = arr + 0.02 * severity
        # Unburnt ground still changes between two dates: progressive drying
        # through the dry season. dNBR is therefore not zero off-scar, so the
        # threshold has to do real work.
        arr = arr - 0.020 * (~truth) * cover
        # A broad haze gradient, standing in for imperfect atmospheric
        # correction. Spatially correlated error is much harder to threshold
        # around than per-pixel noise, and it is what real scenes carry.
        yy = np.linspace(-1.0, 1.0, arr.shape[0])[:, None]
        xx = np.linspace(-1.0, 1.0, arr.shape[1])[None, :]
        arr = arr + 0.012 * (0.6 * yy + 0.4 * xx)
        post[band] = arr + rng.normal(0, 0.009, arr.shape)

    for d in (pre, post):
        for band in d:
            d[band] = np.clip(d[band], 0.0, 1.0)

    transform = (DEFAULT_PIXEL_M, 0.0, 700000.0,
                 0.0, -DEFAULT_PIXEL_M, 8620000.0)
    scene = Scene(pre=pre, post=post, crs=DARWIN_UTM, transform=transform,
                  pixel_size_m=DEFAULT_PIXEL_M, synthetic=True)
    return scene, truth


def load_scene(pre_paths: Dict[str, str], post_paths: Dict[str, str],
               scale: float = 10000.0) -> Scene:
    """Load real bands from GeoTIFFs.

    ``pre_paths`` and ``post_paths`` map band names to files, e.g.
    ``{"B8": "pre_B08.tif", "B12": "pre_B12.tif"}``. Every band must already be
    on the same grid; this does not reproject or resample, it fails loudly
    instead, because silently resampling a band is how a subtle misalignment
    turns into a wrong map.
    """
    import rasterio

    def _read(paths: Dict[str, str]):
        bands, meta = {}, None
        for name, path in paths.items():
            if not os.path.isfile(path):
                raise FileNotFoundError("band {}: {}".format(name, path))
            with rasterio.open(path) as ds:
                arr = ds.read(1).astype("float64")
                nodata = ds.nodata
                if nodata is not None:
                    arr[arr == nodata] = np.nan
                bands[name] = arr / scale
                this = (ds.crs.to_string() if ds.crs else None,
                        tuple(ds.transform)[:6], ds.shape)
            if meta is None:
                meta = this
            elif this != meta:
                raise ValueError(
                    "band {} is not on the same grid as the others. Got "
                    "crs/transform/shape {} but expected {}. Reproject and "
                    "resample to a common grid before loading.".format(
                        name, this, meta))
        return bands, meta

    pre, meta_pre = _read(pre_paths)
    post, meta_post = _read(post_paths)
    if meta_pre != meta_post:
        raise ValueError("pre and post stacks are on different grids: {} vs {}"
                         .format(meta_pre, meta_post))

    crs, transform, _shape = meta_pre
    return Scene(pre=pre, post=post, crs=crs or DARWIN_UTM,
                 transform=transform, pixel_size_m=abs(transform[0]),
                 synthetic=False)


def write_geotiff(path: str, array: np.ndarray, scene: Scene,
                  dtype: str | None = None, nodata=None) -> str:
    """Write one array as a single-band GeoTIFF carrying the scene's geometry.

    Tagged with ``SYNTHETIC_INPUT=1`` when the scene came from the generator,
    so a file cannot be mistaken for a real result once it leaves this process.
    """
    import rasterio
    from rasterio.transform import Affine

    arr = np.asarray(array)
    dtype = dtype or ("int16" if arr.dtype.kind in "iu" else "float32")
    arr = arr.astype(dtype)

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    transform = Affine(*scene.transform) if scene.transform else Affine.identity()
    profile = dict(driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                   count=1, dtype=dtype, crs=scene.crs, transform=transform,
                   compress="deflate", tiled=True)
    if nodata is not None:
        profile["nodata"] = nodata

    with rasterio.open(path, "w", **profile) as ds:
        ds.write(arr, 1)
        tags = {"generator": "savanna-burn-mapper"}
        if scene.synthetic:
            tags["SYNTHETIC_INPUT"] = "1"
            tags["WARNING"] = ("derived from a synthetic test scene, "
                               "not real satellite imagery")
        ds.update_tags(**tags)
    return path
