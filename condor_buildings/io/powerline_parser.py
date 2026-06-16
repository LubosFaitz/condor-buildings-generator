"""
Powerline parser (SPIKE / read-only) for Condor Buildings Generator.

Exploratory step requested before committing to a powerlines feature: extract
``power=line`` / ``power=minor_line`` ways and their tower/pole nodes from OSM,
project them to Condor patch coordinates, classify each line
(major / regional / minor -> Pylon_Large / Pylon_Medium / Pylon_Small, following
the Condor OSM Importer manual ch.6 by Wiek Schoenmakers), and report cross-patch
behaviour so we can judge whether *systematic* (automated) powerline generation is
feasible BEFORE investing in tower/cable geometry.

This module produces NO mesh and is NOT wired into the main pipeline. Run it
standalone:

    python -m condor_buildings.io.powerline_parser --patch-dir test_data --patch-id 036019

The cross-patch metrics mirror the manual's pain points:
  * supports inside vs outside the patch [-2880, +2880]
  * border *transitions* along the ordered way:
        0 -> fully inside or fully outside (trivial)
        1 -> enters/leaves once (easy: trim the overhanging tail)
       >1 -> crosses the border multiple times (manual 6.4 hard case: you cannot
             just delete the outside vertices or you get one giant cable span)
"""

import argparse
import os
import xml.etree.ElementTree as ET
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

from ..projection import IProjector, create_projector
from ..io.patch_metadata import load_patch_metadata
from ..config import PATCH_HALF

logger = logging.getLogger(__name__)

# OSM ``power`` way-values that represent overhead lines we want to draw.
LINE_VALUES = {"line", "minor_line"}

# OSM ``power`` node-values that represent a physical support (a pylon goes here).
SUPPORT_VALUES = {"tower", "pole", "portal", "terminal"}

# Voltage thresholds (kV) for picking the pylon tier. Wiek prefers classifying by
# the OSM ``voltage`` tag over the line name (group chat 2026-06-02, Q5). His three
# delivered pylons are 150 kV / 100 kV / 50 kV, but real OSM voltages vary, so we
# bucket with tunable thresholds and keep the name/line-type rule as a fallback for
# untagged lines:
#   v >= 110 kV          -> major    -> Pylon_Large   (HV transmission trunk)
#   45 kV <= v < 110 kV  -> regional -> Pylon_Medium  (sub-transmission)
#   v < 45 kV            -> minor    -> Pylon_Small    (distribution)
VOLTAGE_LARGE_KV = 110.0
VOLTAGE_MEDIUM_KV = 45.0

# Powerlines below this voltage are NOT drawn at all. A real low-voltage feeder
# (e.g. a 230 V / 400 V service drop) carries only ~6 m poles that are invisible
# from a glider, and in the UK such lines very often run UNDERGROUND along the
# road. OSM rarely tags them `location=underground`, so we filter by voltage
# instead. Only lines with a KNOWN voltage below this are dropped; untagged lines
# still pass through to the name/line-type classifier. (Andy's patch 003024: five
# `voltage=230` minor_line feeders were being drawn as a line along a road that is
# almost certainly underground, 2026-06-10.)
MIN_DRAWN_VOLTAGE_KV = 1.0


@dataclass
class PowerPoint:
    """A single vertex along a powerline way (a cable bend; maybe a support)."""
    node_id: str
    x: float
    y: float
    in_patch: bool
    is_support: bool                 # tagged power=tower/pole/... -> pylon location
    support_kind: Optional[str]      # the raw OSM value when is_support


@dataclass
class PowerLine:
    """One OSM powerline way, projected to Condor coordinates."""
    way_id: str
    power_value: str                 # 'line' or 'minor_line'
    line_class: str                  # 'major' | 'regional' | 'minor'
    pylon_type: str                  # 'Pylon_Large' | 'Pylon_Medium' | 'Pylon_Small'
    name: Optional[str]
    voltage: Optional[str]
    points: List[PowerPoint]

    @property
    def supports(self) -> List[PowerPoint]:
        return [p for p in self.points if p.is_support]

    @property
    def n_inside(self) -> int:
        return sum(1 for p in self.points if p.in_patch)

    @property
    def n_outside(self) -> int:
        return sum(1 for p in self.points if not p.in_patch)

    @property
    def fully_inside(self) -> bool:
        return self.n_outside == 0 and self.n_inside > 0

    @property
    def fully_outside(self) -> bool:
        return self.n_inside == 0

    @property
    def crosses_border(self) -> bool:
        return self.n_inside > 0 and self.n_outside > 0

    @property
    def border_transitions(self) -> int:
        """inside<->outside flips along the ordered way (see module docstring)."""
        flips = 0
        for a, b in zip(self.points, self.points[1:]):
            if a.in_patch != b.in_patch:
                flips += 1
        return flips

    @property
    def inside_runs(self) -> int:
        """
        Count of contiguous in-patch runs. This is the metric that maps to the
        manual's pain (6.4), not the raw transition count:
          0 -> fully outside
          1 -> one in-patch run; trivial whether it touches 0/1/2 borders
               (keep the run, trim any overhanging tails)
         >1 -> two+ in-patch runs separated by an outside gap; deleting the gap
               would bridge a huge cable, so the line must be trimmed by hand.
        """
        runs = 0
        prev_in = False
        for p in self.points:
            if p.in_patch and not prev_in:
                runs += 1
            prev_in = p.in_patch
        return runs

    @property
    def max_span_m(self) -> float:
        """Longest gap between consecutive vertices (cable span sanity check)."""
        longest = 0.0
        for a, b in zip(self.points, self.points[1:]):
            d = ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5
            if d > longest:
                longest = d
        return longest


@dataclass
class PowerlineParseResult:
    lines: List[PowerLine]
    stats: Dict[str, int]
    warnings: List[str]


def _parse_voltage_kv(voltage: Optional[str]) -> Optional[float]:
    """
    Parse an OSM ``voltage`` tag value to kV.

    Per the OSM convention (Key:voltage) the value is in VOLTS — e.g. ``"230"``
    is 230 V (low-voltage distribution), ``"11000"`` is 11 kV, ``"132000"`` is
    132 kV. It may be a semicolon-separated list for multi-circuit lines (e.g.
    ``"110000;20000"``); we take the highest value (the dominant circuit decides
    the tower tier). An explicit ``kV`` suffix (rare/non-standard) is honoured.
    Returns kV, or None if the tag is missing/unparseable.

    NB: do NOT treat bare small numbers as kV. A real ``voltage=230`` low-voltage
    feeder would otherwise read as 230 kV and get giant transmission towers (large
    pylons) stamped on it every few metres — Andy's patch 003024 had five such
    ``minor_line`` ways rendered as a wall of close-packed large pylons (2026-06-10).
    """
    if not voltage:
        return None
    best = None
    for part in voltage.replace(",", ";").split(";"):
        token = part.strip().lower()
        is_kv = "kv" in token
        token = token.replace("kv", "").replace("v", "").strip()
        if not token:
            continue
        try:
            num = float(token)
        except ValueError:
            continue
        kv = num if is_kv else num / 1000.0
        if best is None or kv > best:
            best = kv
    return best


def _classify(power_value: str, tags: Dict[str, str]) -> Tuple[str, str]:
    """
    Map an OSM powerline way to (line_class, pylon_type).

    Wiek prefers the ``voltage`` tag (Q5); we fall back to the manual's ch.6
    name/line-type heuristic when voltage is absent:
      * voltage >= 110 kV   -> major    -> Pylon_Large
      * 45..110 kV          -> regional -> Pylon_Medium
      * < 45 kV             -> minor    -> Pylon_Small
      * (no voltage) minor_line       -> minor    -> Pylon_Small
      * (no voltage) named line       -> major    -> Pylon_Large
      * (no voltage) unnamed line     -> regional -> Pylon_Medium
    """
    kv = _parse_voltage_kv(tags.get("voltage"))
    if kv is not None:
        if kv >= VOLTAGE_LARGE_KV:
            return ("major", "Pylon_Large")
        if kv >= VOLTAGE_MEDIUM_KV:
            return ("regional", "Pylon_Medium")
        return ("minor", "Pylon_Small")

    if power_value == "minor_line":
        return ("minor", "Pylon_Small")
    if tags.get("name"):
        return ("major", "Pylon_Large")
    return ("regional", "Pylon_Medium")


def parse_powerlines(
    filepath: str,
    projector: IProjector,
    patch_half: float = PATCH_HALF,
) -> PowerlineParseResult:
    """
    Parse powerline ways from an OSM file into projected PowerLine records.

    Args:
        filepath: Path to .osm XML file
        projector: Coordinate projector (lat/lon -> local Condor X/Y)
        patch_half: Half-size of the patch in metres (default 2880)

    Returns:
        PowerlineParseResult with lines, aggregate stats, and warnings
    """
    logger.info(f"Parsing powerlines from: {filepath}")

    tree = ET.parse(filepath)
    root = tree.getroot()

    # Pass 1: node coordinates + which nodes are power supports.
    node_coords: Dict[str, Tuple[float, float]] = {}
    node_power: Dict[str, str] = {}
    support_node_total = 0
    for node_elem in root.findall("node"):
        nid = node_elem.get("id")
        node_coords[nid] = (float(node_elem.get("lat")), float(node_elem.get("lon")))
        for tag in node_elem.findall("tag"):
            if tag.get("k") == "power":
                pv = tag.get("v")
                node_power[nid] = pv
                if pv in SUPPORT_VALUES:
                    support_node_total += 1

    # Pass 2: powerline ways.
    lines: List[PowerLine] = []
    warnings: List[str] = []
    cable_ways = 0
    low_voltage_ways = 0
    for way_elem in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way_elem.findall("tag")}
        power_value = tags.get("power")
        if power_value == "cable":
            cable_ways += 1  # usually underground; counted but not drawn
            continue
        if power_value not in LINE_VALUES:
            continue

        # Drop very-low-voltage feeders (likely underground / invisible from the
        # air). Only when the voltage is KNOWN and below the threshold; untagged
        # lines pass through (the classifier handles them by name/line-type).
        kv = _parse_voltage_kv(tags.get("voltage"))
        if kv is not None and kv < MIN_DRAWN_VOLTAGE_KV:
            low_voltage_ways += 1
            continue

        points: List[PowerPoint] = []
        for nd in way_elem.findall("nd"):
            nid = nd.get("ref")
            if nid not in node_coords:
                warnings.append(f"way {way_elem.get('id')}: missing node {nid}")
                continue
            lat, lon = node_coords[nid]
            x, y = projector.project(lat, lon)
            in_patch = -patch_half <= x <= patch_half and -patch_half <= y <= patch_half
            kind = node_power.get(nid)
            is_support = kind in SUPPORT_VALUES
            points.append(
                PowerPoint(nid, x, y, in_patch, is_support, kind if is_support else None)
            )

        if len(points) < 2:
            warnings.append(f"way {way_elem.get('id')}: < 2 usable points, skipped")
            continue

        line_class, pylon_type = _classify(power_value, tags)
        lines.append(
            PowerLine(
                way_id=way_elem.get("id"),
                power_value=power_value,
                line_class=line_class,
                pylon_type=pylon_type,
                name=tags.get("name"),
                voltage=tags.get("voltage"),
                points=points,
            )
        )

    stats = {
        "support_nodes_total": support_node_total,
        "cable_ways_skipped": cable_ways,
        "lines_low_voltage_skipped": low_voltage_ways,
        "lines_total": len(lines),
        "lines_major": sum(1 for l in lines if l.line_class == "major"),
        "lines_regional": sum(1 for l in lines if l.line_class == "regional"),
        "lines_minor": sum(1 for l in lines if l.line_class == "minor"),
        "supports_on_lines": sum(len(l.supports) for l in lines),
        "supports_inside": sum(
            sum(1 for p in l.supports if p.in_patch) for l in lines
        ),
        "lines_fully_inside": sum(1 for l in lines if l.fully_inside),
        "lines_fully_outside": sum(1 for l in lines if l.fully_outside),
        "lines_crossing_border": sum(1 for l in lines if l.crosses_border),
        # The manual's hard case: a line with 2+ in-patch runs (outside gap in
        # the middle) cannot be auto-trimmed by simply dropping outside vertices.
        "lines_multi_run": sum(1 for l in lines if l.inside_runs > 1),
    }

    logger.info(
        f"Parsed {stats['lines_total']} powerlines "
        f"({stats['lines_major']} major / {stats['lines_regional']} regional / "
        f"{stats['lines_minor']} minor), {stats['supports_on_lines']} supports"
        + (f"; skipped {low_voltage_ways} low-voltage (<{MIN_DRAWN_VOLTAGE_KV:g} kV) feeders"
           if low_voltage_ways else "")
    )

    return PowerlineParseResult(lines=lines, stats=stats, warnings=warnings)


def format_report(result: PowerlineParseResult, patch_id: str = "") -> str:
    """Render a human-readable go/no-go report for the spike."""
    s = result.stats
    out: List[str] = []
    out.append("=" * 64)
    out.append(f" POWERLINE PARSER SPIKE - patch {patch_id or '(unknown)'}")
    out.append("=" * 64)

    out.append("")
    out.append("DATA QUALITY")
    out.append(f"  Powerline ways found ......... {s['lines_total']}")
    out.append(f"    major  (named -> Pylon_Large) .... {s['lines_major']}")
    out.append(f"    regional (-> Pylon_Medium) ....... {s['lines_regional']}")
    out.append(f"    minor  (-> Pylon_Small) .......... {s['lines_minor']}")
    out.append(f"  Support nodes on lines ....... {s['supports_on_lines']} "
               f"({s['supports_inside']} inside the patch)")
    out.append(f"  Support nodes in whole file .. {s['support_nodes_total']} "
               f"(tower/pole/portal/terminal)")
    out.append(f"  Underground cable ways skipped {s['cable_ways_skipped']}")
    out.append(f"  Low-voltage feeders skipped .. {s.get('lines_low_voltage_skipped', 0)} "
               f"(<{MIN_DRAWN_VOLTAGE_KV:g} kV; not drawn)")

    out.append("")
    out.append("CROSS-PATCH (the hard part, per manual 6.4)")
    out.append(f"  Lines fully inside patch ..... {s['lines_fully_inside']}  (trivial)")
    out.append(f"  Lines fully outside patch .... {s['lines_fully_outside']}  (drop)")
    out.append(f"  Lines crossing the border .... {s['lines_crossing_border']}")
    out.append(f"    with 2+ in-patch runs ...... {s['lines_multi_run']}  "
               f"(<- the manual's pain case)")

    out.append("")
    out.append("PER-LINE DETAIL  (runs = in-patch runs; >1 needs manual-style trim)")
    header = f"  {'way_id':>11} {'class':>8} {'pts':>4} {'sup':>4} " \
             f"{'in':>4} {'out':>4} {'xing':>4} {'runs':>4} {'maxspan_m':>10}  name/voltage"
    out.append(header)
    for l in sorted(result.lines, key=lambda x: (-x.inside_runs, -len(x.points))):
        label = l.name or (f"{l.voltage}V" if l.voltage else "-")
        out.append(
            f"  {l.way_id:>11} {l.line_class:>8} {len(l.points):>4} "
            f"{len(l.supports):>4} {l.n_inside:>4} {l.n_outside:>4} "
            f"{l.border_transitions:>4} {l.inside_runs:>4} {l.max_span_m:>10.1f}  {label}"
        )

    if result.warnings:
        out.append("")
        out.append(f"WARNINGS ({len(result.warnings)}):")
        for w in result.warnings[:10]:
            out.append(f"  - {w}")
        if len(result.warnings) > 10:
            out.append(f"  ... and {len(result.warnings) - 10} more")

    out.append("")
    out.append("VERDICT HINTS")
    easy = s["lines_crossing_border"] - s["lines_multi_run"]
    out.append(f"  Lines needing no border work ....... {s['lines_fully_inside']}")
    out.append(f"  Lines auto-trimmable (1 in-run) ..... {easy}")
    out.append(f"  Lines needing manual-style trimming  {s['lines_multi_run']}")
    out.append("=" * 64)
    return "\n".join(out)


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="SPIKE: parse OSM powerlines for a Condor patch (no geometry)."
    )
    parser.add_argument("--patch-dir", required=True,
                        help="Directory with h<patch>.txt metadata")
    parser.add_argument("--patch-id", required=True, help="Patch ID, e.g. 036019")
    parser.add_argument("--osm", default=None,
                        help="OSM file (default: map_21.osm in patch-dir)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    metadata = load_patch_metadata(
        os.path.join(args.patch_dir, f"h{args.patch_id}.txt")
    )
    projector = create_projector(
        metadata.zone_number, metadata.translate_x, metadata.translate_y
    )

    osm_path = args.osm or os.path.join(args.patch_dir, "map_21.osm")
    result = parse_powerlines(osm_path, projector)
    print(format_report(result, patch_id=args.patch_id))


if __name__ == "__main__":
    _main()
