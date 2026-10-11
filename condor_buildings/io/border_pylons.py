"""
Shared border-pylon log for Condor Buildings Generator.

A powerline tower standing just beyond a patch border is generated TWICE: once by
the patch that owns the node, and once by the neighbour that keeps the first node
over the seam so its cables continue (see ``generators.powerlines``). Both copies
were stamped, each turned the way its own patch saw fit -- double geometry and
crossed conductors on the seam.

This module is the little notebook the patches share. Whoever builds such a tower
first writes down where it ended up; whoever comes later reads the note, skips the
geometry and only hangs its cables on the tower that is already there. The file
(``border_pylons.json``) sits next to the map data, keyed by OSM node id:

    {"format": 2,
     "pylons": {"123456789": {"patch": "036019", "inside": true,
                              "utm_x": 123456.7, "utm_y": 5432109.8,
                              "z": 412.5, "yaw": 1.2345, "type": "Pylon_Large",
                              "attach": [[123443.0, 5432109.8, 440.3], ...]}}}

Positions are stored ABSOLUTE (``utm = local - translate``), because every patch
has its own local frame; the frames differ by a plain offset only, so a yaw carries
over unchanged and a position just needs ``local = utm + translate`` of the reader.

``attach`` holds the CONDUCTOR END POINTS (the arm tips) of that very tower, in the
same order as the line's attach points and stored absolute just like the position.
The neighbour that only hangs its cables on a tower somebody else built reads them
back instead of recomputing them, so the wires end on the real arms and not beside
them.

Pure Python (no bpy) and deliberately unbreakable: a missing file, a broken JSON, a
locked file or missing rights all mean "no log available" -- a warning is logged and
generation carries on exactly as it did before.
"""

import json
import logging
import math
import os

logger = logging.getLogger(__name__)

# Name of the shared log inside the landscape's map directory.
FILE_NAME = "border_pylons.json"

# On-disk layout version. A file written by any other version is ignored whole.
FORMAT_VERSION = 2

# Self-healing guard: a stored pylon further than this (metres) from where the
# caller's own OSM puts the same node belongs to a different map (or a different
# landscape) and is ignored, so a stale log can never drag a tower off its node.
POSITION_TOLERANCE = 1.0


class BorderPylonLog:
    """The shared note-book for one patch: reads what the neighbours left behind and
    collects what this patch builds, written back out by :meth:`flush`."""

    def __init__(self, path, patch_id, translate_x=0.0, translate_y=0.0):
        self.path = path
        self.patch_id = str(patch_id)
        self.translate_x = float(translate_x)
        self.translate_y = float(translate_y)
        self._pylons = {}
        self._dirty = False
        self._load()

    # -- coordinates ------------------------------------------------------------
    def to_utm(self, x, y):
        """Local patch coordinates -> absolute (patch-independent) coordinates."""
        return (x - self.translate_x, y - self.translate_y)

    def to_local(self, utm_x, utm_y):
        """Absolute coordinates -> the local frame of THIS patch."""
        return (utm_x + self.translate_x, utm_y + self.translate_y)

    # -- reading ----------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return                      # first patch of the landscape: start empty
        except Exception as e:
            logger.warning("Border pylon log: cannot read %s (%s), starting empty",
                           self.path, e)
            return
        if not isinstance(data, dict) or data.get("format") != FORMAT_VERSION:
            logger.warning("Border pylon log: %s has an unknown format, ignored",
                           self.path)
            return
        pylons = data.get("pylons")
        if not isinstance(pylons, dict):
            logger.warning("Border pylon log: %s has no pylon list, ignored", self.path)
            return
        self._pylons = {str(k): v for k, v in pylons.items() if isinstance(v, dict)}

    def _local_attach(self, entry, node_id):
        """The stored conductor end points of one record, in THIS patch's local
        frame. A record written before this field existed (or a damaged one) simply
        has no attach points -- an empty list, never an error."""
        points = entry.get("attach")
        if not points:
            return []
        out = []
        try:
            for p in points:
                lx, ly = self.to_local(float(p[0]), float(p[1]))
                out.append((lx, ly, float(p[2])))
        except Exception as e:
            logger.warning("Border pylon log: broken attach points for node %s (%s)",
                           node_id, e)
            return []
        return out

    def lookup(self, node_id, utm_x, utm_y):
        """The record for one OSM node, converted into THIS patch's local frame.

        Args:
            node_id: OSM node id of the tower.
            utm_x, utm_y: absolute position the CALLER computed from its own OSM
                (see :meth:`to_utm`), used as the self-healing check.

        Returns:
            dict with ``patch``, ``inside``, ``x``, ``y``, ``z``, ``yaw``, ``type``
            and ``attach`` (the conductor end points, empty when the record has
            none), or None when there is no usable record (unknown node, broken
            entry, or the map has moved the node by more than POSITION_TOLERANCE).
        """
        try:
            entry = self._pylons.get(str(node_id))
            if not entry:
                return None
            ex = float(entry["utm_x"])
            ey = float(entry["utm_y"])
            lx, ly = self.to_local(ex, ey)
            return {
                "patch": str(entry.get("patch", "")),
                "in_patch": str(entry.get("in_patch", "")),
                "inside": bool(entry.get("inside", False)),
                "x": lx,
                "y": ly,
                "z": float(entry["z"]),
                "yaw": float(entry["yaw"]),
                "type": entry.get("type", ""),
                "attach": self._local_attach(entry, node_id),
            }
        except Exception as e:
            logger.warning("Border pylon log: broken record for node %s (%s)", node_id, e)
            return None

    # -- writing ----------------------------------------------------------------
    def record(self, node_id, x, y, z, yaw, pylon_type, inside, attach=None):
        """Note down a tower this patch has just built. ``x``/``y`` and the points in
        ``attach`` (the conductor end points on the tower's arms) are LOCAL; they are
        stored absolute so any patch can read them back."""
        try:
            ux, uy = self.to_utm(float(x), float(y))
            points = []
            for p in (attach or ()):
                ax, ay = self.to_utm(float(p[0]), float(p[1]))
                points.append([round(ax, 3), round(ay, 3), round(float(p[2]), 3)])

            # Actual patch where the tower stands (respecting Condor grid direction):
            in_patch = str(self.patch_id)
            if not inside:
                try:
                    col = int(str(self.patch_id)[:3])
                    row = int(str(self.patch_id)[3:])
                    dcol = 1 if float(x) > 2880.0 else (-1 if float(x) < -2880.0 else 0)
                    drow = 1 if float(y) > 2880.0 else (-1 if float(y) < -2880.0 else 0)
                    in_patch = f"{col - dcol:03d}{row + drow:03d}"
                except Exception:
                    pass

            self._pylons[str(node_id)] = {
                "patch": self.patch_id,
                "in_patch": in_patch,
                "inside": bool(inside),
                "utm_x": round(ux, 3),
                "utm_y": round(uy, 3),
                "z": round(float(z), 3),
                "yaw": round(float(yaw), 6),
                "type": pylon_type or "",
                "attach": points,
            }
            self._dirty = True
        except Exception as e:
            logger.warning("Border pylon log: cannot note node %s (%s)", node_id, e)

    def flush(self):
        """Write the log back, atomically (temp file next to it, then replace), so an
        interrupted run can never leave a half-written file behind. Never raises;
        returns True when the file was written."""
        if not self._dirty:
            return False
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"format": FORMAT_VERSION, "pylons": self._pylons},
                          fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            logger.warning("Border pylon log: cannot write %s (%s)", self.path, e)
            try:
                os.remove(tmp)
            except Exception:
                pass
            return False
