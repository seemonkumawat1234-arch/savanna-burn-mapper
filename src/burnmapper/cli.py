"""Command line interface.

    python -m burnmapper demo
    python -m burnmapper map --pre-b8 ... --pre-b12 ... --post-b8 ... --post-b12 ...
"""

from __future__ import annotations

import argparse
import os
import sys

from . import indices
from .mapper import map_burn, plot_burn_map
from .scene import load_scene, synthetic_scene

SCHEMES = {"savanna": indices.SEVERITY_SAVANNA, "usgs": indices.SEVERITY_USGS}


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--scheme", choices=sorted(SCHEMES), default="savanna",
                   help="severity breakpoints (default: %(default)s)")
    p.add_argument("--threshold", type=float, default=0.08,
                   help="dNBR above which a pixel is burnt (default: %(default)s)")
    p.add_argument("--max-nbr-post", type=float, default=0.25,
                   help="post-fire NBR ceiling for the burnt mask; pass -1 to "
                        "disable and threshold on dNBR alone (default: %(default)s)")
    p.add_argument("--out-dir", default="outputs",
                   help="where GeoTIFFs and the figure go (default: %(default)s)")
    p.add_argument("--no-figure", action="store_true", help="skip the PNG")


def cmd_demo(args) -> int:
    scene, truth = synthetic_scene(size=args.size, seed=args.seed,
                                   burn_fraction=args.burn_fraction)
    result = map_burn(scene, scheme=SCHEMES[args.scheme],
                      threshold=args.threshold,
                      max_nbr_post=None if args.max_nbr_post < 0 else args.max_nbr_post,
                      truth=truth, out_dir=args.out_dir)
    print(result.summary())
    if not args.no_figure:
        path = plot_burn_map(result, scene,
                             os.path.join(args.out_dir, "burn_map_demo.png"),
                             title="Burnt area and severity, synthetic savanna scene")
        print("\nfigure: {}".format(path))
    for name, path in sorted(result.written.items()):
        print("{:9s} {}".format(name, path))
    return 0


def cmd_map(args) -> int:
    pre = {"B4": args.pre_b4, "B8": args.pre_b8, "B12": args.pre_b12}
    post = {"B4": args.post_b4, "B8": args.post_b8, "B12": args.post_b12}
    scene = load_scene(pre, post, scale=args.scale)
    result = map_burn(scene, scheme=SCHEMES[args.scheme],
                      threshold=args.threshold,
                      max_nbr_post=None if args.max_nbr_post < 0 else args.max_nbr_post,
                      out_dir=args.out_dir)
    print(result.summary())
    if not args.no_figure:
        print("\nfigure: {}".format(plot_burn_map(
            result, scene, os.path.join(args.out_dir, "burn_map.png"))))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="burnmapper",
        description="Burnt-area and severity mapping from Sentinel-2 using dNBR.")
    sub = ap.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="run on a synthetic scene, no data needed")
    p_demo.add_argument("--size", type=int, default=256)
    p_demo.add_argument("--seed", type=int, default=20260915)
    p_demo.add_argument("--burn-fraction", type=float, default=0.22)
    _add_common(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    p_map = sub.add_parser("map", help="run on real GeoTIFF bands")
    for when in ("pre", "post"):
        for band in ("b4", "b8", "b12"):
            p_map.add_argument("--{}-{}".format(when, band), required=True,
                               metavar="TIF",
                               help="{}-fire {} GeoTIFF".format(when, band.upper()))
    p_map.add_argument("--scale", type=float, default=10000.0,
                       help="reflectance scale factor; Sentinel-2 L2A is "
                            "%(default)s")
    _add_common(p_map)
    p_map.set_defaults(func=cmd_map)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
