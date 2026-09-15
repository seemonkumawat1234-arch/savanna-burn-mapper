"""Fetch a pre/post Sentinel-2 pair from AWS open data, ready for burnmapper.

No account, no API key. Searches the Earth Search STAC API for Sentinel-2 L2A
scenes over an area of interest, picks the least cloudy scene in each of two
date windows, and writes windowed B4, B8 and B12 GeoTIFFs on one common grid.

    python scripts/fetch_sentinel2.py \
        --bbox 132.45 -13.30 132.70 -13.10 \
        --pre 2025-04-15 2025-06-10 \
        --post 2025-08-20 2025-10-20 \
        --out-dir data/kakadu

    burnmapper map \
        --pre-b4  data/kakadu/pre_B4.tif  --pre-b8  data/kakadu/pre_B8.tif \
        --pre-b12 data/kakadu/pre_B12.tif --post-b4 data/kakadu/post_B4.tif \
        --post-b8 data/kakadu/post_B8.tif --post-b12 data/kakadu/post_B12.tif

Two decisions worth knowing about:

**B12 defines the target grid.** B4 and B8 are 10 m and B12 is 20 m. Everything
is resampled onto B12's 20 m grid by averaging, rather than upsampling B12 to
10 m, because upsampling a coarse band invents detail it does not have and then
the burnt-area map carries that invention.

**Both dates must come from the same MGRS tile.** Scenes from different tiles
sit on different grids, and burnmapper refuses mismatched grids by design.
This script enforces it rather than silently reprojecting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"

# Earth Search asset keys for the three bands burnmapper needs.
ASSETS = {"B4": "red", "B8": "nir", "B12": "swir22"}


def _post(body: dict, tries: int = 4) -> dict:
    """POST with retries. Public STAC endpoints reset connections under load."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                SEARCH_URL, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            # A 4xx is our fault and will not improve with retrying.
            raise RuntimeError("STAC search rejected the query: {} {}".format(
                exc.code, exc.read().decode()[:300])) from exc
        except Exception as exc:                        # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("STAC search failed after {} attempts: {}".format(tries, last))


def search(bbox, start, end, max_cloud, limit=20):
    """Least-cloudy-first scenes intersecting bbox in [start, end].

    Dates are expanded to full RFC3339 timestamps. The API rejects bare
    YYYY-MM-DD with "does not match RFC3339 format", which is an easy hour to
    lose.
    """
    data = _post({
        "collections": [COLLECTION],
        "bbox": list(bbox),
        "datetime": "{}T00:00:00Z/{}T23:59:59Z".format(start, end),
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": limit,
    })
    feats = data.get("features", [])
    return sorted(feats, key=lambda f: f["properties"].get("eo:cloud_cover", 999))


def _tile(feature) -> str:
    p = feature["properties"]
    return p.get("grid:code") or p.get("s2:mgrs_tile") or "?"


def pick_pair(pre_scenes, post_scenes):
    """Least-cloudy pre/post pair that share an MGRS tile."""
    by_tile = {}
    for f in post_scenes:
        by_tile.setdefault(_tile(f), []).append(f)
    for pre in pre_scenes:
        posts = by_tile.get(_tile(pre))
        if posts:
            return pre, posts[0]
    raise SystemExit(
        "No pre/post pair shares an MGRS tile. Tiles seen: pre {} / post {}. "
        "Widen the date windows, raise --max-cloud, or shrink the bbox so it "
        "falls inside one tile.".format(
            sorted({_tile(f) for f in pre_scenes}),
            sorted({_tile(f) for f in post_scenes})))


def fetch(feature, label, bbox, out_dir):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    hrefs = {band: feature["assets"][key]["href"] for band, key in ASSETS.items()}

    # B12 is native 20 m and sets the target grid.
    with rasterio.open(hrefs["B12"]) as ds:
        bounds = transform_bounds("EPSG:4326", ds.crs, *bbox)
        win = from_bounds(*bounds, transform=ds.transform).round_offsets().round_lengths()
        shape = (int(win.height), int(win.width))
        transform = ds.window_transform(win)
        crs = ds.crs
    if shape[0] < 2 or shape[1] < 2:
        raise SystemExit("The bbox covers fewer than 2x2 pixels at 20 m. "
                         "Check the coordinate order: lon_min lat_min lon_max lat_max.")

    os.makedirs(out_dir, exist_ok=True)
    written = {}
    for band, href in hrefs.items():
        with rasterio.open(href) as ds:
            bounds = transform_bounds("EPSG:4326", ds.crs, *bbox)
            w = from_bounds(*bounds, transform=ds.transform)
            arr = ds.read(1, window=w, out_shape=shape,
                          resampling=Resampling.average,
                          boundless=True, fill_value=0)
        path = os.path.join(out_dir, "{}_{}.tif".format(label, band))
        profile = dict(driver="GTiff", height=shape[0], width=shape[1], count=1,
                       dtype="uint16", crs=crs, transform=transform,
                       compress="deflate", tiled=True, nodata=0)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(arr.astype("uint16"), 1)
            dst.update_tags(scene_id=feature["id"],
                            datetime=feature["properties"]["datetime"],
                            source="Sentinel-2 L2A via AWS open data")
        written[band] = path
        print("    {:4s} {}".format(band, path))
    return written, shape, crs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--pre", nargs=2, required=True, metavar=("START", "END"),
                    help="pre-fire date window, YYYY-MM-DD YYYY-MM-DD")
    ap.add_argument("--post", nargs=2, required=True, metavar=("START", "END"))
    ap.add_argument("--max-cloud", type=float, default=10.0,
                    help="reject scenes cloudier than this percent (default: %(default)s)")
    ap.add_argument("--out-dir", default="data/scene")
    args = ap.parse_args(argv)

    print("searching {} over {} ...".format(COLLECTION, args.bbox))
    pre_scenes = search(args.bbox, args.pre[0], args.pre[1], args.max_cloud)
    post_scenes = search(args.bbox, args.post[0], args.post[1], args.max_cloud)
    print("  pre window : {} scene(s)".format(len(pre_scenes)))
    print("  post window: {} scene(s)".format(len(post_scenes)))
    if not pre_scenes or not post_scenes:
        raise SystemExit("One window returned nothing. Widen the dates or raise "
                         "--max-cloud. Wet-season months rarely have clear scenes.")

    pre, post = pick_pair(pre_scenes, post_scenes)
    for label, f in (("pre", pre), ("post", post)):
        print("\n{}: {}  {}  cloud {:.2f}%  tile {}".format(
            label, f["id"], f["properties"]["datetime"][:10],
            f["properties"].get("eo:cloud_cover", -1), _tile(f)))
        _w, shape, crs = fetch(f, label, args.bbox, args.out_dir)
        print("    grid {} at 20 m, {}".format(shape, crs))

    print("\nNow run:\n"
          "  burnmapper map \\\n"
          "    --pre-b4 {d}/pre_B4.tif --pre-b8 {d}/pre_B8.tif "
          "--pre-b12 {d}/pre_B12.tif \\\n"
          "    --post-b4 {d}/post_B4.tif --post-b8 {d}/post_B8.tif "
          "--post-b12 {d}/post_B12.tif".format(d=args.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
