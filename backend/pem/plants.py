"""The plants, and the many ways the source files spell them."""
from __future__ import annotations

import re

# id, display name, cluster ("DP" = detail parts, "ASSY" = assembly)
PLANTS = [
    {"id": "TASL-NGP", "name": "TASL-Nagpur", "cluster": "DP"},
    {"id": "TASL-BLR", "name": "TASL-Bangalore", "cluster": "DP"},
    {"id": "TSAL", "name": "TSAL-Hyd", "cluster": "DP"},
    {"id": "TCOE", "name": "TCOE-Hyd", "cluster": "DP"},
    {"id": "TLMAL", "name": "TLMAL-Hyd", "cluster": "ASSY"},
    {"id": "TASL-HYD", "name": "TASL-Hyd", "cluster": "ASSY"},
    {"id": "TBAL", "name": "TBAL-Hyd", "cluster": "ASSY"},
    {"id": "TASL-MCA", "name": "TASL-MCA", "cluster": "ASSY"},
]
PLANT_IDS = [p["id"] for p in PLANTS]

# Spellings seen in the workbooks and decks, reduced to letters and digits.
_ALIASES = {
    "TASL-NGP": ["taslngp", "taslnagpur", "nagpur", "ngp", "tasln01", "n01"],
    "TASL-BLR": ["taslblr", "taslbangalore", "taslbanglore", "taslbengaluru", "bangalore", "banglore", "blr", "taslb01", "b01"],
    "TSAL": ["tsal", "tsalhyd"],
    "TCOE": ["tcoe", "tcoehyd"],
    "TLMAL": ["tlmal", "tlmalhyd", "tlml"],
    "TASL-HYD": ["taslhyd", "taslh01", "h01", "taslhyderabad"],
    "TBAL": ["tbal", "tbalhyd", "tball", "tataboeing"],
    "TASL-MCA": ["taslmca", "taslmcahyd", "mca"],
}
_LOOKUP = {alias: pid for pid, aliases in _ALIASES.items() for alias in aliases}


def canonical_plant(name: object) -> str | None:
    """The plant id for a name as written in a source file, or None if unknown.

    "TASL -Nagpur", "TASL\\n-Nagpur", "TASL NGP" and "TASL(MCA)-HYD" all resolve.
    Unknown names return None rather than a guess; callers decide what to do.
    """
    key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    if not key:
        return None
    if key in _LOOKUP:
        return _LOOKUP[key]
    # "TASL(MCA)-HYD" reduces to "taslmcahyd"; "TASL-NGP Sustainability Summary" starts with an alias.
    for alias in sorted(_LOOKUP, key=len, reverse=True):
        if len(alias) >= 4 and key.startswith(alias):
            return _LOOKUP[alias]
    return None


def plant_name(plant_id: str) -> str:
    return next((p["name"] for p in PLANTS if p["id"] == plant_id), plant_id)
