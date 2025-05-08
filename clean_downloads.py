#!/usr/bin/env python3

import re
from pathlib import Path

CLEAN_DIR = "/home/tenzin/Downloads"

dup_pattern = re.compile(r"^(.+?)\((\d+)\)(\.[^.]+)?$")

for f in Path(CLEAN_DIR).iterdir():
    if not f.is_file():
        continue

    m = dup_pattern.match(f.name)
    if not m:
        continue

    base, _, ext = m.group(1), m.group(2), m.group(3) or ""
    original = Path(CLEAN_DIR) / f"{base}{ext}"
    if original.exists():
        print(f"Removing duplicate: {f.name}")
        f.unlink()
