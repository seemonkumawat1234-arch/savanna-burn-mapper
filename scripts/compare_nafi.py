"""Validate a burnt-area map against NAFI fire-scar mapping.

NAFI, the North Australia and Rangelands Fire Information service, is run by
Charles Darwin University and publishes independent fire-scar mapping for most
of northern Australia. It is the reference product for this country, which
makes it the right thing to check a dNBR map against.

    python scripts/compare_nafi.py \
        --burnt outputs/burnt_mask.tif \
        --layer public__fshkak_2025 \
        --bbox 132.45 -13.30 132.70 -13.10 \
        --months 6 7 8 9

NAFI serves these through a GeoServer WCS, so no account or API key is needed.
Raster values are the **month of burn**, 1 to 12, with 0 for unburnt.

Why ``--months`` matters
------------------------
A dNBR map only sees change between its two image dates. NAFI covers the whole
year. Comparing the two without restricting NAFI to the same window measures
the difference in observation period as much as anything about the method, and
makes the dNBR map look far worse than it is. Pass the months your pre and post
images actually bracket.

Discovering layer names
-----------------------
    curl -s "https://firenorth.org.au/geoserver/wms?service=WMS&version=1.3.0\\
&request=GetCapabilities" | grep -o '<Name>[^<]*fsh[^<]*</Name>'

``fsh*`` layers are the high-resolution (Sentinel-2 derived) scars, ``fs<year>``
are the 250 m MODIS-derived continental ones. The high-resolution layers are
regional: ``fshkak`` Kakadu, ``fshgar`` Garig, ``fshwar`` Warddeken, and so on.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
import urllib.request

WCS = "https://firenorth.org.au/geoserver/wcs"


def fetch_nafi(layer: str, bbox, out_path: str) -> str:
    """Download a NAFI month-of-burn subset as GeoTIFF via WCS 2.0.1.

    The axis labels are Lat and Long, capitalised. GeoServer rejects "lat" and
    "lon" with "Invalid axis label provided", which is an easy half hour to
    lose.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    query = {
        "service": "WCS", "version": "2.0.1", "request": "GetCoverage",
        "coverageId": layer, "format": "image/geotiff",
    }
    url = "{}?{}&subset=Lat({},{})&subset=Long({},{})".format(
        WCS, urllib.parse.urlencode(query), lat_min, lat_max, lon_min, lon_max)

    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as r:
        data = r.read()
    if data[:4] not in (b"MM\x00*", b"II*\x00"):
        head = data[:400].decode("utf-8", "replace")
        raise SystemExit("WCS did not return a GeoTIFF. Response begins:\n" + head)
    with open(out_path, "wb") as fh:
        fh.write(data)
    return out_path


def align(nafi_path: str, like_path: str):
    """Reproject NAFI onto the burnt map's grid.

    Nearest neighbour, always. These are categorical month codes, and averaging
    them would produce months that never happened.
    """
    import numpy as np
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(like_path) as ds:
        burnt = ds.read(1).astype(bool)
        dst = dict(transform=ds.transform, crs=ds.crs,
                   height=ds.height, width=ds.width)
        pixel_ha = abs(ds.transform[0] * ds.transform[4]) / 10_000.0

    months = np.zeros((dst["height"], dst["width"]), dtype="uint8")
    with rasterio.open(nafi_path) as src:
        reproject(source=rasterio.band(src, 1), destination=months,
                  src_transform=src.transform, src_crs=src.crs,
                  dst_transform=dst["transform"], dst_crs=dst["crs"],
                  resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
    return burnt, months, pixel_ha


def main(argv=None) -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(here, "src"))

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--burnt", required=True, help="burnt_mask.tif from burnmapper")
    ap.add_argument("--layer", default="public__fshkak_2025",
                    help="NAFI WCS coverage id (default: %(default)s)")
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--months", nargs="*", type=int, default=None,
                    help="NAFI months to count as burnt, matching your image "
                         "window. Omit to use the whole year, which is usually "
                         "not a fair comparison.")
    ap.add_argument("--nafi-out", default="data/nafi_scar.tif")
    args = ap.parse_args(argv)

    import numpy as np
    from burnmapper import assess

    print("fetching NAFI {} ...".format(args.layer))
    path = fetch_nafi(args.layer, args.bbox, args.nafi_out)
    print("  {}".format(path))

    burnt, months, px_ha = align(path, args.burnt)
    n_px = burnt.size

    print("\nNAFI month-of-burn, area by month")
    for m in range(1, 13):
        n = int((months == m).sum())
        if n:
            print("  month {:2d}: {:>10,.0f} ha  ({:4.1f}%)".format(
                m, n * px_ha, 100.0 * n / n_px))
    print("  unburnt : {:>10,.0f} ha".format(int((months == 0).sum()) * px_ha))

    ref = np.isin(months, args.months) if args.months else (months > 0)
    label = ("NAFI months {}".format(args.months) if args.months
             else "NAFI whole year")

    print("\nareas")
    print("  this map : {:>10,.0f} ha  ({:4.1f}%)".format(burnt.sum() * px_ha, 100 * burnt.mean()))
    print("  {:9s}: {:>10,.0f} ha  ({:4.1f}%)".format(label[:9], ref.sum() * px_ha, 100 * ref.mean()))
    print("  difference: {:+.1f}%".format(100.0 * (burnt.sum() - ref.sum()) / max(1, ref.sum())))

    r = assess(np.where(ref, 2, 1), np.where(burnt, 2, 1), labels=[1, 2])
    print("\nagreement with {}".format(label))
    print(r.report({1: "unburnt", 2: "burnt"}))
    m = r.matrix
    print("  commission (mapped burnt, NAFI not): {:>10,.0f} ha".format(m[0, 1] * px_ha))
    print("  omission   (NAFI burnt, not mapped): {:>10,.0f} ha".format(m[1, 0] * px_ha))
    print("\nNAFI is itself a mapped product with its own errors, so this is "
          "agreement between two\nindependent estimates, not truth.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
