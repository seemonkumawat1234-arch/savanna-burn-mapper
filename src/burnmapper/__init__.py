"""savanna-burn-mapper: burnt-area and severity mapping from Sentinel-2.

CPU only, no GPU, no cloud account. Built around dNBR, the standard index for
burnt area, with severity breakpoints adjusted for northern Australian savanna
where fires are mostly low-severity grass fires.
"""

from .accuracy import ConfusionResult, assess, confusion_matrix
from .indices import (SEVERITY_SAVANNA, SEVERITY_USGS, burnt_mask,
                      class_areas, classify_severity, dnbr, nbr, ndvi,
                      normalised_difference, rbr)
from .mapper import BurnMap, map_burn, plot_burn_map
from .scene import Scene, load_scene, synthetic_scene, write_geotiff

__version__ = "0.1.0"

__all__ = [
    "SEVERITY_SAVANNA", "SEVERITY_USGS",
    "normalised_difference", "nbr", "ndvi", "dnbr", "rbr",
    "classify_severity", "burnt_mask", "class_areas",
    "Scene", "synthetic_scene", "load_scene", "write_geotiff",
    "BurnMap", "map_burn", "plot_burn_map",
    "ConfusionResult", "assess", "confusion_matrix",
    "__version__",
]
