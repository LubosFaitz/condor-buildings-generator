# Condor Buildings Generator — Kompletní manuál

Plugin pro Blender generující 3D budovy pro letecký simulátor Condor. Data o budovách bere z OpenStreetMap, terén z heightmap souborů Condoru. Výsledné OBJ/MTL soubory jdou přímo do Working/Autogen.

---

## Panel: Condor Settings

### Condor Directory
Cesta ke kořenové složce Condoru (např. `C:\Condor3`). Musí existovat podsložka `Landscapes`. Bez správné cesty nelze nic spustit — všechna tlačítka jsou zablokována.

### Landscape
Rozbalovací seznam, který se automaticky naplní z podsložky `Landscapes`. Plugin přečte obsah složky a zobrazí pouze ty krajiny, které mají podsložku `Working`. Pokud žádná taková krajina neexistuje nebo je cesta špatná, seznam zobrazí jen „-- Select Landscape --".

Při nevyplněné cestě nebo nevybrané krajině se zobrazí červená ikonka s chybovým textem přímo v panelu.

---

## Panel: Patch Selection

Patch je čtvercový dílek krajiny v Condoru. ID patche je šestimístné číslo ve formátu `XXXYYY` (první tři číslice = souřadnice X, druhé tři = souřadnice Y), například `035023`.

### Přepínač Single Patch

Přepíná mezi dvěma režimy zadávání patchů.

---

## Režim: Single Patch (přepínač zapnutý)

### Pole Patch ID
Pole pro ruční zadání šestimístného ID patche (max. 6 znaků). Například `035023`.

### Checkbox tr3f
Malý zaškrtávací rámeček vedle pole Patch ID. Mění odkud se bere terén a jak se pojmenovávají objekty:

**tr3f ODŠKRTNUTO (výchozí):**
- Terén se hledá nejprve v `Working/Heightmaps/modified/h{patch_id}.obj`
- Pokud tam není, vezme se `Working/Heightmaps/h{patch_id}.obj`
- Terénní objekt v Blenderu se pojmenuje `TR3{patch_id}` (např. `TR3035023`)
- Materiál terénu se pojmenuje stejně: `TR3{patch_id}`

**tr3f ZAŠKRTNUTO:**
- Terén se hledá výhradně v `Working/Heightmaps/22.5m/h{patch_id}.obj`
- Pokud soubor v 22.5m složce neexistuje → operace okamžitě zastaví s chybou, nic se neimportuje
- Terénní objekt v Blenderu se pojmenuje `TR3f{patch_id}` (např. `TR3f035023`)
- Materiál terénu se pojmenuje stejně: `TR3f{patch_id}`

---

### Tlačítko Import Patch (Single Patch mód)

Importuje terén a soubory budov pro jeden patch. Přesný postup krok za krokem:

**1. Přepnutí workspace**
Plugin přepne Blender na workspace „Layout" (pokud existuje a ještě na něm není).

**2. Import terénu**
Zkontroluje, jestli terénní objekt (`TR3{patch_id}` nebo `TR3f{patch_id}` podle stavu tr3f) již existuje v kolekci `Patch_Terrain`.

Pokud neexistuje, importuje OBJ soubor terénu (cesta závisí na tr3f — viz výše). OBJ se importuje s osami `forward=Y, up=Z`. Naimportovaný objekt dostane správné jméno.

Po importu terénu plugin nastaví texturu:
- Vytvoří nový materiál se jménem terénního objektu
- Do materiálu přidá texturu z `Landscapes/{krajina}/Textures/t{patch_id}.dds` (ortofoto)
- Sestaví shader: Texture Image → Principled BSDF → Material Output
- Pokud soubor `.dds` neexistuje, materiál se vytvoří bez textury (slot zůstane prázdný)
- Vypočítá UV mapu z rozměrů sítě — normalizuje souřadnice X a Y na rozsah 0–1

**3. Kontrola OBJ budov (jen při tr3f)**
Pokud je tr3f zaškrtnuto, plugin zkontroluje jestli existuje alespoň jeden ze souborů v `Working/Autogen/`:
- `o{patch_id}.obj`
- `o{patch_id}_LOD1.obj`

Pokud ani jeden neexistuje → nastaví viewport (viz bod 6) a zobrazí varování „File not found in Autogen". Operace skončí, budovy se neimportují, ale terén zůstane ve scéně a pohled se nastaví správně.

**4. Import OBJ budov**
Hledá soubory budov v `Working/Autogen/`:
- `o{patch_id}.obj` → importuje do kolekce `Condor_{krajina}_{patch_id}`
- `o{patch_id}_LOD1.obj` → importuje do kolekce `Condor_{krajina}_{patch_id}_LOD1`
- Existují-li oba soubory, importují se oba

Pokud ani jeden soubor neexistuje → nastaví viewport a zobrazí varování. Operace skončí.

Každý importovaný OBJ jde do vlastní kolekce. Pokud kolekce ještě neexistuje, vytvoří se. OBJ se importuje s osami `forward=X, up=Z`.

Po importu plugin opraví názvy materiálů: pokud je název objektu v interním mapování textur, materiál se přejmenuje na `condor_{název_objektu}`. Tím se zabrání duplicitám a zajistí správné napojení textur.

**5. Hledání chybějících textur**
Po importu všech OBJ plugin prohledá složku `Working/Autogen/Textures/` a pokusí se dohledat texturové soubory, které Blender nenašel automaticky.

**6. Nastavení viewportu**
Bez ohledu na to, jestli se budovy importovaly nebo ne, na konci (nebo před předčasným ukončením) plugin nastaví:
- Stínování: **Material Preview** (MATERIAL)
- Ohnisková vzdálenost: **50 mm**
- Clip Start: **0.01 m**
- Clip End: **100 000 m**
- Vzdálenost pohledu: **9051 m** (zobrazí celý patch)
- Rotace pohledu: **shora dolů** (Euler 0°, 0°, 0°)
- Uzamčení pohledu: na terénní objekt (`TR3{patch_id}` nebo `TR3f{patch_id}`) — viewport se automaticky centruje na terén

---

### Tlačítko Export Terrain (Single Patch mód)

Exportuje terénní objekt ze scény zpět do souboru OBJ.

**tr3f ODŠKRTNUTO:**
- Hledá objekt pojmenovaný `TR3{patch_id}`
- Pokud nenajde → chyba, nic se neexportuje
- Exportuje do `Working/Heightmaps/modified/h{patch_id}.obj`

**tr3f ZAŠKRTNUTO:**
- Hledá objekt pojmenovaný `TR3f{patch_id}`
- Pokud nenajde → chyba, nic se neexportuje
- Exportuje do `Working/Heightmaps/22.5m/modified/h{patch_id}.obj`

Složka `modified` se vytvoří automaticky, pokud neexistuje. Export používá osy `forward=Y, up=Z`, exportuje triangulovanou síť, normály, UV mapy a materiály. Při dalším importu plugin vždy upřednostní soubor z `modified` před originálem.

---

## Režim: Range (přepínač vypnutý)

Zadává se rozsah souřadnic patchů pomocí čtyř polí:
- **X Min / X Max** — rozsah X souřadnice
- **Y Min / Y Max** — rozsah Y souřadnice

Plugin zobrazí celkový počet patchů, které budou zpracovány (např. „Patches: 6 (2×3)").

### Checkbox terrain
Zobrazí se pouze v range módu. Když je zaškrtnutý, importuje se terén pro každý patch v rozsahu (viz postup níže).

### Tlačítko Import Patch (Range mód)

Importuje OBJ budov a volitelně terén pro všechny patche v zadaném rozsahu. Přesný postup:

**Pro každý patch v pořadí (Y vnější smyčka, X vnitřní):**

1. **Import terénu** (jen pokud je zaškrtnutý checkbox terrain)

   Zkontroluje, jestli objekt `TR3{patch_id}` již existuje v kolekci `Patch_Terrain`.
   Pokud ne, hledá:
   - Nejprve `Working/Heightmaps/modified/h{patch_id}.obj`
   - Pokud nenajde, zkusí `Working/Heightmaps/h{patch_id}.obj`

   Pokud soubor existuje:
   - Importuje do kolekce `Patch_Terrain` (vytvoří ji pokud chybí)
   - Přejmenuje objekt na `TR3{patch_id}`
   - Vytvoří materiál `TR3{patch_id}` s texturou `t{patch_id}.dds` ze složky Textures krajiny
   - Sestaví shader: Texture Image → Principled BSDF → Material Output
   - Vypočítá UV mapu z rozměrů sítě

   Pokud soubor vůbec neexistuje → terén pro tento patch se přeskočí, pokračuje se importem budov.

2. **Import OBJ budov**

   Hledá soubory v `Working/Autogen/`:
   - `o{patch_id}.obj` → importuje do kolekce `Condor_{krajina}_{patch_id}`
   - `o{patch_id}_LOD1.obj` → importuje do kolekce `Condor_{krajina}_{patch_id}_LOD1`

   Pokud ani jeden soubor neexistuje → zaznamená varování pro tento patch a přeskočí na další. Ostatní patche se zpracují normálně.

   Po importu opraví názvy materiálů na `condor_*` varianty.

**Po zpracování všech patchů:**

3. **Hledání chybějících textur**
   Prohledá `Working/Autogen/Textures/` a dohledá soubory které Blender nenašel.

4. **Pozicování patchů** (jen pokud je zaškrtnutý checkbox terrain)

   První patch (nejmenší X a Y) zůstane na místě (offset 0, 0). Každý další patch se posune o násobek 5 760 m (přesná velikost jednoho Condor patche):
   - Posun v ose X: `-(x - x_min) × 5760 m`
   - Posun v ose Y: `(y - y_min) × 5760 m`

   Posouvají se jak objekty v kolekci budov (**LOD0 i LOD1** — kolekce `Condor_{krajina}_{patch_id}` i `…_LOD1`), tak terénní objekt daného patche — takže terén a budovy zůstanou na správném relativním místě vůči sobě.

5. **Nastavení viewportu**

   Material Preview, ohnisková vzdálenost 50 mm, Clip Start 0.01 m, Clip End 100 000 m, pohled shora, vzdálenost 9051 m.

   Uzamčení pohledu:
   - Je-li zaškrtnutý terrain → pohled se uzamkne na terén prvního patche (`TR3{první_patch_id}`)
   - Není-li zaškrtnutý terrain → pohled se uzamkne na první mesh objekt z kolekce budov prvního naimportovaného patche

6. **Výsledek**
   - Pokud se naimportoval alespoň jeden patch → „Imported N patch(es)"
   - Pokud se nenaimportoval žádný patch → chyba „No patches imported — check OBJ files"
   - Varování pro patche bez OBJ se zobrazí v záhlaví (zobrazí se max. 5 varování)

---

## Panel: OSM Data

### OSM Source
Přepínač zdroje dat o budovách:

- **Download from Overpass** — stáhne data z internetu přes Overpass API (OpenStreetMap). Stažená data se uloží jako `map_{patch_id}.osm` do složky Working. Při dalším spuštění se použije tento soubor, pokud existuje.
- **Local OSM File** — použije existující soubor `map_{patch_id}.osm` ze složky Working. Vhodné pro opakované generování bez internetu.

### MSprint — add buildings
Po zaškrtnutí plugin po stažení OSM dat přidá chybějící budovy z Microsoft Global Building Footprints. Funguje takto:
- Stáhne komprimované GZ soubory s budovami pro dané území (jednou, pak se cachují lokálně)
- Přidá budovy z Microsoftu, které v OSM chybí
- Výsledek (OSM + Microsoft) sloučí do jednoho souboru a použije pro generování

---

## Panel: Output

### LOD Level
Určuje jaké úrovně detailu se vygenerují:

- **LOD0 (Detailed)** — detailní síť budov s přesahem střechy 0,5 m za zdi. Soubor `o{patch_id}.obj`.
- **LOD1 (Simple)** — zjednodušená síť bez přesahu střechy. Soubor `o{patch_id}_LOD1.obj`.
- **Both LODs** — vygeneruje a uloží oba soubory. Do Blenderu se importují jako dvě samostatné kolekce.

### Save to Autogen
Uloží vygenerované OBJ a MTL soubory do `Working/Autogen/`. Zapnuto ve výchozím stavu. Pokud vypnuto, soubory se nevygenerují na disk — pouze se zobrazí v Blenderu.

### Import to Blender
Po vygenerování naimportuje výsledné soubory do Blenderova viewportu. Zapnuto ve výchozím stavu.

**Když je vypnuto (file mode):** budovy se nezobrazí v Blenderu, ale rovnou se zapíšou na disk jako soubory **připravené přímo pro Condor** — stejně jako tlačítko Export Condor OBJ+MTL:
- vedle `o{patch_id}.obj` se vždy zapíše i `o{patch_id}.mtl` (materiály a textury z interní tabulky TEXTURE_MAP)
- střechy jsou hotové správně — sedlové zdvojené pro oboustranné zobrazení, valbové se správnými normálami
- větrné turbíny se automaticky pootočí o jeden náhodný úhel kolem vlastní osy, sloučí se do jednoho objektu a přidají do OBJ; úhel je **stejný pro LOD0 i LOD1** téhož patche
- pokud je v sekci *Other objects* zaškrtnuto Chimney **Batch**, přidají se do OBJ i komíny (viz níže)
- textury použitých objektů se zkopírují do `Working/Autogen/Textures/` i ve file-mode — atlasy budov a střechy, a navíc **`Pylons.dds`** (pylóny/vedení/lanovky) a **`WindTurbine.dds`** (turbíny)

---

## Panel: Powerlines

### Generate Powerlines
Zapnutím se z OSM dat vygenerují — **vždy pokud jsou pro daný patch v datech** — tři druhy infrastruktury, vše sloučené do jednoho objektu `pylones` se sdílenou texturou `Pylons.dds` (součást stejného OBJ/MTL jako budovy):

- **Elektrické vedení** (`power=line`, `power=minor_line`) — na každém uzlu 3D pylón, mezi uzly kabely s průhybem (catenary) a výstražné koule. Pro LOD1 se použijí jen velké/střední pylóny (bez drátů, koulí a malých pylónů), volitelně model `pylon_large_low.obj`.
- **Větrné elektrárny** (`power=generator`, vítr) — sloup + vrtule rozdělené na dva modely; každá vrtule se **náhodně pootočí** (deterministicky), stejně pro LOD0 i LOD1.
- **Lanovky** (`aerialway=*`) — viz níže.

Pozn.: dřívější samostatný checkbox „Aerialways" byl **zrušen** — lanovky teď spadají pod tenhle jeden checkbox. Hned pod boxem Powerlines je rozbalovací sekce **Other objects** (komíny, viz dole).

### Lanovky (aerialway)
Z OSM cest `aerialway=*` (kabinkové i sedačkové lanovky) se vygenerují stožáry, rovné lano a zavěšené kabiny/sedačky:
- **Stožáry** — kabinkový (`Pylon_AerialCab`) nebo sedačkový (`Pylon_Aerialway`), natočené podél trasy, patou přesně na terénu. Stožáry zasahující za okraj patche berou výšku z **terénu sousedního patche**, aby na něj navazovaly.
- **Kladky** na vršku stožáru se naklánějí podle **sklonu lana** (do kopce / z kopce).
- **Lano** vede přes vršky kladek; jsou dvě (tam a zpět).
- **Kabiny / sedačky** visí z lana v pravidelných rozestupech (kabiny ~77,5 m, sedačky 15 m). Kabiny jsou pootočené o 180° tak, aby závěs k lanu mířil ven (přejede přes kladky).
- **LOD1:** kabinkový stožár použije odlehčený model `Pylon_AerialCab_ns_low.obj` (pokud je ve složce `assets/pylons`), jinak spadne zpět na detailní; ostatní části jsou v obou LODech stejné.

Vše se sloučí do objektu `pylones`. Když má patch lanovky, v logu (viz „Log generování") se u objektu napíše `pylones (aerialway)`.

### Wind Turbine Rotation
Slider se zobrazí pouze pokud je ve scéně objekt pojmenovaný `wind_turbine` nebo `wind_turbine_*`. Ovládá rotaci vybraných větrných turbín kolem osy Z (0°–360°). Funguje pouze pro vybrané (označené) objekty turbín.

### Merge wind_turbine (tlačítko)
Zobrazí se pouze pokud jsou ve scéně objekty `wind_turbine` nebo `wind_turbine_*`. Sloučí všechny turbíny do jednoho objektu připraveného pro Condor export.

**Postup krok za krokem:**

1. Najde všechny objekty se jménem `wind_turbine` nebo začínajícím `wind_turbine_` v celé scéně (nejen viditelné).
2. Rozdělí je podle toho, ve které kolekci `Condor_*` se nachází — každý patch se slučuje zvlášť.
3. Pro každý patch: vybere všechny turbíny tohoto patche a sloučí je do jednoho objektu (`Join`).
4. Výsledný objekt pojmenuje `wind_turbine`.
5. Aplikuje transformace (`Apply transforms: location, rotation, scale`):
   - **Před merge:** každý objekt turbíny má vlastní origin přesně na svém místě ve světě — X a Y jsou GPS souřadnice turbíny přepočtené do metrů, Z je výška terénu v daném bodě (základna turbíny sedí na terénu). **Proto funguje slider Wind Turbine Rotation** — otáčí turbínu kolem její vlastní osy Z, která prochází originem. Kdyby byl origin jinde (např. v nule scény), rotace by kroužila turbínu kolem středu scény, ne kolem jejího středu.
   - **Po merge a apply:** origin sloučeného objektu se přesune do bodu (0, 0, 0) světových souřadnic. Geometrie všech vrcholů má absolutní světové souřadnice zapečeny přímo do sítě. Rotace každé turbíny je zapečena do geometrie. Od tohoto bodu nelze jednotlivé turbíny samostatně otáčet — jsou součástí jednoho objektu. Tím je objekt připraven pro OBJ export — Condor potřebuje absolutní souřadnice v síti, ne transformace objektu.
6. Smaže všechny materiály a přiřadí jeden materiál `condor_wind_turbine`.
7. Přesune objekt do kolekce `Condor_{krajina}_{patch_id}`.
8. Odstraní duplicitní materiály (`condor_wind_turbine.001`, `.002` atd.) které vznikly při importu nebo předchozích operacích.

---

## Tlačítko Generate Buildings (velké tlačítko)

Hlavní operace pluginu — stáhne data, vygeneruje budovy a importuje je. Tlačítko je aktivní pouze pokud jsou vyplněny: Condor Directory, Landscape a Patch ID (nebo platný rozsah X/Y). Během zpracování se zobrazí text „Processing {patch_id}...".

**Postup pro každý patch:**

1. Zkopíruje chybějící textury z pluginu do `Working/Autogen/Textures/` (Roof1–6.dds, Houses_Atlas.dds, Highrise_Atlas.dds, Industrial_Atlas.dds).

2. **V Single Patch módu:** Importuje terén stejně jako tlačítko Import Patch (viz výše, včetně podpory tr3f).

3. **V Range módu s terrain checkboxem:** Importuje terén pro každý patch stejně jako Range mód Import Patch.

4. Pro každý patch:
   - Načte metadata z `Working/Heightmaps/h{patch_id}.txt` (výškové souřadnice, rozměry). Pokud soubor chybí → patch se přeskočí.
   - Stáhne nebo načte OSM data (`map_{patch_id}.osm`)
   - Volitelně přidá budovy z Microsoftu (MSprint)
   - Spustí pipeline: parsování půdorysů → klasifikace budov → generování střech a stěn → seskupení do objektů podle typu materiálu
   - Uloží OBJ+MTL do `Working/Autogen/` (pokud je Save to Autogen zapnuto)
   - Naimportuje do Blenderu (pokud je Import to Blender zapnuto)

5. Po zpracování všech patchů nastaví viewport (Material Preview, pohled shora, uzamčení na terén).

6. Zobrazí statistiky: počet budov, počet patchů, čas zpracování v ms.

---

## Výšky budov a počet podlaží

Výška každé budovy se určuje v tomto pořadí priority:

1. **OSM tag `height`** — přímá výška v metrech (např. `height=12.5`). Pokud existuje, použije se přímo.
2. **OSM tag `building:levels`** — počet podlaží z OSM. Výška = podlaží × 3 m. Pokud existuje zároveň s `height`, `height` má přednost.
3. **Odhad podle kategorie a plochy** — pokud OSM neobsahuje žádný výškový tag:

| Kategorie | Podmínka | Podlaží | Výška |
|---|---|---|---|
| HOUSE | vždy | 2 | 6 m |
| INDUSTRIAL | vždy | 1 | 6 m (vysoké stropy) |
| APARTMENT | plocha > 500 m² | 4 | 12 m |
| APARTMENT | plocha > 200 m² | 3 | 9 m |
| APARTMENT | ostatní | 2 | 6 m |
| COMMERCIAL | plocha > 200 m² | 2 | 6 m |
| COMMERCIAL | ostatní | 1 | 3 m |
| OTHER | plocha > 300 m² | 3 | 9 m |
| OTHER | plocha > 100 m² | 2 | 6 m |
| OTHER | ostatní | 1 | 3 m |

**Ochrana proti chybám v OSM:** Pokud tag `building:levels` obsahuje nesmyslnou hodnotu (např. překlep `233` místo `3`), plugin ji ořízne na maximum 60 podlaží a výšku na odpovídající limit. Taková budova se zaznamená jako varování.

**Synchronizace výšky a podlaží:** Pokud `building:levels` přepíše odhadovaný počet podlaží ale není zadána explicitní výška, výška se přepočítá jako `podlaží × 3 m`. Tím se zabrání nesouladu (např. dům s 1 podlažím odhadnut na 6 m by měl roztaženou texturu).

---

## Texturový atlas — velikost a UV mapování

### Houses_Atlas.dds (obytné budovy)
Velikost: **512 × 12 288 px**

Atlas je rozdělen svisle na dvě oblasti:

**Střechy (V 0,75 až 1,0 — horní část atlasu):**
- 6 vzorů střech, každý 512 × 512 px
- Vzor se vybírá deterministicky podle semínka budovy (Random Seed)

**Fasády (V 0,0 až 0,75 — dolní část atlasu):**
- 12 stylů fasád, každý 512 × 768 px
- Každý styl má 3 sekce vertikálně: přízemí (ground), patra (upper), štít (gable) — každá sekce 256 px
- Styl fasády se vybírá deterministicky podle semínka budovy

**UV mapování stěn:**
- Každé 3 m délky stěny = 1/3 U šířky v atlasu
- U offset 1/3 — začátek mapování je posunut aby se méně opakovaly dveře (které jsou na levém okraji atlasu)
- V rozsah závisí na počtu podlaží budovy — přízemí vždy na spodku, štít nahoře

### Highrise_Atlas.dds (bytové domy a komerční budovy)
Velikost: **2048 × 12 288 px**

Atlas obsahuje 12 regionů (6 bytové domy + 6 komerční budovy):
- Každý region: 4 podlaží, každé 256 px vysoké
- Bytové domy: regiony 0–5 (horní polovina atlasu)
- Komerční budovy: regiony 6–11 (dolní polovina atlasu)

Výběr regionu závisí na kategorii budovy a počtu podlaží. UV mapování počítá přesné souřadnice pro každé podlaží samostatně.

### Industrial_Atlas.dds (průmyslové budovy)
Velikost: **512 × 9 216 px**

Proč tato velikost: 12 stylů fasád × 768 px na styl = 9 216 px. Žádná sekce střech — proto je atlas o 3 072 px kratší než Houses_Atlas (který má navíc 6 střešních vzorů × 512 px nahoře).

**Klíčový rozdíl oproti Houses_Atlas:** Industrial_Atlas nemá sekci pro střechy. Celý atlas (V 0,0 až 1,0) obsahuje pouze fasády. Houses_Atlas má fasády jen v oblasti V 0,0–0,75 (zbývajících 0,25 tvoří střechy).

Protože kód počítá V souřadnice fasád stejně pro oba atlasy (fasády = V 0,0–0,75), musí se pro industrial všechny V hodnoty přeškálovat koeficientem **1 / 0,75 = 1,333**. Tím se fasády roztáhnou přes celý atlas.

**Struktura fasád:**
- Stejné jako Houses_Atlas: 12 stylů fasád, každý má 3 sekce — přízemí (ground), patro (upper), štít (gable)
- Výběr stylu fasády je deterministický podle semínka budovy

**Definice UV sekcí (kolik metrů = 1 sekce):**
- 1 sekce atlasu = 256 px = **3 m** výšky stěny v reálném světě
- Každý styl má 3 sekce nad sebou: ground (přízemí, dole), upper (patro, uprostřed), gable (štít, nahoře) — celkem 768 px = 9 m
- Přiřazení sekce závisí na indexu podlaží: podlaží 0 → ground, podlaží 1 → upper, podlaží 2+ → gable

**Podlaží a sekce u industrial:**
- Průmyslové budovy mají vždy **1 podlaží = 3 m**
- 1 podlaží → sekce **ground** (přízemí s okny/vraty)
- Sekce upper a gable se nepoužijí
- Fyzická výška stěny v geometrii je **6 m** (kód nastavuje `height=6.0` pro průmyslové budovy — vyšší strop), ale UV mapuje jen 1 sekci (3 m) → textura se natáhne 2× vertikálně přes 6 m stěny

**Horizontální UV (U osa):**
- Každé 3 m délky stěny = 1/3 šířky U v atlasu
- U offset 1/3 — začátek mapování je posunut doprava v atlasu (přeskočí část s dveřmi)

---

## Tlačítko Export Condor OBJ+MTL

Exportuje vygenerované budovy ze scény do souborů OBJ+MTL připravených přímo pro Condor. Funguje pro single patch i range mód.

**Postup pro každý patch:**

1. Najde kolekci `Condor_{krajina}_{patch_id}` (LOD0) a/nebo `Condor_{krajina}_{patch_id}_LOD1` podle nastavení LOD Level. Pokud kolekce neexistuje, zaznamená chybu a přeskočí.

2. **Speciální krok v range módu se zaškrtnutým terrain:** Patche v range módu jsou v Blenderu posunuty od sebe o 5 760 m (každý patch je na jiném místě ve scéně). Condor ale potřebuje souřadnice v OBJ souboru vždy relativně k originu (0,0,0). Plugin proto:
   - Uloží aktuální polohu všech mesh objektů kolekce
   - Přesune je na (0, 0, 0)
   - Aplikuje transformace (`Apply transforms`)
   - Provede export
   - Po exportu vrátí objekty zpět na původní pozice v Blenderu

   V single patch módu nebo bez terrain checkboxu se tento krok přeskočí — objekty jsou již na správném místě.

3. Projde všechny mesh objekty v kolekci a sestaví skupiny objektů podle jejich jmen (bez suffixu `.001` apod.).

4. Pro každou skupinu dohledá texturu z TEXTURE_MAP. Pokud textura není v mapě, pokusí se ji načíst z materiálu objektu v Blenderu.

5. Zapíše OBJ soubor:
   - Triangulovaná síť
   - Osy: forward=X, up=Z (Condor formát)
   - Normály plochy
   - UV souřadnice
   - Odkaz na MTL soubor

6. Zapíše MTL soubor se správnými Condor hodnotami materiálů pro každou skupinu.

7. Pokud je zapnuto Generate Powerlines a existuje textura pylónů, zkopíruje `Pylons.dds` do `Working/Autogen/Textures/`.

**Výstupní soubory:**
- `Working/Autogen/o{patch_id}.obj` + `o{patch_id}.mtl` (LOD0)
- `Working/Autogen/o{patch_id}_LOD1.obj` + `o{patch_id}_LOD1.mtl` (LOD1)

Na konci zobrazí počet exportovaných souborů, počet patchů a dobu trvání.

---

## Statistiky (Last Import)

Po dokončení Generate Buildings se zobrazí box s výsledky:
- **Buildings** — celkový počet vygenerovaných budov
- **Patches** — počet zpracovaných patchů (zobrazí se jen pokud bylo více než 1)
- **Time** — celková doba zpracování v milisekundách

---

## Log generování (generate_log.txt)

Při každém generování se v `Working/Autogen` tvoří **`generate_log.txt`** (anglicky). Log se jen **doplňuje, nikdy nepřepisuje** — běhy jdou pod sebe. Na začátku každého běhu je souhrn, pod ním bloky jednotlivých patchů (zvlášť pro LOD0 a LOD1):

```
============================================================
Total patches: 12  (LOD0: 12, LOD1: 12)
Total time: 640.3 s  (10 min 40 s)
============================================================
031018
Generation time: 8.0 s
Objects:
  Highrise_walls
  flat_roof_1
  ...
  houses
  industrial_walls
  pylones (aerialway)
  chimney
Airport: Letališče Lesce
------------------------------------------------------------
031018_LOD1
...
```

- **Total patches / Total time** — počet patchů (LOD0/LOD1) a součet časů.
- **Objekty** se vypisují jako **finální** (gabled/hipped střechy se slučují do `houses`, takže se uvádí jen `houses`).
- **`pylones (aerialway)`** — když má patch lanovky.
- **Airport** — řádek je jen když střed letiště padne do daného patche (z `airport/airports.json`).

---

## Subpanel: Roof Options

### Roof Selection
Určuje jak plugin rozhoduje o typu střechy pro každou budovu:

- **Geometry-based (Recommended)** — kombinuje tvar půdorysu (plocha, poměr stran, pravoúhlost) a kategorii budovy z OSM. Vhodné pro realistické výsledky bez explicitních tagů.
- **OSM Tags Only** — sedlovou střechu dostanou pouze budovy s tagem `building=house`, `building=detached` apod. Ostatní budovy dostanou rovnou střechu.

### Random Hipped Roofs
Přibližně 50 % budov způsobilých pro sedlovou střechu dostane náhodně valbovou střechu. Vhodné pro testování vizuální variability. Výsledek závisí na hodnotě Random Seed.

### Merge Flat Roofs
Sloučí všechny ploché střechy jednoho patche do jednoho objektu `flat_roof` místo rozdělení do 6 skupin podle atlasu textur (Roof1.dds až Roof6.dds). Nutné pro použití funkce Terrain photo.

### Terrain photo on flat roofs
Dostupné pouze pokud je Merge Flat Roofs zapnuto. Ploché střechy dostanou texturu ortofota z `Landscapes/{krajina}/Textures/t{patch_id}.dds`. UV souřadnice jsou normalizovány na rozměry patche — z výšky střechy splývají se zemí.

### Only industrial
Dostupné pouze pokud je Terrain photo zapnuto. Ortofoto dostanou pouze průmyslové budovy (sloučeny do `flat_roof`). Ostatní budovy s plochou střechou si ponechají normální textury Roof1–6.dds a jdou do objektů `flat_roof_1` až `flat_roof_6`.

### Gable Height
Výška hřebene sedlové nebo valbové střechy nad korunní hranou zdí, v metrech. Platí pro všechny generované šikmé střechy. Výchozí hodnota: 3 m.

### Roof Overhang
Přesah střechy za hranu zdí pro LOD0 (detailní úroveň), v metrech. LOD1 přesah nemá. Výchozí hodnota: 0,5 m.

### Max Floors (Gabled)
Maximální počet podlaží budovy, aby mohla dostat sedlovou nebo valbovou střechu. Vyšší budovy automaticky dostanou rovnou střechu. Výchozí hodnota: 2.

---

## Subpanel: Advanced

### House-Scale Constraints
Podmínky, které musí splnit půdorys budovy pro přidělení sedlové nebo valbové střechy (kromě počtu podlaží):

- **Max House Area** — maximální plocha půdorysu v m². Výchozí: 360 m².
- **Max Side Length** — maximální délka nejdelší strany v m. Výchozí: 30 m.
- **Min Side Length** — minimální délka nejkratší strany v m. Výchozí: 3,2 m.
- **Max Aspect Ratio** — maximální poměr délky k šířce. Výchozí: 4,8.

Budova musí splnit všechny podmínky zároveň. Pokud nesplní byť jednu, dostane rovnou střechu.

### Geometry Constraints

- **Min Rectangularity** — minimální míra "obdélníkovosti" půdorysu (poměr skutečné plochy k ploše ohraničujícího obdélníku). Rozsah 0–1. Budovy s nepravidelným tvarem (L, U, T) mají nízkou hodnotu a dostanou rovnou střechu. Výchozí: 0,70.
- **Max Vertices (Polyskel)** — maximální počet vrcholů půdorysu pro výpočet valbové střechy pomocí polyskeletu. Složité půdorysy s více vrcholy dostanou sedlovou nebo rovnou střechu. Výchozí: 12.

### Terrain Integration

- **Floor Z Offset** — o kolik metrů se budovy zanoří pod povrch terénu, aby nevznikaly mezery v místech kde terén není zcela rovný. Výchozí: 0,3 m.

### Reproducibility

- **Random Seed** — celé číslo pro inicializaci generátoru náhody. Stejné číslo = stejné výsledky (stejné budovy dostanou valbové střechy, stejné budovy dostanou stejné textury). Výchozí: 42.

---

## Other objects (rozbalovací sekce pod Powerlines)

Není to samostatný podpanel dole, ale **rozbalovací box přímo pod oknem Powerlines** (sbalit/rozbalit přes šipku). Při importu více než 2 kolekcí se navíc Outliner automaticky sbalí, aby nebyl nepřehledný. Sekce je kontejner pro doplňkové objekty — zatím komíny, může přibývat.

### Volitelný zdroj komínů: chimney.osm
Pokud je ve složce `Working/Autogen` soubor `chimney.osm` (nebo `Chimney.osm`), plugin z něj před generováním komínů **vytáhne komíny patřící danému patchi** a doplní je do `map_{patch_id}.osm` (čte se streamovaně, duplicitní ID se přeskočí). Komíny se pak vygenerují stejně jako z hlavního OSM. Když soubor ve složce není, nic se nemění a vše běží jako dosud.

### Chimney — Batch (zaškrtávátko vedle nápisu „Chimney")
Slouží pouze pro file mode (Import to Blender vypnuto). Když je zaškrtnuto, plugin po vygenerování OBJ navíc vygeneruje komíny (stejně jako tlačítko Import), sloučí je do jednoho objektu `chimney` s originem v (0, 0, 0) a přidá ho do téhož OBJ. Pokud se zároveň zapisuje MTL, komín dostane v MTL materiál `condor_chimney` a texturu `Chimney.dds`. Výchozí stav: vypnuto.

### Chimneys — Import
Importuje komíny pro patch zadaný v Patch ID. Pracuje v obou módech (Single Patch i Range).

**Postup:**

1. Načte OSM soubor `map_{patch_id}.osm` ze složky `Working/Autogen/`. Pokud soubor neexistuje, patch se přeskočí.
2. Načte metadata z `h{patch_id}.txt` (souřadnicová projekce). Pokud chybí, patch se přeskočí.
3. Zkontroluje dostupnost terénu — hledá `Working/Heightmaps/modified/h{patch_id}.obj`, jinak `Working/Heightmaps/h{patch_id}.obj`. Pokud terén neexistuje, patch se přeskočí.
4. Smaže všechny existující objekty `Chimney_{patch_id}_*` ze scény i paměti, aby se předešlo duplikátům při opakovaném importu.
5. Z OSM dat načte komíny:
   - **Uzly (node)** s tagem `man_made=chimney` — přečte souřadnice a výšku z tagu `height` (výchozí 30 m)
   - **Polygony (way)** s tagem `man_made=chimney` — vypočítá těžiště polygonu, přečte výšku
   - **Kontrola duplikátů:** pokud leží bod (node) uvnitř polygonu (way), polygon se přeskočí — existuje jen jeden komín na daném místě. Detekce používá algoritmus winding number.
6. Pro každý komín importuje 3D model:
   - Výška ≥ 31 m → `chimney_big.obj` (velký průmyslový komín)
   - Výška < 31 m → `chimney_small.obj` (menší komín)
   - Oba modely jsou součástí pluginu ve složce `assets/3Dobjects/`
7. Umístí komín na správné souřadnice X, Y. Výška Z (patka komínu):
   - Pokud je ve scéně terénní objekt `TR3{patch_id}` → vrhl paprsek shora dolů na terén a zjistí přesnou výšku v daném bodě (ray cast)
   - Pokud terén není ve scéně → načte terénní síť ze souboru a interpoluje výšku z trojúhelníků
8. Každý komín dostane jméno `Chimney_{patch_id}_{číslo:03d}` a uloží se do něj `patch_id` jako vlastní atribut.
9. Komíny se zařadí do podkolekcí:
   - Velké → `chimney_big_{patch_id}` (podkolekce `Condor_{krajina}_{patch_id}`)
   - Malé → `chimney_small_{patch_id}` (podkolekce `Condor_{krajina}_{patch_id}`)
10. V Range módu: pokud bylo importováno více patchů, komíny se posunou o správný offset (5760 m × pozice patche) stejně jako terén a budovy.
11. Zkopíruje texturu `Chimney.dds` z `assets/3Dobjects/` do `Working/Autogen/Textures/` (jen pokud tam ještě není).

**Origin každého komína po importu:** Origin leží na X, Y souřadnicích komínu a na výšce Z terénu v daném bodě (patka komínu je přesně na terénu). Geometrie modelu sahá od Z=0 (relativně k originu) nahoru.

---

### Chimneys — Merge
Sloučí všechny komíny aktuálního patche do jednoho objektu připraveného pro Condor export.

**Postup krok za krokem:**

1. Najde všechny objekty začínající `Chimney_` které jsou viditelné ve view layer. Osiřelé objekty v paměti (neviditelné) se ignorují.
2. Rozdělí je podle uloženého `patch_id` atributu — každý patch se slučuje zvlášť.
3. Pro každý patch: vybere všechny komíny a sloučí je do jednoho objektu (`Join`).
4. Výsledný objekt pojmenuje `chimney`.
5. Aplikuje transformace (`Apply transforms: location, rotation, scale`):
   - **Před merge:** každý komín má vlastní origin přesně na své pozici — X a Y jsou světové souřadnice komínu (metry), Z je výška terénu v daném místě (patka komínu leží přesně na terénu). Geometrie komínu stojí od Z=0 originu nahoru. **Proto lze před mergem každý komín samostatně přesouvat nebo upravovat** — origin je tam kde komín fyzicky stojí.
   - **Po merge a apply v Single Patch módu:** origin přejde na (0, 0, 0) světových souřadnic. Absolutní souřadnice všech komínů jsou zapečeny do geometrie sítě. Od tohoto bodu nelze komíny upravovat jednotlivě.
   - **Po merge a apply v Range módu (s terrain):** origin se nastaví na polohu terénního objektu `TR3{patch_id}`. Geometrie se posune tak, aby komíny zůstaly na správném relativním místě vůči terénnímu objektu. Díky tomu jsou komíny správně vůči svému terénu i pokud je patch posunut od origin scény.
6. Smaže všechny materiály a přiřadí materiál `condor_chimney`.
7. Přesune objekt do kolekce `Condor_{krajina}_{patch_id}`.
8. Smaže prázdné podkolekce `chimney_big_{patch_id}` a `chimney_small_{patch_id}`.
9. Odstraní duplicitní materiály `condor_chimney.001`, `.002` atd.

---

---

## Mapování objektů na textury (TEXTURE_MAP)

Plugin interně udržuje tabulku, která přiřazuje každému typu vygenerovaného objektu jeho texturový soubor `.dds`. Tato tabulka se používá při přiřazování materiálů v Blenderu i při exportu MTL souboru pro Condor.

| Název objektu / skupiny | Textura | Které budovy sem patří |
|---|---|---|
| `houses` | `Houses_Atlas.dds` | Obytné budovy se šikmou střechou — stěny i střecha v jednom objektu |
| `Highrise_walls` | `Highrise_Atlas.dds` | Bytové domy (apartments), komerční budovy, neznámé budovy s půdorysem do 200 m² |
| `industrial_walls` | `Industrial_Atlas.dds` | Průmyslové budovy (viz seznam OSM tagů níže) + neznámé budovy s půdorysem nad 200 m² |
| `flat_roof_1` | `Roof1.dds` | Ploché střechy — skupina 1 (náhodné přiřazení) |
| `flat_roof_2` | `Roof2.dds` | Ploché střechy — skupina 2 |
| `flat_roof_3` | `Roof3.dds` | Ploché střechy — skupina 3 |
| `flat_roof_4` | `Roof4.dds` | Ploché střechy — skupina 4 |
| `flat_roof_5` | `Roof5.dds` | Ploché střechy — skupina 5 |
| `flat_roof_6` | `Roof6.dds` | Ploché střechy — skupina 6 |
| `flat_roof` (sloučený) | `Roof1.dds` výchozí, nebo `t{patch_id}.dds` při Terrain photo | Všechny ploché střechy sloučeny do jednoho objektu |
| `pylones` | `Pylons.dds` | Pylóny a kabely elektrického vedení **+ lanovky** (sloučené do stejného objektu, stejná textura) |
| `wind_turbine` | `WindTurbine.dds` | Větrné turbíny |
| `chimney` | `Chimney.dds` | Komíny |

### Které OSM tagy patří do kategorie INDUSTRIAL

Z OSM tagu `building=*` se za průmyslové budovy považují tyto hodnoty:
`industrial`, `warehouse`, `factory`, `hangar`, `manufacture`, `storage_tank`, `silo`, `barn`, `greenhouse`, `farm_auxiliary`, `digester`

Navíc: budovy s tagem `building=yes` nebo jiným neznámým tagem (kategorie OTHER) **a půdorysem větším než 200 m²** se také zařadí do `industrial_walls` — protože velké neoznačené budovy jsou pravděpodobně průmyslové nebo zemědělské haly.

Průmyslové budovy vždy dostanou **plochou střechu** bez ohledu na tvar půdorysu. Výchozí výška je 6 m (1 podlaží s vysokými stropy).

**Jak se mapování použije při importu OBJ do Blenderu:**
Po importu OBJ souboru plugin projde všechny nové objekty. Pokud je název objektu v této tabulce (např. objekt se jmenuje `industrial_walls`), materiál se přejmenuje na `condor_industrial_walls`. Tím se materiály sjednotí — více patchů sdílí stejný materiál místo vzniku duplikátů (`industrial_walls.001`, `industrial_walls.002` atd.).

---

## Subpanel: Debug

### Debug OSM ID
Pole pro zadání OSM ID jedné konkrétní budovy (číselné ID z OpenStreetMap). Pokud je vyplněno, Generate Buildings zpracuje pouze tuto jednu budovu a ignoruje všechny ostatní. Vhodné pro ladění problematických půdorysů nebo testování nových typů střech.
