"""
Záloha pluginu do 'na web/condor_buildings/'.

Spouští se příkazem (z kořene pluginu):
    python zaloha_na_web.py

Co dělá (viz CLAUDE.md, příkaz "zálohuj na web"):
  1. připraví čerstvou složku 'na web/condor_buildings/' (starou smaže),
  2. zkopíruje 3 soubory: main.py, __init__.py, config.py,
  3. zkopíruje všechny podsložky i s obsahem KROMĚ: puvodni, __pycache__, claude,
  4. ze zkopírovaných složek vynechá všechny podsložky __pycache__.

Je to čistě kopírování souborů (nic se z projektu nemaže, jen se přepíše
obsah cílové zálohové složky).
"""

import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(ROOT, "na web", "condor_buildings")

FILES = ["main.py", "__init__.py", "config.py", "manual_eng.md"]
SKIP_DIRS = {"na web", "__pycache__", "puvodni", "claude", ".claude"}


def main():
    # Fresh destination
    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST)

    # The 3 files
    for f in FILES:
        src = os.path.join(ROOT, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DEST, f))
            print(f"  file  {f}")

    # All folders except the skipped ones (and without __pycache__ inside)
    for name in sorted(os.listdir(ROOT)):
        src = os.path.join(ROOT, name)
        if not os.path.isdir(src):
            continue
        if name in SKIP_DIRS:
            print(f"  skip  {name}/")
            continue
        shutil.copytree(
            src, os.path.join(DEST, name),
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        print(f"  dir   {name}/")

    print(f"Hotovo -> {DEST}")


if __name__ == "__main__":
    main()
