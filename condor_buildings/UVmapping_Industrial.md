# UV Mapping — Industrial Walls

## Textura

- Soubor: `Industrial_Atlas.dds`
- Rozměry: `512 × 9216 px`
- Obsah: stejné fasádní styly jako `Houses_Atlas.dds`, **bez střešní sekce**
- V rozsah: fasády pokrývají celou výšku textury — V `0.0` až `1.0`

## Rozdíl oproti Houses_Atlas

| Atlas | Rozměry | Střechy | Fasády (V) |
|---|---|---|---|
| Houses_Atlas.dds | 512 × 12288 px | V 0.75–1.0 | V 0.0–0.75 |
| Industrial_Atlas.dds | 512 × 9216 px | — (nejsou) | V 0.0–1.0 |

Protože Industrial_Atlas nemá střešní sekci, musí být všechny V souřadnice
přeškálovány faktorem `1 / 0.75 = 1.3333`, aby fasády pokryly celou texturu.

## Přiřazení sekcí podle patra

Každý fasádní styl (index 0–11) má 3 sekce seřazené od spodku atlasu nahoru:

| Patro | Sekce | Popis |
|---|---|---|
| 0 (přízemí) | `ground` | dveře + okna |
| 1 (1. patro) | `upper` | okna |
| 2+ (další patra) | `gable` | horní sekce stylu |

## Implementace v kódu

### generators/uv_mapping.py — `compute_multi_floor_wall_uvs()`

Parametr `v_scale` (default `1.0`) se aplikuje na všechny V hodnoty.
Pro industrial_walls se předává `v_scale = 1.0 / 0.75`.

Přiřazení sekcí:
```python
if floor_idx == 0:   section = 'ground'
elif floor_idx == 1: section = 'upper'
else:                section = 'gable'
```

### generators/walls.py — `generate_walls()`

Konstanta `_INDUSTRIAL_V_SCALE = 1.0 / 0.75` se předává do `_generate_ring_walls()`,
která ji předá dál do `compute_multi_floor_wall_uvs()`.

`generate_walls_lod1()` volá `generate_walls()` — V scaling se přenáší automaticky.

## Ruční oprava v Blenderu (diagnostika)

Pokud je potřeba ručně zkontrolovat nebo opravit UV importovaného objektu,
použij script `uv_fix_industrial_floors.py` v Blender Scripting tabu:

```python
# Skupinuje plochy podle XY pozice, řadí je podle Z (patro),
# a přepisuje V sekce: floor 0=ground, 1=upper, 2+=gable
# Aplikuje v_scale = 1/0.75 pro Industrial_Atlas
```
