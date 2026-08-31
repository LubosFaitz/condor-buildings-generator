# Asset materials — how it must be

Reference for how materials must be named in the models under `assets/`.
Use it as a checklist when adding or replacing a model.

Every OBJ needs **two** lines:

- `mtllib <file>.mtl` — where the materials are
- `usemtl <name>` — which material to use (must come **before** the first `f` line)

The name in `usemtl` must match `newmtl` in that `.mtl` file exactly. **No trailing
numbers** (`pylones.014`, `condor_chimney.002`) — those are Blender leftovers and
point to a material that does not exist in the `.mtl`.

---

## assets/pylons — pylons, aerialways, wind turbines

| Models | mtllib | usemtl | texture |
|---|---|---|---|
| `pylon_large/medium/small`, `pylon_substation`, `*_low`, `Pylon_Aerialway_*`, `Pylon_AerialCab_*`, `Telecabine`, `Aerialway_Cab`, `WarningSphere` | `pylons.mtl` | `pylones` | `Pylons.dds` |
| `turbine_tower`, `turbine_blades`, `*_low` | `turbine.mtl` | `wind_turbine` | `WindTurbine.dds` |

**No `condor_` prefix — the plain object name**, exactly as it is keyed in
`TEXTURE_MAP` (`config.py`): `pylones` → `Pylons.dds`, `wind_turbine` →
`WindTurbine.dds`.

The `condor_` prefix is added by the addon itself after merging: pylons, cables and
aerialways are merged into the object `pylones` → material `condor_pylones` in the
scene; turbines into the object `wind_turbine` → `condor_wind_turbine`.

These OBJ files are also read by the addon's own parser
(`generators/powerlines.py`), which takes the shape only — it never reads `mtllib`
or `usemtl` from them.

---

## assets/3Dobjects — chimneys, transmitters, tree rows

| Models | mtllib | usemtl | texture |
|---|---|---|---|
| `chimney_big`, `chimney_small`, `*_low` | `chimney.mtl` | `condor_chimney` | `Chimney.dds` |
| `transmitter_big`, `transmitter_small` | `transmitter.mtl` | `condor_transmitter` | `transmitter.dds` |
| `tree_rows_1_alley`, `tree_rows_2_hedge`, `tree_rows_3_roadside`, `tree_rows_4_solitary` | `tree_rows.mtl` | `condor_tree` | `tree_rows.dds` |

**With the `condor_` prefix — here it matters.** Chimneys and transmitters are
brought in through Blender's regular OBJ import, not through the addon's own parser.
The name from `usemtl` therefore really shows up in Blender as the material name,
and the code relies on it — after import it removes duplicates named
`condor_chimney.001`, `condor_transmitter.001` and so on. If the asset used a
different name (say plain `chimney`), an unused extra material would be left behind
in the scene that the cleanup no longer catches.

All four tree models share **one** material and **one** texture — each of them is
mapped to its own place in `tree_rows.dds`. They are read by the addon's own parser
(`blender/tree_rows.py`), which takes the shape only, but they carry `mtllib` and
`usemtl` all the same so the model opens correctly when edited by hand in Blender.

`Bridge.dds` and `solar.dds` are textures only — there is no OBJ for them; bridges
and solar panels are built from geometry the addon computes itself.

---

## Fixes applied 2026-07-22

**Chimneys** — all four OBJ files had `newmtl condor_chimney` on line 3 by mistake
(that line belongs in the `.mtl`, not in the `.obj`), so `chimney.mtl` was never
loaded. Replaced with `mtllib chimney.mtl`. The names `condor_chimney.001/.002/.003`
were unified to `condor_chimney`.

**Transmitters** — `transmitter_small.obj` called `condor_transmitter.001`, which is
not in `transmitter.mtl`. Fixed to `condor_transmitter`.

**Pylons** — `pylon_small.obj` pointed at a non-existent `pylon_small.mtl` (fixed to
`pylons.mtl`); `Pylon_AerialCab_ns`, `pylon_large_low`, `pylon_medium_low` and
`pylon_small` called `pylones.014 / .019 / .022 / .023` (unified to `pylones`);
`Aerialway_Cab`, `Pylon_AerialCab_ns_low` and `WarningSphere` had no `usemtl` at all
(added `usemtl pylones`).

**Turbines** — the references were fine, but the material was named `Turbine`, which
matched nothing in the addon. Renamed to `wind_turbine` (the `TEXTURE_MAP` key) so it
follows the same rule as the pylons — in `turbine.mtl` and in all four OBJ files.

In every case **only those lines** changed; the geometry was left untouched.
