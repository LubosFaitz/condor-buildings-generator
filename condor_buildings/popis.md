# Popis úprav — Condor Buildings Generator

Tento dokument popisuje co jednotlivé úpravy dělají a proč byly přidány.

---

## Co bylo přidáno

### Větrné elektrárny (wind turbines)

Plugin nyní umí načíst větrné elektrárny z OSM dat a vložit je do scény jako 3D objekty. Elektrárny jsou v OSM označeny tagem `power=generator`.

---

## Popis úprav po souborech

### config.py — nová textura pro větrné elektrárny

Přidána položka `wind_turbine` do mapy textur. To zajistí, že objekt větrné elektrárny dostane při importu do Blenderu správný materiál s texturou `WindTurbine.dds`.

---

### main.py — zapojení větrných elektráren do pipeline

- Přidána statistika `wind_turbines` — po vygenerování se zobrazí počet nalezených elektráren.
- Přidána nová funkce `_generate_wind_turbines_group()` — načte OSM data, najde uzly s `power=generator`, vygeneruje pro každý 3D model turbíny a vrátí ho jako skupinu objektů pro LOD0 i LOD1.
- Tato funkce se zavolá automaticky při spuštění pipeline pokud je zapnuto generování powerlines.
- Na konci reportu se vypíše počet vygenerovaných turbín.

---

### powerlines.py — šablona a generátor turbín

- Přidán záznam `Wind_Turbine` do seznamu šablon pylonů — plugin bude hledat soubor `turbine.obj` ve složce assets/pylons/.
- Přidána statistika `turbines` do výsledků generování.
- Přidána nová funkce `generate_wind_turbines_mesh()` — pro každou turbínu z OSM dat načte šablonu `turbine.obj`, umístí ji na správné souřadnice v patchi (výška se přizpůsobí terénu) a vrátí seznam MeshData objektů.

---

### powerline_parser.py — parsování turbín z OSM

- Přidána datová třída `WindTurbine` — uchovává ID uzlu, souřadnice X/Y a příznak zda leží uvnitř patche.
- Do výsledku parsování (`PowerlineParseResult`) přidáno pole `turbines` se seznamem nalezených turbín.
- Při parsování OSM souboru se nově prochází všechny uzly (`node`) a hledají se ty s tagem `power=generator` **a zároveň** `generator:source=wind`. Tím se vyloučí ostatní typy generátorů (solární, vodní atd.) a zůstanou pouze větrné turbíny. Každý takový uzel se převede na souřadnice patche a přidá do seznamu turbín.
- Do statistik se přidá celkový počet turbín a počet těch, které leží uvnitř patche.

---

### mesh_converter.py — umístění objektů a záložní textura

- **Umístění objektu:** Pokud má MeshData nastaven atribut `origin` (souřadnice X/Y/Z), objekt se při importu do Blenderu automaticky přesune na tuto pozici. To zajistí správné umístění turbín v terénu.
- **Záložní textura:** Pokud pro název skupiny (např. `wind_turbine_3`) není v mapě textur přímý záznam, plugin se pokusí najít základní název bez čísla (např. `wind_turbine`) a použije jeho texturu. Díky tomu všechny variace objektů sdílí stejnou texturu i bez explicitního záznamu pro každou.

---

### osm_downloader.py — stahování turbín z Overpass API

Do dotazu na Overpass API přidán požadavek na uzly `power=generator` + `generator:source=wind`. Stahují se tedy pouze větrné elektrárny — ostatní typy generátorů se nestáhnou.

---

### properties.py — slider pro otáčení turbín

- Přidán import `math`.
- Přidány pomocné funkce pro čtení a nastavení rotace (`_get_wind_turbine_rotation`, `_set_wind_turbine_rotation`) — zajistí, že slider v panelu vždy zobrazuje aktuální rotaci vybraného objektu a při posunu slidu otočí všechny vybrané turbíny o stejný rozdíl.
- Přidána property `wind_turbine_rotation` — slider od 0° do 360° pro otáčení turbín kolem osy Z přímo z panelu.

---

### panels.py — nová tlačítka v UI

- **Tlačítko Export Terrain** — zobrazí se v sekci Single Patch. Exportuje upravený terén do záložní složky `modified/`.
- **Slider rotace turbín + tlačítko Merge** — zobrazí se v sekci Powerlines, ale pouze pokud jsou v scéně nějaké objekty větrných elektráren. Slider umožňuje otáčet vybrané turbíny, tlačítko sloučí všechny turbíny do jednoho objektu.

---

### operators.py — nové operátory a úpravy

#### Import terénu při Generate Buildings
Při kliknutí na Generate Buildings v režimu Single Patch se automaticky importuje terénní mesh (`h{patch_id}.obj`):
- Nejdřív zkontroluje složku `Heightmaps/modified/` — pokud tam soubor existuje, importuje upravený terén.
- Pokud ne, importuje originální terén z `Heightmaps/`.
- Terén se uloží do kolekce `Patch_Terrain` a přiřadí mu se textura `t{patch_id}.dds` z krajiny.
- Pokud terén v scéně již existuje (`TR3{patch_id}`), import se přeskočí.

#### Tlačítko Export Terrain
Nový operátor `CONDOR_OT_export_terrain`:
- Exportuje objekt `TR3{patch_id}` (terén aktuálního patche) do složky `Heightmaps/modified/`.
- Originální soubor v `Heightmaps/` se **nikdy nepřepíše**.
- Slouží k uložení vlastních úprav terénu před dalším importem.

#### Tlačítko Merge wind_turbine
Nový operátor `CONDOR_OT_merge_wind_turbines`:
- Sloučí všechny objekty s názvem `wind_turbine` nebo `wind_turbine_*` do jednoho objektu.
- Aplikuje transformace (poloha, rotace, měřítko).
- Přiřadí výslednému objektu materiál `condor_wind_turbine`.
- Odstraní duplicitní materiály `condor_wind_turbine_0`, `condor_wind_turbine_1` atd.

#### Validace meshe po importu (mesh.validate)

Po každém importu objektů do scény se automaticky spustí `mesh.validate()` na všechny naimportované objekty daného patche. Funkce odstraní degenerované hrany (hrana která začíná a končí ve stejném bodě) a poškozené plochy (plochy s duplicitními nebo prolnutými body). Tato geometrie vzniká u některých budov s problematickým půdorysem v OSM datech. Validace geometrii ani normály nepoškodí — odstraní jen neplatné prvky. Pokud jsou opravy provedeny, počet opravených objektů se vypíše do konzole Blenderu.

#### Tlačítko Export Condor OBJ+MTL
Upravený operátor `CONDOR_OT_export_condor`:
- **Nespouští pipeline**, nestahuje OSM, nic negeneruje.
- Přečte všechny mesh objekty z kolekce `Condor_{landscape}_{patch}` v outlineru (nebo `_LOD1` variantu).
- Exportuje přesně to co je v kolekci — včetně vlastních úprav, sloučených turbín nebo ručně přidaných objektů.
- Pokud kolekce neexistuje, zobrazí chybu a vyzve ke spuštění Generate Buildings.
- Scéna se nijak nemění — žádný import zpět do Blenderu.

---

### msprint.py — doplnění budov z Microsoftu

#### Checkbox „MSprint - add buildings"

V sekci **OSM Data** přibyl checkbox viditelný pro oba zdroje dat (Download i Local). Když je zaškrtnutý, plugin před generováním doplní do OSM souboru budovy z **Microsoft Global ML Building Footprints** — satelitně detekované půdorysy, které v OSM chybí.

#### Jak to funguje:

**Checkbox zapnutý:**
- Pokud soubor `MSprint/map_{patch_id}.osm` již existuje (z minulého stažení), použije se přímo — nic se nestahuje.
- Pokud neexistuje, plugin stáhne index (`dataset-links.csv`) a tile data (`.csv.gz`) z Microsoftu, vyfiltruje budovy patche a uloží `MSprint/map_{patch_id}.osm`. Po uložení se `.gz` soubory smažou (šetří místo), `dataset-links.csv` zůstane pro příští použití.
- Plugin porovná každou MS budovu s existujícími OSM budovami pomocí testu překryvu obrysů (ray-casting) — pokud střed nebo roh MS budovy leží uvnitř OSM budovy, nebo naopak, považuje ji za duplikát a nepřidá ji. Tím se eliminují duplicity i u velkých budov, jejichž střed je daleko od MS budovy, ale MS budova fyzicky leží uvnitř nich.
- Nové budovy se přidají do `map_{patch_id}.osm`. Záloha čistého Overpass souboru se uloží jako `map_{patch_id}.osm.ori` (pouze při prvním sloučení).
- Opakované spuštění nikdy nepřidá MS budovy dvakrát — sloučení vychází vždy z `.ori` zálohy.

**Checkbox vypnutý:**
- Pokud existuje záloha `.ori`, plugin ji automaticky obnoví zpět do `map_{patch_id}.osm` před generováním.
- Generuje se tedy z čistých Overpass dat bez MS budov.
- Záloha `.ori` se nikdy nesmaže.

Po dokončení generování se do konzole Blenderu vypíše souhrnný řádek, např.:
`[Condor] Generování dokončeno: 312 objektů, 1 patch(ů), 4821ms | MSprint přidáno: 127`
Pokud MSprint nebyl použit nebo nepřidal žádné budovy, část `MSprint přidáno` se nezobrazí.

---

### panels.py — panel "Other objects"

Pod sekci Advanced přibyl nový zavíratelný sub-panel **Other objects**. Zatím obsahuje jednu kategorii:

- **Chimney** — dva tlačítka vedle sebe: **Import** a **Merge**.

Struktura umožňuje v budoucnu přidávat další typy objektů pod stejný panel.

---

### operators.py — import a sloučení komínů

#### Tlačítko Import (komíny)
Nový operátor `CONDOR_OT_import_chimneys`:
- Přečte existující OSM soubory pro aktuálně nastavené patche (nestahuje nic nového).
- Najde všechny OSM elementy s tagem `man_made=chimney` — jak nody (bod), tak waye (plocha jako centroid).
- Přečte výšku komína z OSM tagu `height` (výchozí 30 m).
- Převede zeměpisné souřadnice na souřadnice scény přes `TransverseMercatorProjector`.
- Z-souřadnici (výška nad terénem) zjistí ray_castem přímo na terénní objekt `TR3{patch_id}` — ray_cast testuje **pouze mesh terénu**, budovy ve scéně se ignorují. Pokud terén v scéně není, komíny se umístí na Z=0.
- Výška ≥ 31 m → model `chimney_big.obj`, výška < 31 m → model `chimney_small.obj` (z `assets/3Dobjects/`).
- Každý komín dostane název `Chimney_{patch_id}_{pořadí}` (např. `Chimney_036024_001`) a custom property `patch_id`.
- Objekty se roztřídí do dvou kolekcí:
  - `chimney_big` — komíny s velkým modelem
  - `chimney_small` — komíny s malým modelem
- Kolekce se vytvoří pouze pokud jsou nalezeny odpovídající komíny (pokud OSM neobsahuje žádné komíny, žádná kolekce nevznikne).

---

### utils/triangulation.py — oprava střech budov s dvorkem

#### Příčina problému

Budova s dvorkem (inner ring v OSM multipolygon relation) dostávala plochou střechu přes celý půdorys místo prstencové. Stěny se generovaly správně (generátor stěn používá outer i inner ring), ale střecha padala do fallbacku.

Bridge-and-earclip algoritmus pro sloučení inner ringu s outer ringem je křehký na nekonvexních půdorysech (L-tvar, U-tvar, nepravidelné blokové budovy) — typické v reálných OSM datech. Výsledkem byl self-intersecting polygon, který ear-clipping nedokázal zpracovat.

#### Opravy

**Přepsána `triangulate_with_holes()`** na tříkrokový fallback — zkouší strategie v pořadí, přechází na další jen při selhání:

1. **`_triangulate_blender()`** — používá Blenderovu nativní funkci `mathutils.geometry.tessellate_polygon`. Vždy dostupná při běhu uvnitř Blenderu, zvládá polygony s více dírami nativně, nejrobustnější varianta.

2. **`_triangulate_earcut()`** — záloha přes knihovnu `mapbox_earcut` (pro CLI/testovací prostředí bez Blenderu).

3. **`_triangulate_with_holes_legacy()`** — původní bridge-and-earclip algoritmus jako poslední záloha.

**Přidána `_strip_closing_vertex()`** — nová pomocná funkce, která odstraní duplicitní závěrný vrchol (první == poslední) z OSM ringů před triangulací, čímž předejde degenerované geometrii.

---

#### Tlačítko Merge (komíny)
Nový operátor `CONDOR_OT_merge_chimneys`:
- Najde všechny objekty `Chimney_*` ve scéně a seskupí je podle custom property `patch_id`.
- Pro každý patch: sloučí příslušné komíny do jednoho objektu, aplikuje všechny transformace.
- Výslednému objektu přiřadí materiál `condor_chimney` a odstraní staré materiály komínových modelů.
- Sloučený objekt přesune do kolekce `Condor_{landscape}_{patch_id}` (kde jsou ostatní objekty patche).
- Pokud patch kolekce neexistuje, objekt zůstane ve scene root.
- Po dokončení odstraní prázdné kolekce `chimney_big` a `chimney_small`.
