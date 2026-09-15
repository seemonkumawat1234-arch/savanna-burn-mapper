"""End-to-end burnt-area mapping.

Ties the pieces together: bands in, indices, severity classes, areas, an
optional accuracy assessment, and a figure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from . import indices
from .accuracy import ConfusionResult, assess
from .scene import Scene, write_geotiff

__all__ = ["BurnMap", "map_burn", "plot_burn_map"]


@dataclass
class BurnMap:
    """Everything one run produces."""

    nbr_pre: np.ndarray
    nbr_post: np.ndarray
    dnbr: np.ndarray
    rbr: np.ndarray
    severity: np.ndarray
    burnt: np.ndarray
    areas_ha: Dict[str, float]
    burnt_area_ha: float
    scheme: Sequence[Tuple[int, str, float, float]]
    synthetic: bool = False
    accuracy: Optional[ConfusionResult] = None
    written: Dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        lines = []
        if self.synthetic:
            lines += ["*** SYNTHETIC TEST SCENE, NOT REAL IMAGERY ***", ""]
        lines.append("Burnt area: {:,.1f} ha".format(self.burnt_area_ha))
        lines.append("")
        lines.append("{:<18} {:>14}".format("severity class", "hectares"))
        for _cid, label, _lo, _hi in self.scheme:
            lines.append("{:<18} {:>14,.1f}".format(label, self.areas_ha.get(label, 0.0)))
        if "No data" in self.areas_ha:
            lines.append("{:<18} {:>14,.1f}".format("No data", self.areas_ha["No data"]))
        if self.accuracy is not None:
            lines += ["", "Accuracy vs reference", "-" * 34,
                      self.accuracy.report({1: "unburnt", 2: "burnt"})]
        return "\n".join(lines)


def map_burn(scene: Scene,
             scheme: Sequence[Tuple[int, str, float, float]] = indices.SEVERITY_SAVANNA,
             threshold: float = 0.08,
             max_nbr_post: Optional[float] = 0.25,
             truth: Optional[np.ndarray] = None,
             out_dir: Optional[str] = None) -> BurnMap:
    """Run the full chain on one scene.

    ``truth`` is an optional boolean burnt mask. When given, the result carries
    an accuracy assessment against it.

    ``max_nbr_post`` applies the second burnt-mask condition described in
    ``indices.burnt_mask``. Pass None to disable it and threshold on dNBR only.
    """
    scene.require("B4", "B8", "B12")

    nbr_pre = indices.nbr(scene.pre["B8"], scene.pre["B12"])
    nbr_post = indices.nbr(scene.post["B8"], scene.post["B12"])
    d = indices.dnbr(nbr_pre, nbr_post)
    r = indices.rbr(nbr_pre, nbr_post)

    severity = indices.classify_severity(d, scheme)
    burnt = indices.burnt_mask(d, threshold=threshold, nbr_post=nbr_post,
                               max_nbr_post=max_nbr_post)

    areas = indices.class_areas(severity, scene.pixel_size_m, scheme)
    px_ha = (scene.pixel_size_m ** 2) / 10_000.0
    burnt_ha = float(np.count_nonzero(burnt) * px_ha)

    acc = None
    if truth is not None:
        truth = np.asarray(truth).astype(bool)
        if truth.shape != burnt.shape:
            raise ValueError("truth shape {} does not match map shape {}".format(
                truth.shape, burnt.shape))
        # 1 = unburnt, 2 = burnt. Class 0 stays reserved for no-data, which is
        # what accuracy.confusion_matrix drops.
        acc = assess(np.where(truth, 2, 1), np.where(burnt, 2, 1), labels=[1, 2])

    result = BurnMap(nbr_pre=nbr_pre, nbr_post=nbr_post, dnbr=d, rbr=r,
                     severity=severity, burnt=burnt, areas_ha=areas,
                     burnt_area_ha=burnt_ha, scheme=scheme,
                     synthetic=scene.synthetic, accuracy=acc)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        result.written["dnbr"] = write_geotiff(
            os.path.join(out_dir, "dnbr.tif"), d, scene, "float32")
        result.written["rbr"] = write_geotiff(
            os.path.join(out_dir, "rbr.tif"), r, scene, "float32")
        result.written["severity"] = write_geotiff(
            os.path.join(out_dir, "severity.tif"), severity, scene, "int16", nodata=0)
        result.written["burnt"] = write_geotiff(
            os.path.join(out_dir, "burnt_mask.tif"), burnt.astype("int16"),
            scene, "int16", nodata=0)
    return result


def plot_burn_map(result: BurnMap, scene: Scene, path: str,
                  title: str = "Burnt area, dNBR") -> str:
    """Four-panel figure: pre-fire NBR, post-fire NBR, dNBR, severity."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap

    fig, axes = plt.subplots(2, 2, figsize=(11, 10.4))
    fig.patch.set_facecolor("white")

    im0 = axes[0, 0].imshow(result.nbr_pre, cmap="RdYlGn", vmin=-0.4, vmax=0.8)
    axes[0, 0].set_title("Pre-fire NBR")
    fig.colorbar(im0, ax=axes[0, 0], fraction=0.046)

    im1 = axes[0, 1].imshow(result.nbr_post, cmap="RdYlGn", vmin=-0.4, vmax=0.8)
    axes[0, 1].set_title("Post-fire NBR")
    fig.colorbar(im1, ax=axes[0, 1], fraction=0.046)

    im2 = axes[1, 0].imshow(result.dnbr, cmap="inferno", vmin=-0.1, vmax=0.6)
    axes[1, 0].set_title("dNBR (pre minus post)")
    fig.colorbar(im2, ax=axes[1, 0], fraction=0.046)

    ids = [c[0] for c in result.scheme]
    labels = [c[1] for c in result.scheme]
    palette = ListedColormap(
        ["#cfcfcf", "#2f7d4f", "#f1e2a8", "#e08a3c", "#a02020"][:len(ids)])
    bounds = [ids[0] - 0.5] + [i + 0.5 for i in ids]
    im3 = axes[1, 1].imshow(result.severity, cmap=palette,
                            norm=BoundaryNorm(bounds, palette.N))
    axes[1, 1].set_title("Severity class")
    cbar = fig.colorbar(im3, ax=axes[1, 1], fraction=0.046, ticks=ids)
    cbar.ax.set_yticklabels(labels, fontsize=8)

    for ax in axes.ravel():
        ax.set_xticks([])
        ax.set_yticks([])

    subtitle = "{:,.0f} ha burnt".format(result.burnt_area_ha)
    if result.accuracy is not None:
        subtitle += "   |   OA {:.1%}, kappa {:.3f}".format(
            result.accuracy.overall_accuracy, result.accuracy.kappa)
    if result.synthetic:
        subtitle += "   |   SYNTHETIC TEST SCENE, NOT REAL IMAGERY"

    fig.suptitle(title, fontsize=15, y=0.975)
    fig.text(0.5, 0.938, subtitle, ha="center", fontsize=9.5,
             color="#a02020" if result.synthetic else "#444444")
    fig.tight_layout(rect=(0, 0, 1, 0.928))

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path
