# Plán: Lanovky (aerialway) + letecké výstražné koule na el. vedení

Návrh, jak do pluginu přidat **lanovky** a **výstražné koule na vedení**.
**Zatím jen plán, žádný kód.**

Princip lanovek: aerialway má v OSM **stejnou strukturu jako el. vedení**
(trasa = way, stožáry = uzly), takže se **naklonuje modul vedení**
(`io/powerline_parser.py` + `generators/powerlines.py`) a doplní se modely a pár
parametrů. Vše v Pythonu (žádné Geometry Nodes / blosm).

---

## 0) Potvrzené parametry (z návrhu, nejnovější)

### Lanovky — rozestupy a výšky
- **Sedačkové lanovky a vleky:** sedačky/kotvy na lano s rozestupem **15 m**.
- **Kabinkové lanovky:** kabiny s rozestupem **75–80 m** (oběžné gondoly).
- **Výška stožárů:** **oba typy = jako v Blenderu, nescaluje se** (použije se původní
  výška modelu — sedačkový i kabinkový stožár).

### Výstražné koule na el. vedení
- Na **nejvyšším (horním) laně**. Koule má **červeno-bílou šachovnici** (UV
  textura modelu). Koule sedí na laně (kopírují jeho prověšení — vedení se nenarovnává).
- **Je-li nahoře víc lan vedle sebe (stejná výška), koule jsou jen na JEDNOM laně.**
- **Velikost koule ↔ rozestup:**

  | Průměr | Rozestup | Použití |
  |---|---|---|
  | **60 cm** | **30 m** | běžné VN vedení (nejběžnější) |
  | **80 cm** | **35 m** | větší vedení (střední volba, bez konkrétní podmínky) |
  | **130 cm** | **40 m** | extrémní rozpětí (široké řeky, velmi hluboká údolí) |

- **Kde a na čem se osazují:**
  1. **Blízko letiště (2D):** středy letišť z OSM; když trasa vedení leží **do
     3 km** od letiště → koule **60 cm / 30 m**. Přednostně **medium**
     (`Pylon_Medium`); **když u letiště žádné medium není**, použijí se vedení,
     která tam jsou — **small** (a klidně i **large**).
  2. **Přemostění hlubokého údolí / široké řeky (3D):** lano vede **výš než ~45 m
     nad terénem** → koule na **jakémkoli** vedení, velké **130 cm / 40 m**.

### ✅ Vyřešeno
- **Koule: velikost ↔ rozestup** — 60 cm→30 m, 80 cm→35 m, 130 cm→40 m
  (letiště = 60 cm/30 m; údolí/řeka = 130 cm/40 m).
- **Letiště: poloměr 3 km.** Práh **údolí: 45 m** (lano nad terénem).
- **Koule vždy zapnuté** (bez checkboxu) — `warning_balls` default ON, v panelu skryté.
- **Koule slučovat do `pylones`** (materiál `Pylons.dds`).
- **Stožáry obou lanovek: ponechat výšku jako v Blenderu** — nescalují se
  (kabinkový ~24,9 m, sedačkový ve své původní výšce).

---

## 1) OSM data — co se čte

**Lanovky:**
- **Trasa (way):** `aerialway=cable_car | gondola | chair_lift | drag_lift | t-bar | j-bar | platter | mixed_lift | magic_carpet | zip_line`
- **Stožáry (uzel):** `aerialway=pylon` → sem přijde model stožáru.
- **Stanice (uzel/plocha):** `aerialway=station` → volitelně (MVP: vynechat).
- Typ trasy → **kabinková** (cable_car / gondola) vs **sedačková/vlek**
  (chair_lift / drag_lift / t-bar / …) → vybere model + rozestup + výšku.

**Výstražné koule:** berou se z **existujícího vedení** (`power=line`/`minor_line`),
nečtou se z OSM jako objekty — generují se proceduálně (viz bod 6).
Pro podmínku „blízko letiště" se navíc čtou **letiště** z OSM
(`aeroway=aerodrome` — uzel/plocha, střed).

---

## 2) Architektura — co vzniká / co se mění

### Nové soubory
- **`io/aerialway_parser.py`** — podle `powerline_parser.py`: najít `aerialway=*`
  ways a uzly, `aerialway=pylon` = podpěra, projekce do XY patche, `in_patch`,
  přechod hranice (**převzít** z powerline parseru).
- **`generators/aerialway.py`** — podle `powerlines.py`. Znovupoužije generické
  helpery: `_foot_z` (na terén), `_yaw_at` (natočení), `_place_pylon` (orazítkovat
  model), `_add_cable` (lano). Vše do **jednoho objektu `aerialway`**.

### Měněné soubory
- **`generators/powerlines.py`** — přidat **výstražné koule** na nejvyšší lano
  (rozmístění + střídání barev + podmínky letiště/údolí).
- **`io/powerline_parser.py`** (nebo nový malý parser) — načíst **letiště**
  (`aeroway=aerodrome`) pro 2D podmínku.
- **`blender/properties.py`** + **`blender/panels.py`** — checkboxy/slidery
  (lanovky, koule).
- **`blender/operators.py`** / **`main.py`** — napojení (přidat meshe do skupin,
  zkopírovat textury) jako u powerlines.

---

## 3) Modely (assety) — z tvého Blenderu

Šablony jsou v kolekci `Condor_Assets` a **vyexportované jako OBJ do
`assets/pylons/`** (Z nahoru, s UV, bez materiálů):
`Pylon_Aerialway_ns.obj`, `Pylon_AerialCab_ns.obj`, `Aerialway_Cab.obj`,
`Telecabine.obj`, `WarningSphere.obj`. Mapování:

**Origin objektů (důležité — určuje umístění generátorem):**
- **Stožáry** (lanovek i vedení): origin **na patě, na ose** (x=0, y=0, **z=0**) —
  posadí se na terén; attach body lana jsou lokální souřadnice nad patou.
- **Kabina / sedačka** (`Telecabine`, `Aerialway_Cab`): origin na **závěsném bodě
  nahoře** (kde drží lano) — přiloží se na lano a visí pod ním.
- **Výstražná koule** (`WarningSphere`): origin ve **středu koule** — navlékne se
  na lano.


| Účel | Model v Blenderu | Rozměr | Pozn. |
|---|---|---|---|
| **Stožár kabinkové lanovky** | `Pylon_AerialCab_ns` | ~24,9 m | ponechat tuto výšku (nescaluje se) |
| **Kabina (kabinková)** | `Telecabine` | ~5,6 m, 72 v. | rozestup 75–80 m |
| **Stožár sedačkové lanovky** | `Pylon_Aerialway_ns` | ~11 m | celý stožár 1 objekt; lano v Y=±3,29; nescaluje se |
| **Sedačka / kotva** | `Aerialway_Cab` | ~1 m, 20 v. | rozestup 15 m |
| **Výstražná koule** | `WarningSphere` | ~0,12 m, 42 v. | naškálovat na reálný průměr **0,6 / 0,8 / 1,3 m** |
| **Lano** | — (generuje kód) | — | rovné, na vršku stožáru |

`ExampleLine_*` objekty v Blenderu = **hotová ukázková rozpětí** (vzor, jak to má
vypadat), ne šablony.

**Textura / slučování:** všechny modely (stožáry, kabiny, sedačky, koule) mají
**materiál `Pylons.dds`** (stejný jako el. vedení) a **sloučí se do jednoho objektu
`pylones`** dohromady s vedením. Žádný samostatný objekt ani extra textura.
**Koule má červeno-bílou šachovnici** danou UV — ta část je v atlasu `Pylons.dds`.

**Attach body — naměřeno z Blenderu:**

*Sedačková lanovka* (`Pylon_Aerialway_ns` — celý stožár 1 objekt, origin v patě):
- **2 lana** na koncích ramene v **Y = ±3,29 m** (rozteč ~6,6 m), X≈0 (osa jízdy).
- Lano leží na vršku konce ramene (v modelu hlavy Z ≈ 2,7; v celém stožáru =
  výška sloupu + tento offset).
- Sedačka `Aerialway_Cab` (~1 m) visí pod oběma lany; rozestup 15 m.

*Kabinková lanovka* (`Pylon_AerialCab_ns`, `Telecabine`):
- **2 lana** v **Y = ±7,4 m** (rozteč ~14,8 m), na úrovni kladky pylonu
  (v ukázce Z ≈ 22,4 m).
- Lano **rovné, vodorovné** (žádné prověšení).
- Kabina `Telecabine` (výška ~3,7 m) **visí ~1,9 m pod lanem** (závěs nahoře u lana).
- Kabiny **párově na obou lanech ve stejném X**; rozestup po trase **75–80 m**.

---

## 4) Lanovky — jak se staví

- Stožár na každý `aerialway=pylon`, na terén (`_foot_z`), natočený podél trasy.
- **Lano = rovné** (`_add_cable` se sag = 0) mezi vršky sousedních stožárů.
- **Kabiny/sedačky** podél celé trasy v rozestupu:
  - **kabinková → 75–80 m**, model `Telecabine`,
  - **sedačková/vlek → 15 m**, model `Aerialway_Cab`,
  - visí pod lanem, natočené podle směru jízdy.
- **Počet lan:** MVP 1–2 lana na vršku (attach body z modelu).

---

## 5) Výstražné koule na el. vedení — jak se staví

- Na **nejvyšší (horní) lano** rozmístit kouli (`WarningSphere`). Koule má
  **červeno-bílou šachovnici** (UV textura modelu). Sedí na laně (kopírují
  prověšení). Velikost a rozestup jdou spolu: **60 cm → 30 m**, **80 cm → 35 m**,
  **130 cm → 40 m**.
- **Je-li nahoře víc lan vedle sebe (stejná výška) → koule jen na JEDNOM laně.**
- Osadit podle podmínky (ta určí vedení i velikost koule):
  - **Letiště (2D):** trasa do **3 km** od středu letiště (`aeroway=aerodrome`)
    → **60 cm / 30 m**. Přednostně **medium**; když u letiště žádné medium není,
    pak na vedení, která tam jsou (**small**, případně **large**).
  - **Údolí / široká řeka (3D):** lano vede výš než **~45 m** nad terénem
    (výška lana − terén > 45 m; využít `_foot_z` / terénní mesh) → koule na
    **jakémkoli** vedení, velké **130 cm / 40 m**.
- Koule se přidají **do objektu vedení `pylones`** (materiál `Pylons.dds`) — slučují
  se s vedením, ne samostatný objekt.

> Pozn.: tohle je funkce u **el. vedení**, ne u lanovek — zapsáno sem na tvé přání.

---

## 6) UI — ovládací prvky (v panelu)

- **Checkbox „Lanovky"** (vedle „Generate Powerlines").
- **Checkbox „Warning balls"** (výstražné koule na vedení).
- Stožáry se **nescalují** (původní výška modelu) → slider výšky stožáru netřeba.
- Slidery jsou **volitelné** (hodnoty máme pevně dané výše); kdyby ses chtěl hrát:
  rozestup kabin/koulí, vzdálenost od letiště, práh výšky nad údolím.

---

## 7) Na co dát pozor

- **Limit vrcholů c3d (32k/objekt):** kabin/koulí může být hodně → držet modely
  **nízkopoly**, případně dělit jako budovy. Rovné lano šetří vrcholy (bez
  prověšení netřeba dělit → ~6 vrcholů na úsek).
- **Koule – velikost:** model `WarningSphere` je teď ~0,12 m; reálné koule mají
  průměr **0,6 / 0,8 / 1,3 m** → naškálovat na jednu z těchto hodnot.
- **Stanice lanovek:** zatím vynechat.

---

## 8) Fáze

- **Fáze 1:** lanovky — stožáry + rovné lano (kabinková + sedačková), na terén.
- **Fáze 2:** kabiny/sedačky podél lana (rozestupy 75–80 / 15 m).
- **Fáze 3:** výstražné koule na vedení (rozestup 40 m, střídání barev,
  podmínky letiště 3–3 km + údolí 3D).
- **Fáze 4:** stanice, vlastní textury, doladění.

---

## 9) Co potřebuju od tebe

1. **Ukázat v Blenderu attach body** (kde lano sedí na stožáru, kde visí kabina).
2. Můžu ty **modely vyexportovat z Blenderu** do `assets/` (přes naše MCP spojení)?
