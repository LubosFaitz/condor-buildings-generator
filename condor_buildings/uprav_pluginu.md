# Úpravy pluginu Condor Buildings

## Vysílače (transmitters) — samostatný, odstranitelný modul

Přidána funkce **Transmitter** do sekce „Other objects" — Import / Merge / Batch,
úplně stejně jako Chimney, jen hledá vysílače místo komínů.

**Detekce v OSM:** `man_made=mast` nebo `man_made=tower`, k tomu
`tower:type=communication`. Výška z tagu `height`.

**Modely** (`assets/3Dobjects`): `height ≤ 100 m` → `transmitter_small.obj`
(materiál `condor_transmitter_small`), `height > 100 m` → `transmitter_big.obj`
(`condor_transmitter_big`). Oba se **škálují v ose Z** podle `height` (jen když je
height uvedena), pata se posadí na terén (modely nemají origin u paty, dopočítává
se posun). Merge sloučí všechny **big** do `transmitter_big` a všechny **small** do
`transmitter_small` (dva objekty — různé textury, nejde je sloučit dohromady).

**Celá logika je v JEDNOM novém souboru `blender/transmitters.py`** (detekce,
build pro file-mode Batch, operátory Import/Merge, kreslení řádku v panelu,
registrace). Do stávajících skriptů jsou přidané jen **3 krátké háčky v try/except,
označené `--- TRANSMITTER add-on (removable) ---`:**
1. `__init__.py` — `transmitters.register()` / `unregister()`.
2. `blender/panels.py` — uvnitř boxu „Other objects" za Chimney zavolá
   `transmitters.draw_panel(box, context)`.
3. `blender/batch_processing.py` — ve file-mode exportu přidá transmitter skupiny.

Checkbox „Batch" používá vlastní scene property `condor_transmitter_batch`
(registruje ji modul), TEXTURE_MAP položky přidává modul za běhu — proto se
**nemění `properties.py` ani `config.py`**.

**Jak vrátit zpět:** smazat soubor `blender/transmitters.py` (háčky jsou
guardované, takže vše spadne do původního chování), nebo zakomentovat ty 3
označené bloky. Nic jiného na vysílačích nezávisí.

## Oprava duplicitních komínů při Import Chimneys

### Co dělá kontrola node vs. ways
V OSM datech může být jeden komín zapsán dvakrát — jednou jako bod (node) a jednou jako polygon půdorysu (ways). Plugin kontroluje: pokud bod leží uvnitř polygonu, polygon se přeskočí a použije se jen bod. Tím vznikne vždy jen jeden komín na daném místě.

### Oprava 1 — výpočet "uvnitř polygonu" (winding number)
Původní výpočet (ray-casting) špatně vyhodnotil bod blízko hrany půdorysu jako "venku". Polygon se pak nepřeskočil a vznikl duplicitní komín. Nahrazeno algoritmem winding number, který správně detekuje bod uvnitř i blízko hrany.

### Oprava 2 — mazání starých komínů před importem
Při mazání komínů v Blenderu objekty zůstávaly v paměti (osiřelé) s obsazenými jmény. Každý nový import pak dostával přípony .001, .002 atd. Nyní se na začátku každého importu patche automaticky smažou všechny existující Chimney_{patch_id}_* objekty z Blenderovy paměti. Tím je vždy čistý začátek.

### Oprava 3 — slučování komínů (Merge Chimneys)
Při slučování komínů operátor hledal všechny Chimney_* objekty v Blenderově paměti, včetně osiřelých z jiných patchů (např. Chimney_035023_001). Ty nejsou ve view layer a nešly vybrat — vznikala chyba. Nyní se berou jen objekty které jsou skutečně ve view layer (viditelné ve scéně).

### Oprava 4 — viewport po Generate Buildings a Import Patch
Generate Buildings necentroval pohled na střed terénu (chyběl view_location). Import Patch navíc nepřepínal na Layout workspace a měl špatnou hodnotu clip_end (10000 místo 100000). Obě místa nyní nastavují: Layout workspace, clip_end=100000, view_distance=9051.04, rotation=top-down.

### Oprava 5 — centrování viewportu na terén (View Lock)
Viewport se nezobrazoval na středu terénu protože objekty mají origin v (0,0,0) ale pohled okna scény nebyl na tomto středu uzamčen. Přidáno `space.lock_object = TR3{patch_id}` — tím Blender vždy centruje pohled na terénní objekt daného patche. Funguje automaticky bez pevných souřadnic.

Soubor: blender/operators.py

---

## Import Patch v range módu (výběr více patchů)

### Co dělá nová funkce
Tlačítko "Import Patch" v sekci Patch Selection (kde se zadává rozsah X/Y) bylo dříve vždy šedé — fungovalo jen v Single Patch módu. Nyní funguje i pro rozsah patchů.

Po kliknutí na Import Patch v range módu plugin:

1. **Prochází všechny patche** v zadaném rozsahu X/Y (stejně jako Generate Buildings).
2. **Importuje terén** (`h{patch}.obj`, nebo `modified/h{patch}.obj` pokud existuje) — ale jen pokud je zaškrtnuto "terrain" a terén ještě není v kolekci `Patch_Terrain`. Nastaví texturu a UV mapu stejně jako Generate Buildings.
3. **Importuje OBJ soubor** (`o{patch}.obj`) z Working/Autogen do kolekce `Condor_{landscape}_{patch}`. Opraví názvy materiálů na `condor_*` varianty.
4. **Přesune každý patch** na správné místo: první patch zůstane na místě, každý další se posune o 5 760 m (velikost jednoho patche v Condoru) — jak v ose X, tak Y. Přesune se jak kolekce budov, tak terén.
5. **Nastaví viewport** — Material shading, pohled shora, uzamčen na terén prvního patche.

Pokud OBJ soubor pro některý patch neexistuje, vypíše varování a pokračuje s dalšími.

Single Patch mód a Generate Buildings zůstávají beze změny.

Soubor: blender/operators.py

---

## Import Patch — automatické rozpoznání LOD0 a LOD1

### Co dělá
Import Patch nyní automaticky hledá oba soubory v Working/Autogen:
- Pokud existuje `o010028.obj` → naimportuje do kolekce `Condor_{landscape}_010028`
- Pokud existuje `o010028_LOD1.obj` → naimportuje do kolekce `Condor_{landscape}_010028_LOD1`
- Pokud existují oba → naimportuje oba
- Pokud neexistuje ani jeden → napíše chybu

Funguje pro single patch i range mód.

Soubor: blender/operators.py

---

## Nový checkbox "Only industrial" pro flat střechy

### Co dělá
Pod checkboxem "Terrain photo on flat roofs" přibyl nový checkbox **Only industrial** (defaultně odškrtnutý). Jde zaškrtnout pouze když je zapnuté "Terrain photo".

Když je zaškrtnutý:
- Flat střechy na **industrial budovách** se sloučí do jednoho objektu `flat_roof` a dostanou texturu z terrain photo (t<patch>.dds)
- Flat střechy na **ostatních budovách** jdou do `flat_roof_1..6` s normálními texturami (Roof1..6.dds) — stejně jako bez merge

Když je odškrtnutý — chování stejné jako dřív.

### Soubory změněny
- `blender/properties.py` — nová vlastnost `flat_roof_industrial_only`
- `blender/panels.py` — checkbox v UI
- `config.py` — nový parametr v PipelineConfig
- `processing/mesh_grouper.py` — logika separace industrial flat střech
- `main.py` — předání parametru do MeshGrouper
- `blender/operators.py` — předání parametru do PipelineConfig

---

## Checkbox tr3f vedle pole Patch ID

### Co dělá
V sekci Patch Selection (Single Patch mód) je pole pro zadání čísla patche (Patch ID) zkráceno a vedle něj přibyl zaškrtávací checkbox **tr3f**.

Když je **tr3f zaškrtnuto**: terén se importuje z `Working/Heightmaps/22.5m/h{patch_id}.obj`. Pokud soubor v této složce neexistuje, zobrazí se anglická chyba a operace se zastaví.

Když je **tr3f odškrtnuto**: chování stejné jako dřív — bere se `Working/Heightmaps/modified/h{patch_id}.obj`, nebo pokud neexistuje, `Working/Heightmaps/h{patch_id}.obj`.

Platí pro Generate Buildings i Import Patch v single patch módu.

### Soubory změněny
- `blender/properties.py` — nová vlastnost `patch_tref`
- `blender/panels.py` — Patch ID a checkbox tr3f jsou na jednom řádku
- `blender/operators.py` — logika výběru zdrojové složky pro terén

---

## Oprava Export OBJ+MTL pro LOD1

### Problém
Když se vygenerovaly budovy v LOD1 módu, export do OBJ+MTL nefungoval — nic se nezapsalo.

### Příčina
Generate Buildings pro LOD1-only mód vytvářel kolekci bez přípony `Condor_{landscape}_{patch}`. Export přitom vždy hledal `Condor_{landscape}_{patch}_LOD1` — ta neexistovala, takže se nic neexportovalo.

### Oprava
Generate Buildings nyní vytváří kolekci `Condor_{landscape}_{patch}_LOD1` i pro LOD1-only mód. Export ji najde správně a zapíše soubor `o{patch}_LOD1.obj` + `.mtl`.

Soubor: blender/operators.py

---

## Oprava Export Terrain pro tr3f mód

### Co dělá
Tlačítko Export Terrain nyní reaguje na checkbox tr3f:

Když je **tr3f zaškrtnuto**: hledá objekt `TR3f{patch_id}` a exportuje ho do `Working/Heightmaps/22.5m/modified/h{patch_id}.obj`.

Když je **tr3f odškrtnuto**: chování stejné jako dřív — hledá `TR3{patch_id}`, exportuje do `Working/Heightmaps/modified/h{patch_id}.obj`.

Soubor: blender/operators.py

---

## Oprava viewport setupu po Import Patch (single patch mód)

### Problém
Po kliknutí na Import Patch se pohled ve scéně nenastavoval správně — chybělo Material shading, správný clip end, uzamčení pohledu na terén atd. Platilo pro oba případy: normální (tr3f odškrtnuto) i tr3f (zaškrtnuto).

### Příčina
Operátor vracil výsledek dříve, než se dostalo na nastavení viewportu. Pokud v autogenu nebyly žádné OBJ soubory budov, vrátil se hned s varováním a viewport setup přeskočil. Stejný problém byl i při tr3f, kdy soubor budov chybí záměrně.

### Oprava
Nastavení viewportu (Material shading, lens 50, clip start/end, pohled shora, uzamčení na terénní objekt) bylo přidáno i před každý předčasný návrat — před varování v normálním módu i v tr3f módu. Takže i když nejsou budovy, terén se zobrazí správně se stejným pohledem jako při úspěšném importu.

Soubor: blender/operators.py

---

## Oprava os při importu budov v Import Patch

### Co se změnilo
Tlačítko Import Patch importovalo OBJ soubory budov se špatnými osami `forward_axis='X', up_axis='Z'`. Změněno na `forward_axis='Y', up_axis='Z'`, aby se budovy naimportovaly se správnou orientací.

Platí pro oba módy Import Patch:
- single patch mód
- range mód

Změna se týká pouze importu budov v Import Patch. Import terénu, import komínů ani export (Export Condor OBJ+MTL = Forward X / Up Z) se nemění.

Soubor: blender/operators.py

---

## Úklid terénu při neúspěšném Generate Buildings (oprava zamrznutí okna)

### Problém
Když Generate Buildings nezpracoval ani jeden patch (např. oba checkboxy Save to Autogen i Import to Blender odškrtnuté), zobrazila se chybová hláška „No patches were processed successfully". Před tím už ale operátor stihl naimportovat terén do scény. Okno pak nešlo zavřít — Blender při kliknutí zamrzl.

### Příčina
Operátor naimportoval velký terén a pak skončil chybou. Zůstal ve scéně osiřelý terén a Blender u toho zamrzal.

### Oprava
Operátor si teď během běhu zapamatuje, který terén v tomto běhu naimportoval (seznam jmen). Když na konci zjistí, že neuspěl ani jeden patch, **před zobrazením chybového okna** ten terén odstraní ze scény i z Outlineru (a pokud zůstane kolekce `Patch_Terrain` prázdná, smaže i ji). Scéna se tím vrátí do původního stavu, takže okno už jde normálně zavřít a Blender nezamrzne.

Odstraní se pouze terén naimportovaný v tomto neúspěšném běhu — terén z dřívějška zůstává. Chybová hláška se zobrazuje dál.

Soubor: blender/operators.py

---

## Větrné turbíny správně usazené i bez Import to Blender (file mód)

### Problém
Když byl Import to Blender odškrtnutý (zápis OBJ na disk) a v patchi byly větrné turbíny, všechny se zapsaly do OBJ naskládané ve středu patche (0,0,0) místo na svá místa. Důvod: turbíny si polohu ukládaly stranou (origin) a tu při zápisu do OBJ nikdo nepoužil — na rozdíl od pylonů, které mají polohu zapečenou přímo v geometrii.

### Oprava
Když je Import to Blender odškrtnutý (zápis OBJ souboru), pipeline teď turbíny zpracuje jako pylony:
- Vezme polohu každé turbíny (X, Y z OSM, výška Z z terénu) a **zapeče ji přímo do geometrie**.
- Všechny turbíny v patchi **otočí o stejný náhodný úhel** (úhel je daný seedem, takže výsledek je při stejném seedu vždy stejný).
- Sloučí je do **jednoho objektu** `wind_turbine` (origin 0,0,0, souřadnice v geometrii) — stejně jako pylony.

Tím se turbíny v OBJ usadí na správná místa.

Když je Import to Blender **zapnutý**, chování zůstává beze změny — každá turbína je samostatný objekt s originem na patě, takže ji lze jednotlivě otočit sliderem a pak ručně sloučit tlačítkem Merge wind_turbine.

Soubory: main.py, generators/powerlines.py

---

## Budovy se po importu patche neotáčejí o 90°

### Problém
Export budov do OBJ+MTL (funkce `export_condor_obj_mtl`) má zapnutý `axis_swap`, který souřadnice otáčí o −90° kolem osy Z (přepočet `(x,y,z) → (y,−x,z)`) — tak to Condor potřebuje a export se nemění. Import patche ale budovy načítal s `forward_axis='Y'`, takže to otočení nevracel zpět a budovy se v Blenderu objevily **pootočené o 90°** oproti terénu.

### Oprava
Import **budov** (souborů `o<patch>.obj`) teď čte s `forward_axis='X'` (up zůstává `Z`) místo `forward_axis='Y'`. Tím se otočení z exportu při importu vyruší a budovy sednou přesně na terén.

- Změna se týká jen importu budov — **terén** (`h<patch>.obj`) i **komíny** se dál importují s `forward_axis='Y'` beze změny.
- Do meshe se nic nezapisuje, mění se jen osa importu.

Soubory: blender/operators.py

---

## Import Patch sám pozná osu budov (X nebo Y)

### Problém
Soubor `o<patch>.obj` může vzniknout dvěma způsoby, z nichž každý zapisuje **jinou osu**:
- **Export to OBJ+MTL** (funkce `export_condor_obj_mtl`) → otáčí souřadnice na **Forward X / Up Z**.
- **Generate Buildings s odškrtnutým „Import to Blender"** (funkce `export_mesh_groups`) → zapisuje souřadnice **syrově ve Forward Y / Up Z**, bez otáčení.

Import Patch ale načítal budovy **napevno s Forward X**. Na soubory z „Export to OBJ+MTL" to sedělo, ale soubory z Generate (Y) se naimportovaly **otočené o 90°**.

### Oprava
Import Patch teď před načtením přečte hlavičku OBJ a osu si zvolí sám:
- Soubory z `export_condor_obj_mtl` mají v hlavičce řádek `# Axis swap: True` → import použije **Forward X**.
- Soubory z `export_mesh_groups` ten řádek nemají → import použije **Forward Y**.

Tím Import Patch správně načte **oba** typy souborů bez otáčení a bez ručního přepínání os. Žádný export se nemění, jen import.

- Platí pro single patch i range mód.
- Přidán pomocný `_detect_obj_forward_axis()`, který přečte prvních pár řádků hlavičky souboru.

Soubory: blender/operators.py

---

## POZNÁMKA: tato změna byla VRÁCENA ZPĚT

Slučování sedlových střech do `houses` u LOD0 exportu (`_merge_gabled_for_export`)
bylo na přání vráceno do PŮVODNÍHO stavu — slučování zase funguje jako dřív.
Důvod: bez slučování zůstaly střechy jako samostatné objekty bez materiálu
(`gabled_roofs_lod0` / `hipped_roofs` nejsou v `TEXTURE_MAP`). Záznam níže je proto
už neaktuální a ponechán jen pro historii.

## LOD0 export (Import to Blender vypnuté) srovnán na stav jako ve složce nova

### Co se změnilo
Export budov do OBJ v režimu „Import to Blender vypnuté" (funkce `export_mesh_groups`) dostával u **LOD0** skupiny prožené funkcí `_merge_gabled_for_export(...)`, která **slučovala sedlové střechy (`gabled_roofs_lod0`) do objektu `houses`**. Ve složce nova se tohle slučování nedělá — sedlové střechy jdou do OBJ jako samostatný objekt.

Na žádost srovnáno na stav jako v nova: LOD0 export teď posílá `lod0_groups` přímo, **bez** slučování. Sedlové střechy tak zůstávají samostatné.

- Týká se **jen LOD0** v režimu „file" (Import to Blender vypnuté). LOD1 byl beze změny už předtím.
- Funkce `_merge_gabled_for_export` v souboru zůstává (jen se u LOD0 exportu nevolá), takže vrácení je snadné.

### Jak to vrátit zpět (původní stav)
V `main.py`, v bloku exportu LOD0, vrátit řádek:

```python
            export_mesh_groups(
                lod0_groups,                         # <-- současný (jako nova)
                result_lod0_path,
                comment=f"LOD0 - Patch {config.patch_id}"
            )
```

zpět na původní:

```python
            export_mesh_groups(
                _merge_gabled_for_export(lod0_groups),   # <-- původní (sloučí gabled do houses)
                result_lod0_path,
                comment=f"LOD0 - Patch {config.patch_id}"
            )
```

Soubor: main.py

---

## Export při vypnutém „Import to Blender" srovnán na X, Z (jako Export to OBJ+MTL)

### Problém
Soubor `o<patch>.obj` se zapisoval **dvěma různými cestami s různou osou**:
- **Export to OBJ+MTL** (`export_condor_obj_mtl`) → správně **Forward X / Up Z** (otáčí `(x,y,z) → (y,−x,z)`).
- **Generate s vypnutým „Import to Blender"** (`export_mesh_groups`) → zapisovalo souřadnice **syrově ve Forward Y**, bez otáčení.

Podle README má být výstup pro Condor vždy **axis-corrected Forward X / Up Z**. Cesta s vypnutým importem to nedělala, takže její soubory byly oproti tomu otočené.

### Oprava
`export_mesh_groups` teď zapisuje vrcholy se stejným otočením jako Condor export — `(x,y,z) → (y,−x,z)`, tedy **Forward X / Up Z**. Do hlavičky souboru navíc přidává řádek `# Axis swap: True`, takže Import Patch ho podle hlavičky pozná a načte správně (Forward X).

Tím obě cesty (tlačítko i vypnutý import) dávají **stejný výstup X, Z** a Import Patch je načte správně, bez otáčení.

- Staré soubory zapsané ještě ve Forward Y (bez té značky v hlavičce) Import Patch dál pozná a načte s Forward Y — funguje tedy na staré i nové soubory.

Soubor: io/obj_exporter.py

---

## Rozdělení velkých objektů při exportu (checkbox "Separate")

### Co to dělá
Condor (formát c3d) zvládá maximálně ~65 535 vrcholů na jeden objekt. Když měl
objekt (např. `houses`) třeba 185 000 vrcholů, byl pro Condor příliš velký.

Nový volitelný checkbox **Separate** (nad tlačítkem Generate Buildings, defaultně
**vypnutý**) zapne rozdělení: po exportu se každý objekt s více než **60 000
vrcholy** rozdělí na několik menších — `houses`, `houses2`, `houses3`, … — kde
každý díl má pod 60 000 vrcholů. Dělí se po celých plochách (žádný trojúhelník se
nerozpůlí). Každý díl dostane v `.mtl` vlastní materiál se **stejnými hodnotami a
stejnou texturou** (`map_Kd`) jako původní objekt.

- **Vypnutý checkbox (default):** export funguje úplně stejně jako dřív.
- **Zapnutý checkbox:** po normálním exportu se navíc spustí dělení.

### Jak je to udělané
Veškerá logika dělení je v **samostatném novém souboru** `obj_split.py`, který
zpracuje už hotové vyexportované soubory `.obj` + `.mtl` (přečte je a přepíše).
Stávající exportní funkce `export_condor_obj_mtl` zůstala **beze změny** — k ní
se nesahalo. Do pluginu se přidalo jen napojení checkboxu.

### Měněné / vytvořené soubory
- **NOVÝ:** `obj_split.py` — samostatný post-processor (funkce
  `split_large_objects(obj_path, max_verts=60000)`).
- `blender/properties.py` — přidána vlastnost `separate_large_objects`
  (BoolProperty, default False).
- `blender/panels.py` — přidán řádek `layout.prop(props, "separate_large_objects")`
  nad tlačítko Generate Buildings.
- `blender/operators.py` — v operátoru exportu (`CONDOR_OT_export_condor`) se po
  úspěšném `export_condor_obj_mtl(...)` zavolá `split_large_objects(out_obj)`,
  ale jen když je checkbox zapnutý.

### Jak to vrátit zpět (revert)
1. Smazat soubor `obj_split.py`.
2. `blender/properties.py` — smazat celý blok `separate_large_objects: BoolProperty(...)`
   (vložený před `wind_turbine_rotation:`).
3. `blender/panels.py` — smazat dva řádky
   `# Separate large objects on export (off by default)` a
   `layout.prop(props, "separate_large_objects")` (vložené za `layout.separator()`
   v sekci Import Button, před `row = layout.row(align=True)`).
4. `blender/operators.py` — smazat blok
   `# Optional post-process: split objects over 60000 verts ...` až po
   `errors.append(f"Patch {patch_id} {lod_name}: separate failed: {e}")`
   (vložený mezi `files_written.append(out_obj)` a `except Exception as e:`).

---

## Rozdělení: správný výpočet vrcholů a limit 25 000

### Co se opravilo
První verze rozdělení počítala vrcholy špatně — podle počtu „pozic" (řádků `v`).
Jenže Condor počítá vrcholy **po triangulaci a s normálami**, takže jich vidí
zhruba **3× víc** (každý trojúhelník má 3 vlastní vrcholy). Kvůli tomu zůstávaly
objekty po rozdělení moc velké a převod na c3d hlásil „can't create c3d from obj".

Teď skript počítá vrcholy **stejně jako Condor** (to číslo, co ukazuje sloupec
*Vertex* v Object Editoru) a dělí podle něj. Také:
- **Limit je 25 000** vrcholů na objekt (Condorův strop je 32 767 — *signed
  16-bit*; 25 000 nechává rezervu).
- **Rozdělí se i objekty jako `pylones`**, které předtím propadly.
- **Jména jsou vždy unikátní** — i kdyby v Blenderu bylo víc objektů „houses",
  nikdy nevzniknou dva stejné materiály (dřív vznikalo `houses2` dvakrát a MTL
  se rozbilo).

Soubor: `obj_split.py` (jen tento samostatný skript, do pluginu se nesáhlo).

---

## Rozdělení i při vypnutém „Import to Blender" (file mód)

### Co to dělá
Když se budovy generují s **vypnutým Import to Blender** (dávkový režim), plugin
zapisuje OBJ přímo na disk funkcí `export_mesh_groups` — ten soubor **nemá MTL,
nemá normály a není triangulovaný**. Landscape Editor (LE) ho neumí převést na
c3d. Naopak soubor z tlačítka **Export Condor OBJ+MTL** (triangulovaný, s
normálami a s MTL) LE převést **umí** — ověřeno.

Nově: když je **Separate zapnutý** a **Import to Blender vypnutý**, po zápisu se
ten file-mode OBJ **přepíše do úplně stejného formátu jako z tlačítka** (MTL +
triangulace + normály) — použitím **téže funkce, kterou používá tlačítko**
(`export_condor_obj_mtl`) — a pak se na něj pustí stejné **dělení na 25 000**
jako u tlačítka. Výsledek = identický s tlačítkem + rozdělení, a LE ho převede.

Textury do MTL se berou z `config.build_texture_map` (jméno objektu → textura),
takže není potřeba mít budovy v Blenderu.

- **Separate vypnutý:** vše jako dřív, beze změny (starý formát z `export_mesh_groups`).
- **Separate zapnutý:** OBJ se přepíše na Condor-ready (MTL) a rozdělí.

Veškerá logika je v **novém samostatném skriptu** `obj_split_filemode.py`
(jen přečte OBJ a zavolá ověřené funkce `export_condor_obj_mtl` + `split_large_objects`).

### Měněné / vytvořené soubory
- **NOVÝ:** `obj_split_filemode.py` — přepis na Condor-ready + dělení pro file mód
  (`split_large_objects_filemode(obj_path, texture_map, max_verts=25000)`).
- `blender/operators.py` — v generujícím operátoru se po `run_pipeline` (jen
  když je Separate zapnutý a Import to Blender vypnutý) postaví texture_map a
  zavolá `split_large_objects_filemode` na vyexportované `o<patch>.obj` (LOD0 i LOD1).

### Jak to vrátit zpět (revert)
1. Smazat soubor `obj_split_filemode.py`.
2. `blender/operators.py` — smazat blok
   `# File mode (Import to Blender off): optionally split ...` až po
   `errors.append(f"Patch {patch_id}: separate (file mode) failed: {e}")`
   (vložený za `patches_processed += 1`, před `# Import to Blender if requested`).

---

## Dávkové zpracování (checkbox "Batch processing")

### Co to dělá
Nový checkbox **Batch processing** vedle **Separate** (defaultně vypnutý).
Funguje **jen společně s Import to Blender**. Když jsou **oba zapnuté**, Generate
Buildings projede celý rozsah patchů a **každý patch zpracuje samostatně** —
takže v Blenderu je vždy jen jeden patch (nízká paměť).

Pro každý patch v rozsahu:
1. vygeneruje a naimportuje budovy **bez terénu** (terén se v dávce neimportuje),
2. **pootočí všechny větrné turbíny** patche o **jeden náhodný úhel** kolem osy Z,
3. udělá **merge** turbín (`condor.merge_wind_turbines`),
4. vyexportuje **Condor-ready OBJ+MTL** (stejně jako tlačítko; když je **Separate**
   zapnutý, velké objekty se rozdělí na 25 000),
5. **smaže** kolekci patche z Blenderu,
6. pokračuje dalším patchem.

- **Batch processing vypnutý:** vše jako dřív, beze změny.
- **Batch processing zapnutý + Import to Blender vypnutý:** nic zvláštního (jen file mód).

Veškerá dávková logika je v **novém samostatném skriptu** `batch_processing.py`.

### Měněné / vytvořené soubory
- **NOVÝ:** `batch_processing.py` — celý dávkový řetězec pro jeden patch
  (`process_patch(context, props, patch_id, paths)`).
- `blender/properties.py` — nová vlastnost `batch_processing` (default vyp.).
- `blender/panels.py` — checkbox **Batch processing** vedle **Separate**
  (společný řádek `row_opts`).
- `blender/operators.py` — v Generate Buildings:
  - tři podmínky importu terénu dostaly `and not props.batch_processing`
    (single-patch terén, per-patch terén, pozicování patchů) → v dávce se terén
    neimportuje,
  - na konci smyčky přes patche (za importem každého patche) se při
    `batch_processing AND import_to_blender` zavolá `process_patch`.

### Jak to vrátit zpět (revert)
1. Smazat soubor `batch_processing.py`.
2. `blender/properties.py` — smazat blok `batch_processing: BoolProperty(...)`.
3. `blender/panels.py` — vrátit `row_opts` zpět na jediný
   `layout.prop(props, "separate_large_objects")`.
4. `blender/operators.py` — smazat blok `# Batch processing: ...` (volání
   `process_patch`) a ze tří podmínek importu terénu odstranit
   `and not props.batch_processing`.

---

## Oprava: vrácené oboustranné sedlové střechy (LOD0)

### Co bylo špatně
V jiném chatu se zjednodušila funkce `_add_pitched_building` v
`processing/mesh_grouper.py` tak, že **střecha šla rovnou do `houses`**:
```python
self.houses.merge(result.walls)
self.houses.merge(result.roof)
```
Tím zůstaly skupiny `gabled_roofs` a `hipped_roofs` prázdné, nevyrobily se
skupiny `gabled_roofs_lod0` / `hipped_roofs`, a import do Blenderu tak neměl co
zdvojit. Výsledek: **sedlové střechy na LOD0 byly jednostranné** (chyběl
duplikát s obrácenými normálami) a valbové nedostaly přepočet normál.

### Oprava
Funkce vrácena na **původní verzi** (podle složky `puvodni`) — střecha jde do
správné skupiny:
```python
self.houses.merge(result.walls)
if self.is_lod0 and result.actual_roof_type == RoofType.GABLED:
    self.gabled_roofs.merge(result.roof)      # sedlové LOD0 -> zdvojí se při importu
elif result.actual_roof_type == RoofType.HIPPED:
    self.hipped_roofs.merge(result.roof)      # valbové -> přepočet normál
else:
    self.houses.merge(result.roof)            # sedlové LOD1 -> rovnou do houses
```
Import pak u sedlových LOD0 udělá `_duplicate_and_flip_mesh` (duplikát s
obrácenými normálami) a **sloučí do `houses`** — v Blenderu tedy nevzniká
samostatný objekt, střechy jsou zase oboustranné.

Soubor: `processing/mesh_grouper.py` (jen funkce `_add_pitched_building`).

---

## File-mode: valbové střechy sloučené do houses (kvůli textuře)

### Co a proč
Ve file-módu (Import to Blender vypnutý) se sedlové střechy slévaly do `houses`,
ale **valbové (`hipped_roofs`) zůstávaly jako samostatný objekt**. Protože
file-mode nemá MTL a Landscape Editor přiřazuje texturu podle jména objektu,
`hipped_roofs` zůstal **bez materiálu a textury**.

(Pozn.: tohle bylo i v původní verzi — `_merge_gabled_for_export` slévala jen
sedlové. Není to regrese, je to **nové vylepšení** na přání uživatele.)

### Oprava
Funkce `_merge_gabled_for_export` v `main.py` nově slévá do `houses` **i
`hipped_roofs`** (nejen `gabled_roofs_lod0`). Pak ve file-módu žádný samostatný
`hipped_roofs` objekt není a valbové střechy dostanou **texturu houses
(Houses_Atlas)**. Týká se to **jen file-módu** (funkce se volá jen tam); import
do Blenderu je beze změny.

Soubory: `main.py` — funkce `_merge_gabled_for_export` (přidáno slití
`hipped_roofs`) a navíc **LOD1 export ji teď taky volá** (`export_mesh_groups(
_merge_gabled_for_export(lod1_groups), ...)`). Dřív ji volal jen LOD0, takže v
LOD1 souboru zůstával `hipped_roofs` jako samostatný objekt bez textury.

### Jak to vrátit zpět (revert)
1. Vrátit `_merge_gabled_for_export` tak, aby slévala jen `gabled_roofs_lod0`
   (jako v `puvodni/main.py`) — odebrat větve pro `hipped`.
2. V LOD1 exportu vrátit `export_mesh_groups(lod1_groups, ...)` (bez
   `_merge_gabled_for_export`).

---

## Limit dělení (Separate) změněn na 32000

Konstanta `DEFAULT_MAX_VERTS` změněna z 25000 na **32000** v obou skriptech:
`obj_split.py` (tlačítko Export) a `obj_split_filemode.py` (file-mód). Je to
těsně pod Condorovým stropem 32767 (rezerva 767). Dávka (`batch_processing.py`)
používá `obj_split.py`, takže jede taky na 32000.

---

## Checkboxy Separate a Batch processing dočasně skryté

V `blender/panels.py` jsou checkboxy **Separate** a **Batch processing**
**zakomentované** (nad tlačítkem Generate Buildings) — nejsou vidět a jsou
neaktivní. Vlastnosti (`separate_large_objects`, `batch_processing`) zůstávají
v kódu, ale defaultně vypnuté, takže se split ani dávka nespustí.

Zpětné zapnutí: v `panels.py` odkomentovat tři řádky:
```python
row_opts = layout.row(align=True)
row_opts.prop(props, "separate_large_objects")
row_opts.prop(props, "batch_processing")
```

Soubor: `blender/panels.py`.

---

## „add MTL" místo „Separate" + dělení zrušeno, Batch maže kolekce

Větší úprava na přání uživatele:

### Dělení (Separate) zrušeno
Dělení velkých objektů se už nepoužívá. Odebrána **volání** `split_large_objects`:
- v `blender/operators.py` u tlačítka Export (Condor OBJ+MTL),
- v `blender/operators.py` ve file-mode hooku,
- v `batch_processing.py` v dávkovém exportu.

Soubory `obj_split.py` a `obj_split_filemode.py` **zůstaly na disku** (nepoužívané,
záloha) — nejsou odnikud volané.

### Checkbox „Separate" → „add MTL"
Vlastnost `separate_large_objects` přejmenována na **`add_mtl`** (label „add MTL").
Funkce: když je **Import to Blender vypnutý** a **„add MTL" zaškrtnuté**, ke
každému vygenerovanému `o<patch>.obj` se dopíše **MTL** se stejnými materiály a
texturami jako u tlačítka Export OBJ+MTL (textury z `build_texture_map`). Bez
dělení. Když je odškrtnuté → jen OBJ + JSON jako dřív.

Logika je v **`batch_processing.py`** ve funkci `add_mtl_to_filemode_obj(obj_path,
texture_map)` — přečte file-mode OBJ a přepíše ho na Condor-ready OBJ+MTL přes
`export_condor_obj_mtl` (triangulace, normály, MTL; `axis_swap=False`, protože
souřadnice jsou už prohozené). File-mode hook v operátoru ji volá pro LOD0 i LOD1.

### Batch checkbox znovu viditelný + maže kolekce
- Checkboxy **„add MTL"** a **„Batch processing"** jsou v panelu zase vidět
  (společný řádek `row_opts`).
- Dávka při mazání patche (`batch_processing._delete_patch`) teď odstraní **i
  prázdné kolekce** (`bpy.data.collections.remove` po odlinkování), aby
  nezůstávaly v outlineru — dřív `cleanup_buildings_collection` mazal jen objekty.

### Měněné soubory
- `blender/properties.py` — `separate_large_objects` → `add_mtl`
- `blender/panels.py` — checkboxy „add MTL" + „Batch processing" (odkomentováno)
- `blender/operators.py` — odebrán split (tlačítko), file-mode hook → `add_mtl`
- `batch_processing.py` — `add_mtl_to_filemode_obj`, mazání kolekcí, odebrán split

### Pozdější úprava: Batch checkbox skrytý
Checkbox **Batch processing** v `panels.py` znovu **zakomentován** (řádek
`# row_opts.prop(props, "batch_processing")`) — není vidět. „add MTL" zůstává
viditelné. Vlastnost `batch_processing` zůstává v kódu (vypnutá), dávka se
nespustí. Zpětně zobrazit = odkomentovat ten řádek.

---

## Komíny do file-mode OBJ (checkbox „Batch" u Chimney)

### Co to dělá
Nový checkbox **„Batch"** vedle nápisu **Chimney** (sekce Other objects, default
vyp.). Když generuji s **Import to Blender vypnutým** (file-mód) a tenhle „Batch"
je zaškrtnutý, po vygenerování OBJ se navíc do něj **přidají komíny**:
- najdou se komíny v OSM (`man_made=chimney`, node i way),
- na každé místo se posadí model `chimney_big.obj` / `chimney_small.obj` (podle
  výšky) na terén,
- sloučí se do jednoho objektu **`chimney`** (origin 0,0,0), s prohozenými osami
  (Condor y,-x,z), aby seděl na budovy,
- objekt `chimney` se přidá **do `o<patch>.obj`** (LOD0 i LOD1).

Když je zapnuté i **„add mtl batch"**, komín dostane v MTL **materiál + texturu
Chimney.dds** (textura se zkopíruje do `Autogen/Textures`). Když je „add mtl
batch" vypnuté, komín se jen přidá do prostého OBJ (bez MTL).

Celé se to dělá **bez importu do Blenderu** — komíny se počítají přímo jako
MeshData (parsování OSM + terénu + assetů), takže to nezdržuje a neplní outliner.

### Měněné / vytvořené soubory
- `blender/properties.py` — nová vlastnost `chimney_batch` („Batch")
- `blender/panels.py` — checkbox „Batch" vedle nápisu Chimney (+ doplněno `props`)
- `blender/operators.py` — file-mode hook: staví komín a přidá ho do OBJ/MTL
- `batch_processing.py` — `build_chimney_meshdata` (staví komín jako MeshData),
  `append_chimney_plain` (přidá komín do prostého OBJ), `add_mtl_to_filemode_obj`
  rozšířeno o parametr `chimney_md`, pomocný `_parse_obj_as_meshdata`

### Pozn. k souřadnicím
Komíny se umisťují v surových souřadnicích projekce a pak se na ně aplikuje
**stejný přepoh os jako u budov** (`_condor_xform`, y,-x,z). To je nejcitlivější
část — ověřit v Condoru, že komíny stojí na správném místě na budovách.

### Oprava: otáčení patche o 90° (hlavička Axis swap)
`add mtl batch` přepisuje OBJ přes `export_condor_obj_mtl(axis_swap=False)`,
protože souřadnice už jsou prohozené (z file-mode exportu). Tím ale do hlavičky
zapsalo `# Axis swap: False`, kdežto souřadnice JSOU prohozené → addonový Import
Patch to přečetl jako neprohozené a **otočil celý patch o 90°** (uživatel musel
dělat R+Z 90). Opraveno: po zápisu se hlavička přepíše na **`Axis swap: True`**
(funkce `_set_axis_swap_header_true` v `batch_processing.py`).

---

## File-mode: oboustranné sedlové + čisté valbové střechy (LOD0)

### Co bylo špatně
Při importu do Blenderu dělá `mesh_converter`: sedlové (gabled) LOD0 **zdvojí
a otočí normály** (`_duplicate_and_flip_mesh`) a valbové (hipped) přes
`validate()` + `recalc_normals` srovná na jednostranné se správnými normálami.
**File-mód tohle nedělal** — jen zapsal surová data → sedlové LOD0 byly
jednostranné a valbové měly „divné hrany a otočené normály" (jsou v datech
oboustranné).

### Oprava
V `main.py` ve `_merge_gabled_for_export` (běží jen ve file-módu) se teď před
sloučením do `houses`:
- **`gabled_roofs_lod0`** zduplikuje s obrácenou orientací (`_duplicate_faces_reversed`
  z `roof_gabled.py`) → oboustranné, jako při importu.
- **`hipped_roofs`** pročistí: nová pomocná funkce `_dedupe_reversed_faces` odebere
  obrácené duplikáty a **nechá první (původní, CCW nahoru) plochy** → jednostranné
  se správnými normálami. (Čistší a deterministické než Blender validate+recalc.)

Výsledek ve file-módu je tak pro LOD0 stejný jako při importu do Blenderu.

Soubor: `main.py` (`_merge_gabled_for_export` + `_dedupe_reversed_faces`).

---

## File-mode export přepsán: střechy přes Blender (spolehlivě)

### Proč
Předchozí pokus opravit střechy „v datech" (`_merge_gabled_for_export`) nefungoval
spolehlivě (gabled se nezdvojily, hipped měly špatné normály). Zdvojení sedlových
a srovnání normál valbových dělá **Blenderův import** (`mesh_converter`) a čistě
v datech se to těžko replikuje.

### Jak to teď je
File-mód (Import to Blender vypnutý) teď exportuje přes **stejný kód jako tlačítko
Export OBJ+MTL**:
1. pipeline běží v **memory** režimu (vrátí skupiny, nezapisuje),
2. skupiny se **na chvíli naimportují do dočasné kolekce v Blenderu** přes
   `import_grouped_meshes_to_blender` → tam se sedlové zdvojí a valbové srovnají
   normály (přesně jako vždycky),
3. objekty se přečtou zpět (`blender_obj_to_meshdata`) a zapíšou do `o<patch>.obj`
   (přes `export_condor_obj_mtl` když je **add mtl batch**, jinak `export_mesh_groups`),
4. zapíše se i `o<patch>_report.json`,
5. dočasná kolekce se **smaže** (v Blenderu nic nezůstane).

Komín (když je **Batch** u Chimney) se přidá do skupin před exportem; staví se
nově v **základních souřadnicích** (osy prohodí až exportér, společně s budovami).

Výsledek = **1:1 jako tlačítko Export** — pro LOD0 i LOD1, s MTL i bez.

### Měněné soubory
- `blender/operators.py` — `output_mode = "memory"` vždy; file-mode větev volá
  `export_filemode_via_blender` (nahradila starý „add MTL / chimney" hook).
- `batch_processing.py` — nová funkce `export_filemode_via_blender` (+ pomocná
  `_remove_collection`); `build_chimney_meshdata` vrací surové (neprohozené) souřadnice.

(Staré `add_mtl_to_filemode_obj`, `append_chimney_plain` a v `main.py`
`_merge_gabled_for_export` zdvojování zůstaly v kódu, ale už se ve file-módu
nevolají — nahradil je tento spolehlivější postup.)

### Oprava: file-mód vytváří vždy OBA LODy
`export_filemode_via_blender` původně respektoval rozbalovátko Output LOD, takže
při „LOD0" se nevytvořil `o<patch>_LOD1.obj`. Starý file-mód ale vždy psal oba.
Opraveno: file-mód teď vždy zapíše **LOD0 i LOD1** (oba s opravenými střechami),
bez ohledu na Output LOD.

### Oprava: pád při slučování valbových (`None` v seznamu objektů)
Při importu (i z file-módu) padalo `_join_objects_into` na
`'NoneType' object has no attribute 'select_set'` — hned po sloučení sedlových
zůstal v `bpy.context.view_layer.objects` prázdný (`None`) záznam a slučování
valbových na něm spadlo. Opraveno: `_join_objects_into` v `mesh_converter.py`
nejdřív udělá snapshot seznamu (`list(...)`) a **`None` přeskočí**; hlídá i
prázdný `target`. Týká se importu i file-módu.

### Oprava: větrné turbíny ve file-módu (po přechodu na memory režim)
Přechodem file-módu na „memory" se přestaly turbíny zapékat (zůstaly jako
samostatné nepootočené objekty). Opraveno **jen v `batch_processing.py`**: nová
funkce `_merge_turbines_filemode` v `export_filemode_via_blender` po importu
turbíny **pootočí všechny jedním náhodným úhlem kolem originu každé (v patě),
sloučí do jednoho `wind_turbine`, udělá apply all transform a origin 0,0,0**.
Pak se přečtou aktuální objekty z kolekce (včetně sloučené turbíny) a zapíšou do
OBJ. Nic jiného se neměnilo.

Úhel se losuje **jednou na patch** (`turbine_angle` před smyčkou LOD) a použije
se pro **LOD0 i LOD1**, aby se turbíny v obou LODech otočily stejně.
`_rotate_turbines` dostala nepovinný parametr `angle`.

---

## Checkbox „add mtl batch" schovaný, ale vždy zapnutý
Na přání: checkbox **„add mtl batch"** v `panels.py` zakomentován (není vidět).
Aby se MTL i tak tvořil pořád:
- `add_mtl` má v `properties.py` **default `True`**,
- a `export_filemode_via_blender` v `batch_processing.py` **zapisuje MTL vždy**
  (natvrdo `export_condor_obj_mtl`, bez ohledu na uloženou hodnotu) — aby to
  fungovalo i ve starém `.blend`, kde mohla být hodnota uložená jako vypnutá.

Zpětné zapnutí toggle: odkomentovat checkbox v `panels.py` a v
`export_filemode_via_blender` vrátit `if props.add_mtl:` (jinak `export_mesh_groups`).

---

## Přesun: batch_processing.py do složky blender/
`batch_processing.py` přesunut z kořene pluginu do **`blender/`** (používá `bpy`,
patří k Blenderové části). Upravené importy:
- moduly mimo blender (`config`, `io.*`, `models.*`, `generators.*`,
  `projection.*`) → `from ..…`,
- moduly v blender (`mesh_converter`, `operators`) → `from .…`,
- v `blender/operators.py` import změněn na `from .batch_processing import …`,
- cesta k assetům komínů opravena o úroveň výš
  (`os.path.dirname(os.path.dirname(__file__))`).

---

## Komíny: žádný „válec" pod komínem + správná výška a model

### Problém
Některé komíny jsou v OSM označené **zároveň jako komín i jako budova**
(`man_made=chimney` + `building=yes`, typicky `height=60`). Plugin proto z toho
polygonu dělal **vysoký kulatý dům s plochou střechou** („válec") a na něj pak
postavil model komínu. Navíc se výška komínu z OSM používala jen na výběr
„velký/malý" model — a vkládal se **napevno 100 m** (velký) nebo **30 m** (malý),
takže komín s `height=60` byl ve scéně 100 m vysoký.

### Řešení 1 — válec se vůbec nevytvoří
V `io/osm_parser.py` se polygon s tagem **`man_made=chimney` přeskočí** (u ways i
u relací), takže se z něj nedělá budova. Reálné budovy ani kulatá sila/nádrže bez
komínu to neovlivní (ty tag komínu nemají). Válec ani jeho plochá střecha tím
nikdy nevzniknou — nic se neřeže.

### Řešení 2 — výběr modelu a skutečná výška
Import komínů (a Batch verze) teď čte z OSM i tag **`material`** a jestli je
**`height` uvedená**. Pravidlo:

- **`material=brick`** → model **chimney_small** (cihlový vzhled), bez ohledu na výšku
- jinak a **výška > 30 m** → model **chimney_big** (červenobílý)
- jinak (jiný/žádný materiál a výška ≤ 30 m) → **chimney_small**

A výška:
- když je **`height` uvedená** → model se **naškáluje na tu výšku** (mění se jen
  výška, šířka zůstává, pata zůstává na terénu),
- když **`height` chybí** → nechá se **nativní výška** modelu (malý 30 m / velký 100 m).

Oba modely sdílí stejnou texturu `Chimney.dds`; „cihla vs. pruhovaný" je dané UV
mapováním uvnitř každého modelu, takže výběrem modelu se mění jen vzhled.

### Měněné soubory
- `io/osm_parser.py` — přeskočení `man_made=chimney` ve filtru budov (řešení 1).
- `blender/operators.py` — import komínů: čte `material` + jestli je `height`,
  nový výběr modelu (`is_big`), škálování `ch_obj.scale.z`, zařazení do kolekce
  podle modelu (místo starého prahu `height >= 31`).
- `blender/batch_processing.py` — `build_chimney_meshdata` stejné pravidlo
  (škáluje přímo Z souřadnice vrcholů před posazením na terén), aby file-mode /
  Batch dával stejný výsledek jako import.

---

## File mode: tvoří se i `o<patch>.log`

### Problém
Při **Generate Buildings** s vypnutým **Import to Blender** vznikaly jen
`o<patch>.obj`+`.mtl` (LOD0 i LOD1) a `o<patch>_report.json`, ale **žádný `.log`**.
Readme přitom „detailed processing log" slibuje. Log uměl zapsat jen příkazový
runner (`main.py`, CLI); Blenderový addon `setup_logging` s log souborem nikdy
nevolal — takže log z addonu nevznikal (ani dřív).

### Řešení
Ve file-módu (Import to Blender vyp.) se teď do `Working/Autogen` zapisuje
**`o<patch>.log`** se stejným podrobným obsahem a formátem jako z CLI
(úroveň DEBUG, řádky `čas [úroveň] jméno_modulu: zpráva`). Logovací zapisovač se
**připojí ještě před** během pipeline (takže zachytí celý průběh — parsování,
střechy, export) a po dokončení patche se zase **odpojí**.

Je to bezpečné a izolované: jen se přidá/odebere jeden logovací handler, vše je
v `try/except`, takže **i kdyby se log nepodařilo otevřít, generování běží dál**.
Týká se **jen** file-módu; při importu do Blenderu se log nepíše (jako dosud).

### Měněné soubory
- `blender/operators.py` — nové pomocné funkce `_attach_patch_log` /
  `_detach_patch_log` a jejich napojení ve smyčce přes patche (připojení před
  `run_pipeline`, odpojení ve všech cestách: chyba pipeline, neúspěch, i po
  `export_filemode_via_blender`). Handler kopíruje formát z `main.setup_logging`.

---

## Letecké výstražné koule na el. vedení

### Co to dělá
Nový checkbox **„Warning balls"** (pod „Generate Powerlines") osadí na **nejvyšší
lano** vedení letecké výstražné koule (model `assets/pylons/WarningSphere.obj`,
červeno-bílá šachovnice z atlasu `Pylons.dds`). Koule se **slučují do stejného
objektu `pylones`** jako vedení (žádná extra textura). Když jsou nahoře dvě lana
ve stejné výšce, koule jdou jen na jedno.

### Pravidla osazení
- **Blízko letiště (do 5 km od `aeroway=aerodrome`):** koule **60 cm, rozestup 30 m**.
  Přednostně na **medium** vedení (`Pylon_Medium`); pokud u letiště žádné medium
  není, použijí se vedení, která tam jsou (small, případně large).
- **Přemostění hlubokého údolí / široké řeky:** když lano vede **výš než 45 m nad
  terénem** → koule **130 cm, rozestup 40 m**, na jakémkoli vedení.
- (Velikost 80 cm / 35 m je k dispozici jako střední volba, bez auto-podmínky.)

### Důležité: stahování letišť z OSM
Stahovací Overpass dotaz dřív letiště **vůbec nestahoval**, takže detekce neměla
data. Přidáno `node["aeroway"="aerodrome"]` + `way["aeroway"="aerodrome"]` do
dotazu. **Pozor:** plugin existující `map_<patch>.osm` bere z cache a znovu
nestahuje — aby se letiště objevilo, je nutné **starý `map_<patch>.osm` smazat**
(plugin ho pak stáhne znovu už s letišti).

### Měněné soubory
- `blender/osm_downloader.py` — do Overpass dotazu přidány `aeroway=aerodrome`
  (node + way).
- `io/powerline_parser.py` — parser čte středy letišť (`aeroway=aerodrome`,
  node i těžiště way) → `PowerlineParseResult.aerodromes`.
- `generators/powerlines.py` — výběr nejvyššího lana (`_top_attach`), osazení
  koulí (`_place_ball`, `_place_balls_on_cable`), podmínky letiště/údolí
  (`_ball_mode_for_span`); `generate_powerline_meshes` má nové parametry
  `draw_balls`, `aerodromes`; konstanty velikostí/rozestupů a prahů.
- `config.py` — `PipelineConfig.generate_warning_balls`.
- `main.py` — `_generate_powerline_group(..., draw_balls=...)`, předání letišť a
  flagu; CLI `--warning-balls`.
- `blender/properties.py`, `panels.py`, `operators.py` — checkbox „Warning balls"
  a jeho napojení.

### Úpravy po prvním testu
- **Checkbox skrytý, koule vždy zapnuté:** `warning_balls` má default **ON** a
  v panelu je zakomentovaný (jako „add mtl"). Koule se tedy přidají vždy, když
  jsou zapnuté Powerlines.
- **Poloměr letiště 5 → 3 km** (`AIRPORT_RADIUS = 3000`).
- **Koule visela pod drátem:** drát se kreslí jako **lomená čára** (rovné úseky mezi
  vzorkovacími body), ale koule byly na **hladké parabole**, která se mezi body
  propadá pod ten úsek → koule visely pod drátem. Opraveno: koule se počítají na
  **stejné lomené čáře jako drát** (`cable_z` v `_place_balls_on_cable`). Model
  koule je vycentrovaný správně (ověřeno Z[-0,06; 0,06]); přidané vycentrování
  podle bboxu je jen pojistka.

### Přepis podle Annexu 14 (letecká norma)
Na základě normy přepsána pravidla osazení:
- **Letiště podle DRÁHY, ne středu letiště:** stahuje se `aeroway=runway`, parser
  z ní udělá **zónu = kruh kolem středu dráhy, poloměr = (půlka délky dráhy) + 458 m**
  (`PowerlineParseResult.airport_zones`). Délka se bere přednostně z tagu
  **`length`** (přesnější — namapované konce way bývají kratší), jinak z geometrie.
  Fallback na střed letiště (poloměr 750+458 m), když runway není v OSM.
- **Zrušeno medium/fallback i poloměr 3 km** — koule dostanou všechna vedení
  uvnitř zóny.
- **Velikosti a rozestup:**
  - **letiště, vedení < 69 kV** → 60 cm, **40 m**
  - **letiště, vedení ≥ 69 kV** → 60 cm, **30 m** (hustší na velkých vedeních)
  - **údolí (jakékoli vedení)** → 120 cm, **60 m**
- Napětí se bere ze skutečného tagu `voltage` (`_parse_voltage_kv`); když chybí,
  bere se jako ≥ 69 kV jen `Pylon_Large`. Práh údolí zůstává 45 m.

### Letiště přes hranici patche → sdílený `airports.json`
Aby koule fungovaly i když je dráha v **sousedním** patchi (zóna přesahuje hranici):
- při stahování patche se navíc udělá **samostatný Overpass dotaz** jen na
  `aeroway=aerodrome`+`runway` v **bboxu 3×3 patche** (patch + 1 na každou stranu,
  ~17280 m). `map_<patch>.osm` (budovy/vedení) se **nemění**.
- nalezená letiště se sloučí do **jednoho** souboru `Working/Autogen/airport/airports.json`,
  klíč = **název letiště** (dedup — co tam je, se znovu nezapíše). Pro každé:
  `{ "length": <m>, "center": [lat, lon] }` (střed dráhy; length z tagu, jinak z geometrie).
- při generování se `airports.json` přečte, střed se **zprojektuje do daného patche**
  → zóna (length/2 + 458). Když soubor není, fallback na aeroway z patch OSM.
- **Robustnost parseru letišť:** stahují se i **relace** letišť (velká letiště, např.
  Líně); dráha se k letišti přiřadí podle **bboxu** (way i relace); **přeskočí se
  smetí** (disused/abandoned dráhy a kratší než 50 m); na jedno letiště se vezme
  **nejdelší dráha**. **Letiště bez dráhy** (jen `aeroway=aerodrome`) dostane zónu
  z plochy letiště (těžiště + delší strana bboxu; bodové letiště má default).
- Měněno: `osm_downloader.py` (`download_airports_for_patch`, `_parse_airports`,
  `build_aeroway_query`, …), `io/powerline_parser.py` (`read_airport_zones`),
  `main.py` (čte `airports.json`), `blender/operators.py` (volá hledání letišť).
  **Generování vedení netknuté.**
- Barva = červeno-bílá šachovnice (**beze změny** — barvy se neřeší).
- Měněno: `osm_downloader.py` (runway), `powerline_parser.py` (airport_zones),
  `powerlines.py` (`_ball_mode_for_span` + `_line_is_hv`, konstanty, import
  `_parse_voltage_kv`), `main.py` (předání `airport_zones`), `properties.py` (popis).

## LOD0 vs LOD1 – low modely pro turbíny, vedení a komíny

Cílem je, aby **LOD1** (zjednodušená úroveň) používal odlehčené modely a méně
geometrie než detailní **LOD0**. Platí pro obě cesty – file-mode (dva soubory
`o<patch>.obj` a `o<patch>_LOD1.obj`) i import do Blenderu (kolekce bez suffixu =
LOD0, se `_LOD1` = LOD1).

- **Větrné turbíny:** LOD0 dál používá detailní `turbine.obj`, **LOD1** používá
  odlehčený `turbine_low.obj`. Pozice a natočení jsou stejné, liší se jen model.
- **Elektrické vedení:** LOD0 zůstává plný (pylony + dráty + výstražné koule),
  **LOD1 obsahuje jen pylony** – žádné dráty, žádné koule a navíc **bez malých
  pylonů** (`pylon_small.obj`, typ `Pylon_Small`; malé vedení se v LOD1 vynechá celé).
- **Komíny (file-mode „Batch"):** do LOD0 jdou plné `chimney_big/small.obj`, do
  **LOD1** odlehčené `chimney_big_low.obj` / `chimney_small_low.obj`.
- **Komíny (tlačítko „Import Chimneys" v Blenderu):** respektuje volbu „Output
  LOD" – při **LOD0** naimportuje detailní komíny do kolekce bez suffixu, při
  **LOD1** low modely do kolekce `_LOD1`, při **Both** obojí. „Merge Chimneys"
  slučuje LOD0 a LOD1 odděleně (každý do své kolekce). Když je u komínů zapnuté
  **„Batch", tlačítko „Import Chimneys" je nefunkční** (slučování běží automaticky
  při generování).
- Měněno: `generators/powerlines.py` (low šablona turbíny + přepínač `low`),
  `main.py` (`_generate_wind_turbines_group` LOD0/LOD1, `_generate_powerline_group`
  LOD1 jen pylony), `blender/batch_processing.py` (`build_chimney_meshdata` low +
  LOD0/LOD1 komín v exportu), `blender/operators.py` (Import/Merge Chimneys podle
  LOD, „Import Chimneys" vypnuté při Batch).

### Oprava: „Merge wind_turbine" vynechával jednu turbínu v LOD1
Když jsou ve scéně obě kolekce (Both LODs), základní turbína v druhé kolekci se
kvůli kolizi jmen jmenuje `wind_turbine.001`. Tlačítko „Merge wind_turbine" ji
nepoznalo (hledalo jen `wind_turbine` nebo `wind_turbine_…`, ale po „wind_turbine"
je tečka, ne podtržítko), takže ji při slučování vynechalo a v LOD1 zůstaly **dva**
objekty místo jednoho. Nově se turbína pozná podle toho, že má v názvu podřetězec
`wind_turbine` (ať se jmenuje jakkoli — `wind_turbine.001`, `wind_turbine.002`,
`wind_turbine_3`, `wind_turbine_NOVY` …). Slučování běží po kolekcích, takže v každé
kolekci (každý patch i každý LOD) vznikne jeden objekt `wind_turbine`. Funguje i pro
více patchů najednou. Měněno: `blender/operators.py` (`CONDOR_OT_merge_wind_turbines`,
výběr + `poll`).

### „Merge Chimneys" funguje stejně robustně jako turbíny
Stejná logika jako u turbín: za komín se bere **každý objekt, který má v názvu
podřetězec „chimney"** (case-insensitive — `Chimney_045034_001`, `chimney.002`,
`chimney_NOVY` …). Slučuje se **po kolekcích** (přes Condor patch/LOD kolekci, kterou
operátor najde i přes podsložky `chimney_big_…`/`chimney_small_…`), takže v každé
kolekci (každý patch i LOD) vznikne jeden objekt `chimney`. Funguje pro víc patchů i
obě LOD úrovně. Měněno: `blender/operators.py` (`CONDOR_OT_merge_chimneys`,
výběr podle podřetězce + seskupení podle kolekce + `poll`).

### Pořadí v exportu: wind_turbine před pylones
V exportovaném `o<patch>.obj` se objekty řadily abecedně, takže `pylones` (p) byl
před `wind_turbine` (w). Nově se turbíny posunou **těsně před `pylones`** (ostatní
objekty zůstávají abecedně; když v patchi nejsou pylony nebo turbíny, pořadí se
nemění). **Pozn.:** týká se to **pořadí v souboru `.obj`**, ne Blender outlineru —
ten řadí podle abecedy sám (to je view nastavení Blenderu, ne plugin). Měněno:
`io/obj_exporter.py` (nová `_condor_group_order`, použita v `export_mesh_groups`
i `export_condor_obj_mtl`).

### Větrné turbíny: rozdělený model + náhodné protočení vrtulí
Model turbíny je nově rozdělený na **sloup+gondolu** (statická část) a **vrtuli**
(rotor). Assety v `assets/pylons/`: `turbine_tower.obj` / `turbine_tower_low.obj`
a `turbine_blades.obj` / `turbine_blades_low.obj` (vše `usemtl Turbine` →
`turbine.mtl` / `WindTurbine.dds`). Staré `turbine.obj` / `turbine_low.obj` jsou
zazálohované ve složce `assets/pylons/zaloha wind/`.

Při generování se pro každou turbínu:
- postaví **sloup+gondola** natočené o stejný patch-úhel (jako dřív),
- **vrtule** se protočí o **náhodný úhel** kolem osy rotoru (osa Y procházející
  nábojem `WT_BLADE_HUB = (0, 5.84, 84.23)`), takže každá turbína má listy v jiné
  poloze; pak se přidá stejný patch-úhel,
- sloup i vrtule se **sloučí do jednoho** objektu `wind_turbine`.

Náhodný úhel je deterministický ze `seed` a stejný pro **LOD0 i LOD1** (turbína má
v obou úrovních listy ve stejné poloze). Počet vrcholů zůstává (LOD0 ~296, LOD1
~84). Měněno: `generators/powerlines.py` (`PYLON_FILES` 4 nové díly, konstanta
`WT_BLADE_HUB`, funkce `_spin_blades`, přepsaná `generate_wind_turbines_mesh` +
parametr `seed`), `main.py` (`_generate_wind_turbines_group` předává `seed`).

### Zrychlení: LOD0 i LOD1 jedním průchodem (vedení + turbíny)
Dřív se LOD1 počítal druhým plným voláním generátoru (vedení i turbíny se tím
generovaly dvakrát, včetně drahých dotazů na terén `foot_z`). Nově se obě LOD
postaví **v jednom průchodu** — umístění (pozice, `foot_z`, natočení, u turbín i
náhodné protočení vrtule) se spočítá **jednou** a orazítkuje se do obou LOD:
- **Vedení:** velké/střední pylony se vloží rovnou do LOD0 i LOD1, dráty + koule +
  malé pylony jen do LOD0. `generate_powerline_meshes` teď vrací
  `(mesh_lod0, mesh_lod1, stats)`.
- **Turbíny:** placement se spočítá jednou a orazítkuje detailním modelem (LOD0) a
  low modelem (LOD1). `generate_wind_turbines_mesh` teď vrací
  `(lod0_meshes, lod1_meshes, count)`.
Tím odpadly dvojí dotazy na terén → rychlejší generování. Měněno:
`generators/powerlines.py` (obě funkce + runner `_main`), `main.py`
(`_generate_powerline_group`, `_generate_wind_turbines_group` — jedno volání).

### Checkbox „Randomized wind turbine"
Nový checkbox v boxu **Powerlines** (vedle „Generate Powerlines"). Platí **jen ve
file-mode** (Import to Blender vypnuté):
- **vypnuto (výchozí):** všechny sloupy turbín v patchi se natočí o **jeden** náhodný
  úhel (jako dosud),
- **zapnuto:** **každý sloup** se natočí o **vlastní** náhodný úhel.
Úhel je deterministický z `(per-patch seed, index turbíny)`, takže **LOD0 i LOD1**
mají tu samou turbínu natočenou stejně. (Protočení vrtule kolem rotoru je náhodné
per-turbína nezávisle na tomto.) Checkbox je **zašedlý, když je zapnuté „Import to
Blender"** (platí jen pro file-mode). Měněno: `blender/properties.py`
(`randomize_wind_turbines`), `blender/panels.py` (checkbox v boxu Powerlines),
`blender/batch_processing.py` (`_rotate_turbines` + `_turbine_index`,
`_merge_turbines_filemode`, `export_filemode_via_blender` — per-patch seed).

### LOD1 pylony: low model `pylon_large_low.obj`
Pro **LOD1** se u pylonů použije **low varianta**, pokud existuje soubor
`pylon_<typ>_low.obj`. Zatím je hotový jen **`pylon_large_low.obj`**, takže:
- **LOD1 velké pylony** → `pylon_large_low.obj`,
- **LOD1 střední pylony** → zatím plný `pylon_medium.obj` (až přibude
  `pylon_medium_low.obj`, použije se automaticky).
Low varianty se načítají **jen když soubor existuje** (chybějící nic nerozbije).
LOD0 beze změny. Měněno: `generators/powerlines.py` (`OPTIONAL_PYLON_FILES`,
`load_pylon_templates` načte volitelné low šablony, `generate_powerline_meshes`
razítkuje do LOD1 `tmpl1 = <typ>_Low` jinak plný).

### Lanovky (aerialway) — Fáze 1: stožáry + rovné lano
Nová volitelná funkce: z OSM `aerialway=*` way se generují **lanovky** —
stožár na každý uzel `aerialway=pylon` (na terén, natočený podél trasy) a
**rovné lano** (bez prověšení) mezi vršky sousedních stožárů. Vše do **samostatného
objektu `aerialway`** se stejnou texturou jako vedení (`Pylons.dds`). Stejná
geometrie pro LOD0 i LOD1 (zatím nejsou low modely).
- **Kabinkové** (`cable_car`/`gondola`) → stožár `Pylon_AerialCab_ns.obj`, lano v
  **(0, ±5.77, 22.34)** (na rameni, kousek od konce — určeno 3D kurzorem).
- **Sedačkové/vleky** (`chair_lift`/`drag_lift`/`t-bar`/…) → stožár
  `Pylon_Aerialway_ns.obj`, lano na **kolech/kladkách (0, ±2.81, 8.20)**, ne na
  rameni nahoře.
- Modely se **nescalují** (původní výška).
- **Fáze 2 — kabiny/sedačky:** podél lana se rozmístí nosiče visící pod lanem
  (origin modelu = závěs na laně, tělo dolů): **kabinkové → `Telecabine.obj`
  rozestup ~77,5 m**, **sedačkové → `Aerialway_Cab.obj` rozestup 15 m**.
  Rozmisťují se jako **jedna spojitá smyčka**: nahoru po prvním laně a **dolů po
  druhém laně obráceně** (tím se nosiče na zpětném laně automaticky **otočí o 180°**)
  a **zbytek vzdálenosti se přenese** z cesty nahoru do cesty dolů → rozestup je
  plynulý kolem celé smyčky (obě lana nezačínají stejně). Měřeno 3D podél lana,
  start půl rozestupu od stožáru. Funkce `_place_carriers` (přenos zbytku) +
  `AERIALWAY_CARRIER_FILES`/`AERIAL_CARRIER`/`AERIAL_SPACING` v `generators/aerialway.py`.
- **Natočení stožárů:** lanovkové rameno je v lokální ose **Y** (rameno pylonů
  vedení je v X), proto se pro lanovky použije `yaw = _yaw_at − 90°` → rameno je
  **kolmo na trasu, stejně jako u vedení**.
- **Pata na terénu (oprava utopení):** vedení používá `_foot_z`, který schválně
  bere **minimum terénu v okruhu 4 m + zapuštění `TOWER_SINK = 0,3 m`** (aby široká
  příhradová pata neplavala). To ale lanovkový stožár **utápělo pod terén** (~0,8 m
  i víc na svahu). Lanovka teď používá vlastní `_aerial_foot_z` = **přímo povrch
  terénu v uzlu** (`_terrain_z`), bez minima a zapuštění → stožár stojí **na**
  terénu. `_foot_z` (vedení/budovy) zůstává beze změny.
- **Hraniční stožáry → terén sousedního patche:** stožár umístěný kousek **za
  hranicí** patche (kvůli pokračování lana přes okraj) bral dřív jen výšku okraje
  (plaval). Nově se výška bere z **terénu sousedního patche** (`NeighborTerrain`):
  podle toho, kterou hranicí uzel přesahuje, se určí soused (jih = řádek −1, sever
  +1, …), načte se jeho `h<id>.obj` + `h<id>.txt` (cachuje se), souřadnice uzlu se
  převedou do rámce souseda přes **rozdíl `translate`** (`local = utm + translate`
  → posun o `translate_souseda − translate_aktuálního`) a výška se vezme z jeho
  terénu. Tím **hraniční stožár sedí na reálné zemi a navazuje na vedlejší patch**
  (stejná poloha, stejné natočení yaw — to je invariantní vůči posunu — i stejná
  výška, protože se počítá ze stejného terénu). Měněno: `generators/aerialway.py`
  (`NeighborTerrain`, `_aerial_foot_z` + `neighbor`), `main.py`
  (`_generate_aerialway_group` dostane `heightmaps_dir`/`patch_id`/`translate`).
- Zapíná **checkbox „Aerialways"** v boxu Powerlines.
Nové soubory: `io/aerialway_parser.py` (najde aerialway ways + pylony, projekce,
in_patch, přechod hranice), `generators/aerialway.py` (znovupoužije helpery z
`powerlines.py` — `_foot_z`/`_yaw_at`/`_place_pylon`/`_add_cable`/`_rotz`). Měněno:
`config.py` (`TEXTURE_MAP['aerialway']='Pylons.dds'`, `PipelineConfig.generate_aerialway`),
`main.py` (`_generate_aerialway_group` + zařazení do LOD skupin), `blender/properties.py`
+ `panels.py` (checkbox), `blender/operators.py` + `batch_processing.py` (předání
flagu + kopírování/preview textury Pylons.dds), `blender/osm_downloader.py`
(do Overpass dotazu přidáno `way["aerialway"]` — aditivně jako `power`/`aeroway`).
**Pozn.:** kvůli tomu se musí patch **stáhnout znovu** (starý `map_<patch>.osm`
ještě lanovky neobsahuje).

### Lanovky: naklánění kladek sedačkového stožáru podle sklonu lana
Sedačkový stožár je rozdělený na **statický sloup** (`Pylon_Aerialway_ns.obj`,
60 v) a **kladky/rolny** (`Pylon_Aerialway_rollers.obj`, 144 v, pivot na ose v
**Z≈7,97**). Při generování se pro každý stožár spočítá **sklon lana** v daném uzlu
(`_pitch_at` = úhel z rozdílu výšek sousedních uzlů a vodorovné vzdálenosti) a
**kladky se o ten úhel naklopí** kolem osy **Y** procházející pivotem (`_tilt_rollers`,
obdoba `_spin_blades` u turbíny), pak se orazítkují stejně jako sloup → kladky
**kopírují stoupání/klesání lana**. Měněno: `generators/aerialway.py`
(`AERIALWAY_ROLLER_FILES`/`AERIAL_ROLLERS`/`ROLLER_PIVOT`, `_tilt_rollers`,
`_pitch_at`, dvouprůchodové umístění — nejdřív `node_world`, pak sloup + kladky).

### Lanovky: naklánění kladek i u kabinkového stožáru
Stejné chování přidáno i pro **kabinkový stožár**: přidány oddělené kladky
`Pylon_AerialCab_rollers.obj` (208 v, pivot na ose v **Z≈22,34** — výška ramene,
kde leží obě lana). Při generování se naklopí podle sklonu lana úplně stejně jako
u sedačkového. Aby se počítalo se správným pivotem každého typu, je teď pivot
**podle typu stožáru** (`AERIAL_ROLLER_PIVOT`: sedačka Z≈7,97, kabinka Z≈22,34)
místo jediné konstanty `ROLLER_PIVOT`. `_tilt_rollers` teď dostává pivot jako
parametr. Měněno: `generators/aerialway.py`. Oba typy lanovek tak mají kladky,
které kopírují stoupání/klesání lana.

Navíc posunuty **attach body lana** kabinky na **vršek koleček** (Y±7,68,
Z≈22,59) místo na původní konce ramen (Y±5,77, Z≈22,34) — lano teď leží
v drážkách koleček. Kabinky visící z lana se tím posunou taky na kolečka.
Měněno: `AERIAL_ATTACH["Pylon_AerialCab"]`.

### Lanovky spadají pod „Generate power lines“ a slučují se do objektu pylones
Lanovky už nemají vlastní checkbox. Generují se **společně s elektrickým vedením**
(jeden checkbox „Generate power lines“) a jejich geometrie se **sloučí přímo do
objektu `pylones`** — žádný samostatný objekt `aerialway` se už nevytváří. Když
v patchi vedení není, ale lanovka ano, objekt `pylones` se vytvoří z lanovek.
Tím mají automaticky stejný materiál i texturu jako vedení (jsou to fyzicky jeden
objekt). Odebrán checkbox i přepínač `generate_aerialway` (panel, properties,
config). Měněno: `main.py` (sloučení přes `MeshData.merge` pod
`generate_powerlines`), `blender/panels.py`, `blender/properties.py`,
`blender/operators.py`, `blender/batch_processing.py`, `config.py`.

### File-mode kopíruje i Pylons.dds / WindTurbine.dds do Textures
Ve file-mode (Import to Blender vypnutý) se atlasy budov a střechy kopírovaly do
`Working/Autogen/Textures` (`_ensure_autogen_textures`), ale **`Pylons.dds` a
`WindTurbine.dds` ne** — ty se kopírovaly jen při importu do Blenderu. Doplněno:
ve file-mode se teď taky zavolá `_copy_asset_textures_for_result`, takže textury
vygenerovaných objektů (pylony/vedení/lanovky, turbíny) jsou v Textures i bez
importu. Měněno: `blender/operators.py`.

### Při více kolekcích se Outliner sbalí
Při „Import Patch“ se po načtení, **když je víc než 2 kolekce `Condor_…`**,
v Outlineru sbalí obsah kolekcí (přes `outliner.show_one_level(open=False)`), aby
nebylo všechno rozbalené a nepřehledné. Při jednom/dvou patchích zůstane vše
otevřené. Platí pro single i range import. Přidáno `_collapse_collections_if_many`
v `blender/operators.py`. (Oprava: funkce prochází **všechna okna**
`window_manager.windows` a sbalení běží **odloženě přes `bpy.app.timers`** ~0,2 s
po importu — Outliner se přestaví až po doběhnutí importu, takže sbalení během
importu nemělo na čem pracovat. Timer to spustí až v ustáleném stavu.)

### Oprava: LOD1 kolekce se v range importu posunou jako LOD0
Při „Import Patch“ v range módu (se zapnutým terénem) se patche rozmisťují do
mřížky posunem o ±5760 m. Posouvala se ale jen LOD0 kolekce
`Condor_<landscape>_<patch>`, kdežto `…_<patch>_LOD1` zůstávala na (0,0,0) — proto
všechny LOD1 patche ležely přes sebe v jednom rohu. Opraveno: posun se aplikuje na
**obě** kolekce (LOD0 i LOD1). Měněno: `blender/operators.py` (`_import_range`).

### Souhrnný generate_log.txt v Working/Autogen
Při generování se v `Working/Autogen` tvoří **`generate_log.txt`**. Log se jen
**doplňuje, nikdy nepřepisuje** (při dávce jdou patche pod sebe, oddělené čárou).
Ke každému patchi se připíše blok:
```
031018
Cas generovani: 7.0 s
Objekty:
  Highrise_walls
  flat_roof_1
  ...
  pylones
  chimney
  Highrise_walls_LOD1
  flat_roof_1_LOD1
  ...
  pylones_LOD1
Letiste: Praha-Ruzyne
------------------------------------------------------------
```
Tedy číslo patche, čas generování, **objekty pod sebou** (nejdřív LOD0, pak LOD1
s příponou `_LOD1`; `chimney` když běží Batch) a — pokud střed letiště padne do
patche (z `airport/airports.json`) — jeho název (řádek se jinak vynechá). Píše se
**vždy** (oba režimy). Přidáno `append_run_log` / `airports_in_patch` v
`blender/batch_processing.py`, volání v `blender/operators.py`.

Pokud má patch **lanovky** (sloučené do `pylones`), v logu se u toho objektu
napíše `pylones (aerialway)`. Detekce přes nové pole `aerialways` v
`PipelineStats` (main.py), nastavené v aerialway bloku.

**Úprava formátu:** LOD0 a LOD1 jsou teď **dva samostatné bloky** (`<patch>` a
`<patch>_LOD1`), každý s časem, objekty a letištěm. V logu se vypisují **finální
objekty** — `gabled_roofs_lod0` a `hipped_roofs` se při importu slučují do
`houses`, takže se vypíše jen `houses` (ne syrové skupiny střech). Čas je pro oba
bloky stejný (celý patch se generuje najednou).

**Souhrn + angličtina:** na začátku každého běhu se do logu zapíše souhrn
(`Total patches`, počty LOD0/LOD1, `Total time` = součet časů patchů), pod ním
pak jednotlivé bloky. Celý log je teď **anglicky** (`Generation time`, `Objects`,
`Airport`). Bloky se během běhu sbírají a souhrn + bloky se zapíšou najednou na
konci (zůstává append-only). `write_run_summary` v `blender/batch_processing.py`.

### Lanovky: kabinkový stožár má LOD1 low model
LOD0 i LOD1 lanovek jsou jinak stejné, **jen kabinkový stožár** `Pylon_AerialCab`
používá pro **LOD1** odlehčený model `Pylon_AerialCab_ns_low.obj`. Ostatní
(sedačkový stožár, kolečka, lana, kabiny/sedačky) je v obou LODech stejné.
Přidáno `AERIALWAY_LOW_FILES` + `AERIAL_LOW` a parametr `low` ve
`generate_aerialway_meshes`; `main.py` teď generuje aerialway dvakrát (LOD0
detailní, LOD1 low) místo deepcopy, šablony se načtou jednou a sdílí.
Když `Pylon_AerialCab_ns_low.obj` ve složce není, LOD1 spadne zpět na detailní.
Měněno: `generators/aerialway.py`, `main.py`.

### Komíny navíc z volitelného souboru chimney.osm
Pokud je ve složce `Working/Autogen` soubor `chimney.osm` (nebo `Chimney.osm`),
plugin z něj **vytáhne komíny patřící danému patchi** (`man_made=chimney` uvnitř
dlaždice) a **doplní je do `map_<patch>.osm`** ještě před generováním komínů —
takže se vygenerují stejně jako komíny z hlavního OSM. Soubor se čte **streamovaně**
(šetří paměť) a ID, která už v map OSM jsou, se přeskočí (žádné duplikáty).
**Když soubor `chimney.osm` ve složce není, nic se nemění a vše běží jako dosud.**
Funguje pro file-mode (Batch) i pro tlačítko „Import Chimneys“. Přidáno:
`inject_chimneys_from_source` v `blender/batch_processing.py`, volání tamtéž
a v `blender/operators.py`.

### „Other objects“ přesunuto pod Powerlines + popisek o lanovkách
Rozbalovací sekce **„Other objects“** (kontejner pro doplňkové objekty — teď
Chimney, může přibývat) je teď **rozbalovací box přímo pod oknem Powerlines**
v hlavním panelu, místo samostatného podpanelu dole (podpanely se v Blenderu
vždy kreslí až za celým hlavním panelem, takže nešly vložit doprostřed). Sbalit/
rozbalit přes šipku (nová property `show_other_objects`). Původní podpanel
`CONDOR_PT_other_objects_panel` zrušen. Zároveň upraven popisek u Powerlines na
`Pylons + cables + aerialways -> 'pylones' (Pylons.dds)`, protože lanovky teď
spadají do `pylones`. Měněno: `blender/panels.py`, `blender/properties.py`.

### Kabinky otočené o 180° kolem Z (zalomení závěsu ven)
Kabinky se teď otáčí o **180° kolem svislé osy**, aby zalomení závěsu, kterým
sahá nahoru k lanu, mířilo **ven** (od středu stožáru) a přejelo přes kolečka —
předtím mířilo dovnitř. Platí to pro obě lana (přijíždějící i odjíždějící). Týká
se **jen kabinek**, sedačky si nechávají natočení podle směru jízdy. Přidána mapa
`AERIAL_CARRIER_YAW` (kabinka = π, sedačka = 0) a parametr `yaw_offset` v
`_place_carriers`. Měněno: `generators/aerialway.py`.

### Lanovky sdílí stejný materiál jako pylony vedení
Lanovky i elektrické vedení používají stejnou texturu `Pylons.dds`, ale doteď
se exportovaly jako **dva různé materiály** (`condor_aerialway` vs
`condor_pylones`). Nově **sdílí jeden materiál**: objekt `aerialway` v OBJ
zůstává oddělený, ale odkazuje na materiál `pylones` (stejné `newmtl`, stejné
`usemtl`, stejná textura). Přidána mapa `MATERIAL_ALIAS` v `config.py`
(`aerialway` → `pylones`); `export_condor_obj_mtl` (`io/obj_exporter.py`) píše
`newmtl` jen jednou na materiál a `usemtl` přes alias. Platí pro file-mode
i import do Blenderu.
