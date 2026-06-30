# Pravidla
 vždy dodržuj tato pravidla:

Před každou změnou kódu:

- Nejprve vysvětli, kde je problém.
- Napiš, jak ho chceš opravit.
- Uveď, které soubory budeš měnit.
- Počkej na moje potvrzení.

Bez mého potvrzení neupravuj žádný kód.

Zachovej původní styl a formátování projektu.

Neměň části kódu, které nesouvisí s řešeným problémem.
po každě změne kodu napiš zmenu do md které se jmenuje  uprav_pluginu kde popisuješ co to přesně d+ělá, alew srozumitelně ne kod

# Příkaz "zálohuj na web"

Když řeknu "zálohuj na web" (nebo podobně, např. "záloha na web"), **spusť z
kořene pluginu** tento jeden script:
```bash
python zaloha_na_web.py
```

Ten script (`zaloha_na_web.py`) udělá zálohu do `na web/condor_buildings/`:
připraví čerstvou složku (starou smaže), zkopíruje `main.py`, `__init__.py`,
`config.py` a všechny podsložky s obsahem KROMĚ `puvodni`, `__pycache__`,
`claude` (a `__pycache__` vynechá i uvnitř kopírovaných složek).

Nespouštěj syrové `cp/rm/find` — všechno dělá ten script (a má povolené jen
jeho spuštění, aby nevyskakovalo potvrzovací okno). 