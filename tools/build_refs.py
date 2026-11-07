"""Build references from local game localization and extracted vdata scripts.

Extract/decompile heroes.vdata_c and abilities.vdata_c with Source2Viewer-CLI
first, then pass the directory containing heroes.vdata and abilities.vdata.
No game files or temporary directories are modified.
"""

import argparse
import glob
import json
import re
from pathlib import Path

G = r"C:/Program Files/steam/steamapps/common/Deadlock/game/citadel"


def murmur2(key: bytes, seed=0x31415926):
    m = 0x5BD1E995
    r = 24
    ln = len(key)
    h = (seed ^ ln) & 0xFFFFFFFF
    i = 0
    while ln >= 4:
        k = int.from_bytes(key[i : i + 4], "little")
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> r
        k = (k * m) & 0xFFFFFFFF
        h = (h * m) & 0xFFFFFFFF
        h ^= k
        i += 4
        ln -= 4
    if ln == 3:
        h ^= key[i + 2] << 16
    if ln >= 2:
        h ^= key[i + 1] << 8
    if ln >= 1:
        h ^= key[i]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h


def blocks(path):
    s = Path(path).read_text(encoding="utf-8").replace("\r", "")
    parts = re.split(r"^\t([A-Za-z_]\w*) = \n\t\{", s, flags=re.M)
    out = {}
    for i in range(1, len(parts), 2):
        body = parts[i + 1].split("\n\t}", 1)[0]
        f = {
            k: v.strip().strip('"')
            for k, v in re.findall(r"^\t\t(\w+) = (.+)$", body, re.M)
        }
        out[parts[i]] = f
    return out


def loc(game_dir, lang):
    d = {}
    for fn in glob.glob(str(game_dir) + f"/resource/localization/*/*_{lang}.txt"):
        for k, v in re.findall(
            r'^\s*"([^"]+)"\s+"((?:[^"\\]|\\.)*)"',
            Path(fn).read_text(encoding="utf-8-sig", errors="replace"),
            re.M,
        ):
            d.setdefault(k, re.sub(r"#\|\w\|#", "", v))
    return d


def build(game_dir, scripts_dir):
    ru, en = loc(game_dir, "russian"), loc(game_dir, "english")
    heroes = {}
    for k, f in blocks(Path(scripts_dir) / "heroes.vdata").items():
        if "m_HeroID" in f:
            hid = int(f["m_HeroID"])
            heroes[hid] = {
                "code": k,
                "ru": ru.get(k + ":n") or ru.get(k) or k,
                "en": en.get(k + ":n") or en.get(k) or k,
            }
    items = {}
    for k, f in blocks(Path(scripts_dir) / "abilities.vdata").items():
        items[murmur2(k.encode())] = {
            "code": k,
            "ru": ru.get(k) or en.get(k) or k,
            "en": en.get(k) or k,
            "class": f.get("_class"),
            "slot": f.get("m_eItemSlotType"),
            "tier": f.get("m_iItemTier"),
            "atype": f.get("m_eAbilityType"),
        }
    return heroes, items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game-dir", type=Path, default=Path(G), help="Deadlock game/citadel directory"
    )
    parser.add_argument(
        "--scripts-dir",
        type=Path,
        required=True,
        help="Directory with decompiled heroes.vdata and abilities.vdata",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "app/pipeline/refs.json",
    )
    args = parser.parse_args()
    heroes, items = build(args.game_dir, args.scripts_dir)
    if not heroes or not items:
        parser.error("No heroes or items found; check the extracted vdata files")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump({"heroes": heroes, "items": items}, fh, ensure_ascii=False, indent=0)
        fh.write("\n")
    print(f"Wrote {len(heroes)} heroes and {len(items)} items to {args.out}")


if __name__ == "__main__":
    main()
