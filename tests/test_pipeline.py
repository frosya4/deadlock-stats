import csv
import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GOLD_COLUMNS = [
    "team",
    "name",
    "hero",
    "total",
    "gpm",
    "Players",
    "Assists",
    "LaneCreeps",
    "Neutrals",
    "Bosses",
    "Treasure",
    "Denies",
    "orbs",
    "passive_other",
    "lost_on_death",
]


@pytest.fixture(scope="module")
def raw_dir(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    for name in ("msgs.tsv", "ents.jsonl"):
        with gzip.open(FIXTURES / f"133162474.{name}.gz", "rb") as src:
            with (raw / name).open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return raw


@pytest.fixture(scope="module")
def processed(raw_dir, tmp_path_factory):
    from app.pipeline.process import process

    out = tmp_path_factory.mktemp("processed")
    header = json.loads(
        (FIXTURES / "133162474.header.json").read_text(encoding="utf-8")
    )
    summary = process(raw_dir, header, out)
    return summary, json.loads((out / "match.json").read_text(encoding="utf-8")), out


def test_reference_match(processed):
    summary, data, out = processed
    match = data["match"]
    assert match["winner"] == "Sapphire (Archmother)"
    assert match["duration"] == "32:51"
    player = next(p for p in data["players"] if p["name"] == "Игрок 8")
    assert player["hero"] == "Виктор"
    assert [player[k] for k in ("kills", "deaths", "assists", "netWorth", "gpm")] == [
        26,
        1,
        18,
        60851,
        1852,
    ]
    assert len(data["killFeed"]) == 93
    assert len(data["chat"]) == 33
    assert summary == {
        **{k: match[k] for k in ("matchId", "startUTC", "duration", "winner")},
        "heroes": [
            {k: p[k] for k in ("hero", "team", "name")} for p in data["players"]
        ],
        "score": {"amber": 26, "sapphire": 64},
    }
    assert {p.name for p in out.iterdir()} == {
        "match.json",
        "postmatch_raw.json",
        "players.csv",
        "items.csv",
        "deaths.csv",
        "chat.csv",
        "gold.csv",
    }
    assert all("laneColor" not in p for p in data["players"])


def test_player_timeline(processed, raw_dir):
    _, data, _ = processed
    timeline = data["playerTimeline"]
    assert len(timeline) == 12
    for points in timeline.values():
        assert len(points) >= 60
        assert all(p["t"] >= 0 for p in points)
        assert all(a["t"] < b["t"] for a, b in zip(points, points[1:]))
        assert set(points[0]) == {
            "t",
            "netWorth",
            "kills",
            "deaths",
            "assists",
            "level",
            "lastHits",
            "heroDamage",
        }
    player = next(p for p in data["players"] if p["name"] == "Игрок 8")
    assert abs(timeline[str(player["slot"])][-1]["netWorth"] - 60851) <= 1000
    entities = [
        json.loads(line)
        for line in (raw_dir / "ents.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rules = next(
        e["f"] for e in reversed(entities) if e["cls"] == "CCitadelGameRulesProxy"
    )
    start = next(v for k, v in rules.items() if k.endswith("m_flGameStartTime"))
    snap = next(
        e
        for e in entities
        if e["cls"] == "CCitadelPlayerController"
        and e["tag"] != "final"
        and e["tick"] / 64 >= start
    )
    point = timeline[str(snap["f"]["m_unLobbyPlayerSlot"])][0]
    assert point["t"] == snap["tick"] / 64 - start
    assert point["netWorth"] == snap["f"]["m_iGoldNetWorth"]


def test_matches_prototype(processed):
    _, data, _ = processed
    expected = json.loads(
        (FIXTURES / "133162474.expected.json").read_text(encoding="utf-8")
    )
    assert [
        {k: (v["id"] if k == "lane" else v) for k, v in p.items()
         if k not in {"gpm", "fantasy", "grade", "rank", "mvp", "hsTimeline", "sourceTimeline"}}
        for p in data["players"]
    ] == expected["players"]
    for key in ("objectives", "damageMatrix"):
        assert data[key] == expected[key]


def test_missing_postmatch(raw_dir, tmp_path):
    from app.pipeline.errors import DemoError
    from app.pipeline.process import process

    with (raw_dir / "msgs.tsv").open() as src, (tmp_path / "msgs.tsv").open("w") as dst:
        dst.writelines(line for line in src if line.split("\t")[1] != "316")
    with pytest.raises(DemoError) as exc:
        process(tmp_path, {}, tmp_path / "out")
    assert (
        exc.value.user_message
        == "В демке нет итогов матча (матч не доигран или запись оборвана)"
    )
    assert not (tmp_path / "out" / "match.json").exists()


def test_gold_timeline(processed):
    _, data, out = processed
    meta = json.loads((out / "postmatch_raw.json").read_text(encoding="utf-8"))
    assert len(data["goldTimeline"]) == 12
    for player in meta["players"]:
        expected = [
            {
                "t": st["timeStampS"],
                **{
                    g.get("source", "k_eOther").removeprefix("k_e"): g.get("gold", 0)
                    for g in st.get("goldSources", [])
                },
            }
            for st in player["stats"]
        ]
        assert data["goldTimeline"][str(player["playerSlot"])] == expected


def test_gold_csv(processed):
    _, data, out = processed
    with (out / "gold.csv").open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        assert reader.fieldnames == GOLD_COLUMNS
        rows = list(reader)
    assert len(rows) == 12
    for row, p in zip(rows, data["players"]):
        assert row["name"] == p["name"]
        assert int(row["gpm"]) == p["gpm"]
        assert int(row["total"]) == p["netWorth"]
        assert int(row["lost_on_death"]) == p["goldDeathLoss"]
        sources = GOLD_COLUMNS[5:12]
        assert all(int(row[k]) == p["gold"].get(k, {}).get("gold", 0) for k in sources)
        assert int(row["orbs"]) == sum(g["orbs"] for g in p["gold"].values())
        assert int(row["passive_other"]) == max(
            0, p["netWorth"] - sum(int(row[k]) for k in sources) - int(row["orbs"])
        )


def test_sparse_postmatch_fields(raw_dir, tmp_path, processed):
    from app.pipeline.process import process
    import citadel_usermessages_pb2 as um
    import citadel_gcmessages_common_pb2 as gc

    with (raw_dir / "msgs.tsv").open() as src, (tmp_path / "msgs.tsv").open("w") as dst:
        for line in src:
            tick, kind, payload = line.rstrip("\n").split("\t")
            if kind != "316":
                continue
            msg = um.CCitadelUserMsg_PostMatchDetails.FromString(bytes.fromhex(payload))
            meta = gc.CMsgMatchMetaDataContents.FromString(msg.match_details)
            for p in meta.match_info.players:
                for st in p.stats:
                    for gold in st.gold_sources:
                        gold.ClearField("source")
            for dealer in meta.match_info.damage_matrix.damage_dealers:
                for source in dealer.damage_sources:
                    for target in source.damage_to_players:
                        target.ClearField("damage")
            msg.match_details = meta.SerializeToString()
            dst.write(f"{tick}\t316\t{msg.SerializeToString().hex()}\n")
    shutil.copyfile(raw_dir / "ents.jsonl", tmp_path / "ents.jsonl")
    process(tmp_path, {}, tmp_path / "out")
    result = json.loads((tmp_path / "out" / "match.json").read_text(encoding="utf-8"))
    assert all(set(p["gold"]) == {"Other"} for p in result["players"])
    assert all(v == 0 for row in result["damageMatrix"].values() for v in row.values())


def test_build_refs(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "heroes.vdata").write_text(
        "\thero_test = \n\t{\n\t\tm_HeroID = 100\n\t}\n", encoding="utf-8"
    )
    (scripts / "abilities.vdata").write_text(
        '\tupgrade_test = \n\t{\n\t\t_class = "citadel_item"\n\t\tm_eAbilityType = "EAbilityType_Item"\n\t}\n',
        encoding="utf-8",
    )
    game = tmp_path / "citadel"
    loc = game / "resource" / "localization" / "test"
    loc.mkdir(parents=True)
    for lang, hero, item in [
        ("russian", "Герой", "Предмет"),
        ("english", "Hero", "Item"),
    ]:
        (loc / f"test_{lang}.txt").write_text(
            f'"hero_test:n" "{hero}"\n"upgrade_test" "{item}"\n', encoding="utf-8-sig"
        )
    out = tmp_path / "refs.json"
    result = subprocess.run(
        [
            sys.executable,
            str(FIXTURES.parents[1] / "tools/build_refs.py"),
            "--game-dir",
            str(game),
            "--scripts-dir",
            str(scripts),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    refs = json.loads(out.read_text(encoding="utf-8"))
    assert refs["heroes"]["100"] == {"code": "hero_test", "ru": "Герой", "en": "Hero"}
    assert list(refs["items"].values())[0]["ru"] == "Предмет"


def test_csv_formula_cells_and_duplicate_names(raw_dir, tmp_path):
    from app.pipeline.process import process
    import json
    with (raw_dir / "ents.jsonl").open(encoding="utf-8") as src, (tmp_path / "ents.jsonl").open("w", encoding="utf-8", newline="\n") as dst:
        for line in src:
            obj = json.loads(line)
            if obj["cls"] == "CCitadelPlayerController" and "m_iszPlayerName" in obj["f"]:
                obj["f"]["m_iszPlayerName"] = "=1+1"
            dst.write(json.dumps(obj, ensure_ascii=False) + "\n")
    shutil.copyfile(raw_dir / "msgs.tsv", tmp_path / "msgs.tsv")
    process(tmp_path, {}, tmp_path / "out")
    data = json.loads((tmp_path / "out/match.json").read_text(encoding="utf-8"))
    assert len(data["damageMatrix"]) == 12
    assert all(len(row) == 12 for row in data["damageMatrix"].values())
    with (tmp_path / "out/players.csv").open(encoding="utf-8-sig", newline="") as fh:
        assert all(row["name"] == "'=1+1" for row in csv.DictReader(fh, delimiter=";"))
