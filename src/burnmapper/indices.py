"""Spectral indices and burn-severity classification.

All functions are pure numpy and operate on 2D float arrays of surface
reflectance. Nothing here touches disk or the network, so the whole module is
cheap to test and runs on any CPU.

Band references are Sentinel-2 MSI:

    B4   red     665 nm
    B8   NIR     842 nm
    B12  SWIR-2  2190 nm

References
----------
Key, C.H. and Benson, N.C. (2006). Landscape Assessment: Sampling and Analysis
    Methods. FIREMON: Fire Effects Monitoring and Inventory System. USDA Forest
    Service, RMRS-GTR-164-CD.
Parks, S.A., Dillon, G.K. and Miller, C. (2014). A new metric for quantifying
    burn severity: the Relativized Burn Ratio. Remote Sensing 6(3), 1827-1844.
"""

from __future__ import annotations

from typing import Dict, Sequence, Tuple

import numpy as np

__all__ = [
    "normalised_difference",
    "nbr",
    "ndvi",
    "dnbr",
    "rbr",
    "SEVERITY_USGS",
    "SEVERITY_SAVANNA",
    "classify_severity",
    "burnt_mask",
]

# Small value that keeps a zero denominator from raising. Reflectance sums of
# exactly zero only occur over no-data, which the caller masks out anyway.
_EPS = 1e-10


def normalised_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(a - b) / (a + b), with zero denominators returned as NaN.

    NaN rather than 0 is deliberate: a zero index value is a legitimate
    measurement, so silently substituting one would hide no-data as real data.
    """
    a = np.asarray(a, dtype="float64")
    b = np.asarray(b, dtype="float64")
    if a.shape != b.shape:
        raise ValueError("band shapes differ: {} vs {}".format(a.shape, b.shape))
    denom = a + b
    out = np.full(a.shape, np.nan, dtype="float64")
    valid = np.abs(denom) > _EPS
    out[valid] = (a[valid] - b[valid]) / denom[valid]
    return out


def nbr(nir: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """Normalised Burn Ratio, (NIR - SWIR2) / (NIR + SWIR2).

    Healthy vegetation is high in NIR and low in SWIR2, so NBR is high. Fire
    removes the NIR-bright canopy and exposes charred material and soil, which
    are SWIR2-bright, so NBR drops sharply. That contrast is what makes NBR the
    standard index for burnt area.
    """
    return normalised_difference(nir, swir2)


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """Normalised Difference Vegetation Index, (NIR - Red) / (NIR + Red)."""
    return normalised_difference(nir, red)


def dnbr(nbr_pre: np.ndarray, nbr_post: np.ndarray) -> np.ndarray:
    """Differenced NBR, pre-fire minus post-fire.

    Positive values mean NBR fell, which is the direction fire pushes it.
    Negative values indicate the scene got greener between the two dates, which
    over savanna usually means wet-season regrowth rather than anything to do
    with fire.
    """
    nbr_pre = np.asarray(nbr_pre, dtype="float64")
    nbr_post = np.asarray(nbr_post, dtype="float64")
    if nbr_pre.shape != nbr_post.shape:
        raise ValueError("pre/post shapes differ: {} vs {}".format(
            nbr_pre.shape, nbr_post.shape))
    return nbr_pre - nbr_post


def rbr(nbr_pre: np.ndarray, nbr_post: np.ndarray) -> np.ndarray:
    """Relativised Burn Ratio, dNBR / (NBR_pre + 1.001).

    dNBR is an absolute change, so the same fire registers a larger dNBR over
    dense pre-fire vegetation than over sparse. RBR divides that out, which
    matters in savanna where pre-fire cover varies a lot across a single scene.
    The 1.001 offset is from Parks et al. (2014) and keeps the denominator
    positive, since NBR is bounded at -1.
    """
    return dnbr(nbr_pre, nbr_post) / (np.asarray(nbr_pre, dtype="float64") + 1.001)


# dNBR breakpoints from Key and Benson (2006). Each entry is
# (class id, label, lower bound inclusive, upper bound exclusive).
SEVERITY_USGS: Tuple[Tuple[int, str, float, float], ...] = (
    (1, "High regrowth",     -np.inf, -0.250),
    (2, "Low regrowth",       -0.250, -0.100),
    (3, "Unburnt",            -0.100,  0.100),
    (4, "Low severity",        0.100,  0.270),
    (5, "Moderate-low",        0.270,  0.440),
    (6, "Moderate-high",       0.440,  0.660),
    (7, "High severity",       0.660,  np.inf),
)

# Savanna fires are mostly fast-moving surface fires in grass. They consume the
# understorey without killing the canopy, so they land far lower on the dNBR
# scale than the crown fires the USGS breakpoints were derived from. Applying
# the USGS table unchanged to savanna reports almost everything as "low
# severity" or "unburnt" and loses the real variation.
#
# These breakpoints are a pragmatic rescaling for early-dry-season savanna
# burning, NOT an authoritative published table. Calibrate against field or
# NAFI reference data before using them for anything that matters.
SEVERITY_SAVANNA: Tuple[Tuple[int, str, float, float], ...] = (
    (1, "Regrowth",          -np.inf, -0.050),
    (2, "Unburnt",            -0.050,  0.080),
    (3, "Patchy / light",      0.080,  0.180),
    (4, "Moderate",            0.180,  0.320),
    (5, "Intense",             0.320,  np.inf),
)


def classify_severity(
    dnbr_arr: np.ndarray,
    scheme: Sequence[Tuple[int, str, float, float]] = SEVERITY_SAVANNA,
) -> np.ndarray:
    """Bin a dNBR array into severity class ids.

    Returns an int16 array. NaN input maps to 0, which is reserved for no-data
    in every scheme, so 0 never collides with a real class.
    """
    dnbr_arr = np.asarray(dnbr_arr, dtype="float64")
    out = np.zeros(dnbr_arr.shape, dtype="int16")
    finite = np.isfinite(dnbr_arr)
    for class_id, _label, lo, hi in scheme:
        if class_id == 0:
            raise ValueError("class id 0 is reserved for no-data")
        sel = finite & (dnbr_arr >= lo) & (dnbr_arr < hi)
        out[sel] = class_id
    return out


def burnt_mask(
    dnbr_arr: np.ndarray,
    threshold: float = 0.08,
    nbr_post: np.ndarray | None = None,
    max_nbr_post: float | None = None,
) -> np.ndarray:
    """Boolean burnt/unburnt mask.

    ``threshold`` is the dNBR above which a pixel counts as burnt. The default
    of 0.08 matches the savanna scheme's unburnt ceiling.

    ``nbr_post`` with ``max_nbr_post`` adds an optional second condition: the
    post-fire pixel must itself be dark in NBR terms. This suppresses a common
    false positive where a pixel was simply very green before the fire window
    and merely less green after, producing a high dNBR without any burning.
    """
    dnbr_arr = np.asarray(dnbr_arr, dtype="float64")
    mask = np.isfinite(dnbr_arr) & (dnbr_arr >= threshold)
    if nbr_post is not None and max_nbr_post is not None:
        nbr_post = np.asarray(nbr_post, dtype="float64")
        if nbr_post.shape != dnbr_arr.shape:
            raise ValueError("nbr_post shape does not match dnbr")
        mask &= np.isfinite(nbr_post) & (nbr_post <= max_nbr_post)
    return mask


def class_areas(classified: np.ndarray, pixel_size_m: float,
                scheme: Sequence[Tuple[int, str, float, float]] = SEVERITY_SAVANNA
                ) -> Dict[str, float]:
    """Area in hectares per severity class.

    One 20 m Sentinel-2 pixel is 400 m2, so 25 pixels to the hectare. Reporting
    hectares rather than pixel counts is what a land manager actually needs.
    """
    px_ha = (pixel_size_m ** 2) / 10_000.0
    out: Dict[str, float] = {}
    for class_id, label, _lo, _hi in scheme:
        out[label] = float(np.count_nonzero(classified == class_id) * px_ha)
    nodata = float(np.count_nonzero(classified == 0) * px_ha)
    if nodata:
        out["No data"] = nodata
    return out
