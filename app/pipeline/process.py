"""Decode dldump output using the vendored Deadlock protobufs and references."""

import collections
import csv
import datetime
import json
import sys
import unicodedata
from pathlib import Path

from .errors import DemoError

# Generated modules use bare sibling imports. Do not regenerate or patch them.
PROTO_DIR = str(Path(__file__).resolve().parents[1] / "proto")
STEAMLEARN_DIR = str(Path(__file__).resolve().parent / "_vendor")
if STEAMLEARN_DIR not in sys.path:
    sys.path.insert(0, STEAMLEARN_DIR)
if PROTO_DIR not in sys.path:
    sys.path.insert(0, PROTO_DIR)
import citadel_usermessages_pb2 as um
import citadel_gcmessages_common_pb2 as gc
from google.protobuf.json_format import MessageToDict

with Path(__file__).with_name("refs.json").open(encoding="utf-8") as fh:
    refs = json.load(fh)
H = {int(k): v for k, v in refs["heroes"].items()}
I = {int(k): v for k, v in refs["items"].items()}
TEAM = {
    "k_ECitadelLobbyTeam_Team0": "Amber (Hidden King)",
    "k_ECitadelLobbyTeam_Team1": "Sapphire (Archmother)",
}
TEAMNUM = {2: "k_ECitadelLobbyTeam_Team0", 3: "k_ECitadelLobbyTeam_Team1"}


def hero(h):
    return H.get(h, {}).get("ru", str(h))


def item(i):
    x = I.get(i)
    return x["ru"] if x else f"#{i}"


def mmss(s):
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


def write_json(path, value):
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1)


def player_timeline(snaps, game_start, slots):
    """Use controller snapshots, keyed by lobby slot, on the game clock."""
    fields = {
        "netWorth": "m_iGoldNetWorth",
        "kills": "m_iPlayerKills",
        "deaths": "m_iDeaths",
        "assists": "m_iPlayerAssists",
        "level": "m_iLevel",
        "lastHits": "m_iLastHits",
        "heroDamage": "m_iHeroDamage",
    }
    timeline = {str(s): [] for s in slots}
    for tick in sorted(snaps):
        t = tick / 64.0 - game_start
        if t < 0:
            continue
        for f in snaps[tick]:
            slot = str(f.get("m_unLobbyPlayerSlot", -1))
            if slot not in timeline:
                continue
            point = {"t": t, **{k: int(f.get(v, 0)) for k, v in fields.items()}}
            if timeline[slot] and timeline[slot][-1]["t"] == t:
                timeline[slot][-1] = point
            else:
                timeline[slot].append(point)
    return timeline


def csv_writer(fh):
    """Escape spreadsheet formulas in every text cell, preserving numeric cells."""
    writer = csv.writer(fh, delimiter=";", lineterminator="\n")

    class SafeWriter:
        def writerow(self, row):
            def safe(value):
                if isinstance(value, str):
                    stripped = value
                    while stripped and (stripped[0].isspace() or unicodedata.category(stripped[0]).startswith("C")):
                        stripped = stripped[1:]
                    if value.startswith(("\t", "\r")) or stripped.startswith(("=", "+", "-", "@")):
                        return "'" + value
                return value
            return writer.writerow([safe(value) for value in row])
    return SafeWriter()


def write_gold(path, players):
    sources = [
        "Players",
        "Assists",
        "LaneCreeps",
        "Neutrals",
        "Bosses",
        "Treasure",
        "Denies",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv_writer(fh)
        writer.writerow(
            [
                "team",
                "name",
                "hero",
                "total",
                "gpm",
                *sources,
                "orbs",
                "passive_other",
                "lost_on_death",
            ]
        )
        for p in players:
            gold = [p["gold"].get(k, {}).get("gold", 0) for k in sources]
            orbs = sum(g["orbs"] for g in p["gold"].values())
            # Same residual calculation as the prototype's gold table.
            rest = max(0, p["netWorth"] - sum(gold) - orbs)
            writer.writerow(
                [
                    p["team"],
                    p["name"],
                    p["hero"],
                    p["netWorth"],
                    p["gpm"],
                    *gold,
                    orbs,
                    rest,
                    p["goldDeathLoss"],
                ]
            )


def process(raw_dir: Path, header: dict, out_dir: Path) -> dict:
    """Write match JSON/CSV artifacts and return the match-list summary.

    Missing postmatch metadata is an expected user-facing DemoError. The caller
    owns temporary-file cleanup and publication of the completed output folder.
    """
    # ---- messages
    meta = None
    chat = []
    kills = []
    bosses = []
    wheel = []
    pings = []
    chatev = []
    gameover = None
    idols = []
    CLS = {
        314: um.CCitadelUserMsg_ChatMsg,
        319: um.CCitadelUserMsg_HeroKilled,
        347: um.CCitadelUserMsg_BossKilled,
        312: um.CCitadelUserMsg_ChatWheel,
        303: um.CCitadelUserMsg_MapPing,
        317: um.CCitadelUserMsg_ChatEvent,
        320: um.CCitadelUserMsg_ReturnIdol,
        346: um.CCitadelUserMessage_GameOver,
    }
    msgcount = collections.Counter()
    with (raw_dir / "msgs.tsv").open(encoding="utf-8") as source:
        for line in source:
            t, i, h = line.rstrip("\n").split("\t")
            t = int(t)
            i = int(i)
            msgcount[i] += 1
            if i == 316:
                m = um.CCitadelUserMsg_PostMatchDetails()
                m.ParseFromString(bytes.fromhex(h))
                md = gc.CMsgMatchMetaDataContents()
                md.ParseFromString(m.match_details)
                meta = MessageToDict(md)["matchInfo"]
                continue
            if i not in CLS:
                continue
            m = CLS[i]()
            m.ParseFromString(bytes.fromhex(h))
            d = MessageToDict(m)
            d["_tick"] = t
            (
                {
                    314: chat,
                    319: kills,
                    347: bosses,
                    312: wheel,
                    303: pings,
                    317: chatev,
                    320: idols,
                }
                .get(i, [])
                .append(d)
                if i != 346
                else None
            )
            if i == 346:
                gameover = d
    if meta is None:
        raise DemoError(
            "В демке нет итогов матча (матч не доигран или запись оборвана)"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "postmatch_raw.json", meta)
    # ---- entities
    final = {}
    snaps = collections.defaultdict(list)
    rules = {}
    with (raw_dir / "ents.jsonl").open(encoding="utf-8") as source:
        for line in source:
            d = json.loads(line)
            if d["cls"] == "CCitadelPlayerController":
                f = d["f"]
                if d["tag"] == "final":
                    final[int(f.get("m_unLobbyPlayerSlot", -1))] = f
                else:
                    snaps[d["tick"]].append(f)
            if d["cls"] == "CCitadelGameRulesProxy":
                rules = d["f"]
    gst = [v for k, v in rules.items() if k.endswith("m_flGameStartTime")]
    game_start = gst[0] if gst else 0

    def gtime(tick):
        return tick / 64.0 - game_start

    # player table
    slot2 = {}
    for p in meta["players"]:
        s = p["playerSlot"]
        f = final.get(s, {})
        slot2[s] = {"name": f.get("m_iszPlayerName", "?"), "hero": hero(p["heroId"])}
    pawn2slot = {}
    for s, f in final.items():
        if "m_hHeroPawn" in f:
            pawn2slot[int(f["m_hHeroPawn"]) & 0x3FFF] = s
    cs = meta["customUserStats"]
    csn = {c["id"]: c["name"] for c in cs}
    players = []
    for p in sorted(meta["players"], key=lambda x: (x["team"], x["playerSlot"])):
        s = p["playerSlot"]
        f = final.get(s, {})
        st = p["stats"][-1]
        cus = {
            csn.get(c["id"], c["id"]): c["value"] for c in st.get("customUserStats", [])
        }
        shots = cus.get("Enemy Hero Accuracy##Shots", 0)
        hits = cus.get("Enemy Hero Accuracy##Hits", 0)
        hs = cus.get("Enemy Hero Accuracy##Headshots", 0)
        build = []
        abil = []
        for it in p.get("items", []):
            x = I.get(it["itemId"], {})
            rec = {
                "t": it["gameTimeS"],
                "time": mmss(it["gameTimeS"]),
                "id": it["itemId"],
                "name": item(it["itemId"]),
                "en": x.get("en"),
                "code": x.get("code"),
                "slot": (x.get("slot") or "").replace("EItemSlotType_", ""),
                "tier": (x.get("tier") or "").replace("EModTier_", ""),
                "sold": mmss(it["soldTimeS"]) if it.get("soldTimeS") else None,
                "upgrade": it.get("upgradeId", 0),
                "imbued": (
                    item(it["imbuedAbilityId"]) if it.get("imbuedAbilityId") else None
                ),
            }
            (
                build
                if x.get("atype") == "EAbilityType_Item"
                or x.get("class") == "citadel_item"
                else abil
            ).append(rec)
        gs = {
            g.get("source", "k_eOther").replace("k_e", ""): g
            for g in st.get("goldSources", [])
        }
        players.append(
            {
                "slot": s,
                "name": f.get("m_iszPlayerName", "?"),
                "steamid64": f.get("m_steamID"),
                "accountId": p["accountId"],
                "team": TEAM[p["team"]],
                "teamKey": p["team"],
                "hero": hero(p["heroId"]),
                "heroEn": H.get(p["heroId"], {}).get("en"),
                "heroId": p["heroId"],
                "lane": p["assignedLane"],
                "level": p["level"],
                "kills": p["kills"],
                "deaths": p["deaths"],
                "assists": p["assists"],
                "kda": round((p["kills"] + p["assists"]) / max(1, p["deaths"]), 2),
                "netWorth": p["netWorth"],
                "gpm": (
                    round(p["netWorth"] / (meta["durationS"] / 60))
                    if meta["durationS"]
                    else 0
                ),
                "lastHits": p["lastHits"],
                "denies": p["denies"],
                "heroDamage": st.get("playerDamage", 0),
                "damageTaken": st.get("playerDamageTaken", 0),
                "objDamage": int(f.get("m_iObjectiveDamage", 0)),
                "creepDamage": st.get("creepDamage", 0),
                "neutralDamage": st.get("neutralDamage", 0),
                "bossDamage": st.get("bossDamage", 0),
                "healing": st.get("playerHealing", 0),
                "selfHealing": st.get("selfHealing", 0),
                "damageMitigated": st.get("damageMitigated", 0),
                "damageAbsorbed": st.get("damageAbsorbed", 0),
                "healPrevented": st.get("healPrevented", 0),
                "shotsHit": st.get("shotsHit", 0),
                "shotsMissed": st.get("shotsMissed", 0),
                "heroShots": shots,
                "heroHits": hits,
                "heroHeadshots": hs,
                "accuracyHero": round(100 * hits / shots, 1) if shots else None,
                "headshotRate": round(100 * hs / hits, 1) if hits else None,
                "parrySuccess": cus.get("Parry Success", 0),
                "parryMiss": cus.get("Parry Miss", 0),
                "weaponPower": st.get("weaponPower"),
                "techPower": st.get("techPower"),
                "maxHealth": st.get("maxHealth"),
                "gold": {
                    k: {
                        "gold": v.get("gold", 0),
                        "orbs": v.get("goldOrbs", 0),
                        "kills": v.get("kills", 0),
                    }
                    for k, v in gs.items()
                },
                "goldDeathLoss": st.get("goldDeathLoss", 0),
                "goldDenied": st.get("goldDenied", 0),
                "creepKills": st.get("creepKills", 0),
                "neutralKills": st.get("neutralKills", 0),
                "abandonAt": p.get("abandonMatchTimeS", 0),
                "pingsSent": len(p.get("pings", [])),
                "items": build,
                "abilities": abil,
                "timeline": [
                    {
                        k: x.get(k)
                        for k in (
                            "timeStampS",
                            "netWorth",
                            "kills",
                            "deaths",
                            "assists",
                            "playerDamage",
                            "level",
                            "creepKills",
                            "denies",
                            "playerHealing",
                        )
                    }
                    for x in p["stats"]
                ],
                "deathDetails": [
                    {
                        "t": d["gameTimeS"],
                        "time": mmss(d["gameTimeS"]),
                        "killerSlot": d.get("killerPlayerSlot"),
                        "killer": slot2.get(d.get("killerPlayerSlot"), {}).get(
                            "name", "(NPC/объект)"
                        ),
                        "killerHero": slot2.get(d.get("killerPlayerSlot"), {}).get(
                            "hero"
                        ),
                        "pos": d.get("deathPos"),
                        "killerPos": d.get("killerPos"),
                        "respawn_s": d.get("deathDurationS"),
                    }
                    for d in p.get("deathDetails", [])
                ],
                "customStats": cus,
            }
        )

    OBJ = {
        "Tier1": "Страж (T1)",
        "Tier2": "Хранитель (T2)",
        "BarrackBoss": "Казармы (Walker)",
        "TitanShieldGenerator": "Генератор щита Патрона",
        "Titan": "Патрон",
        "Core": "Ядро Патрона (финал)",
    }
    objs = []
    for o in meta["objectives"]:
        oid = o["teamObjectiveId"].replace("k_eCitadelTeamObjective_", "")
        base = oid.split("_")[0]
        lane = oid.split("_")[1] if "_" in oid else ""
        other = (
            "k_ECitadelLobbyTeam_Team1"
            if o["team"].endswith("0")
            else "k_ECitadelLobbyTeam_Team0"
        )
        objs.append(
            {
                "objective": oid,
                "name": OBJ.get(base, base) + (f" · {lane}" if lane else ""),
                "owner": TEAM[o["team"]],
                "destroyedBy": TEAM[other],
                "firstDamage": mmss(o.get("firstDamageTimeS", 0)),
                "destroyed": (
                    mmss(o["destroyedTimeS"]) if o.get("destroyedTimeS") else None
                ),
                "t": o.get("destroyedTimeS") or 0,
                "playerDamage": o.get("playerDamage", 0),
                "creepDamage": o.get("creepDamage", 0),
            }
        )
    objs.sort(key=lambda x: (x["t"] == 0, x["t"]))

    feed = []
    for k in kills:
        v = pawn2slot.get(k.get("entindexVictim"))
        a = pawn2slot.get(k.get("entindexAttacker"))
        gt = gtime(k["_tick"])
        feed.append(
            {
                "t": round(gt),
                "time": mmss(max(0, gt)),
                "victimSlot": v,
                "killerSlot": a,
                "assistSlots": [pawn2slot[x] for x in k.get("entindexAssisters", []) if x in pawn2slot],
                "victim": slot2.get(v, {}).get("name", "?"),
                "victimHero": slot2.get(v, {}).get("hero"),
                "victimTeam": next(
                    (TEAM[x["team"]] for x in meta["players"] if x["playerSlot"] == v),
                    None,
                ),
                "killer": (
                    slot2.get(a, {}).get("name", "NPC/объект")
                    if a is not None
                    else "NPC/объект"
                ),
                "killerHero": slot2.get(a, {}).get("hero") if a is not None else None,
                "assists": [
                    slot2[pawn2slot[x]]["name"]
                    for x in k.get("entindexAssisters", [])
                    if x in pawn2slot
                ],
            }
        )

    dm = meta["damageMatrix"]
    mat = {}
    for dd in dm.get("damageDealers", []):
        s = dd["dealerPlayerSlot"]
        for src in dd.get("damageSources", []):
            for tp in src.get("damageToPlayers", []):
                mat.setdefault(s, {}).setdefault(tp["targetPlayerSlot"], 0)
                mat[s][tp["targetPlayerSlot"]] += (tp.get("damage") or [0])[-1]

    nw = []
    for tick in sorted(snaps):
        row = {
            "t": round(gtime(tick)),
            "Amber (Hidden King)": 0,
            "Sapphire (Archmother)": 0,
        }
        for f in snaps[tick]:
            tn = TEAMNUM.get(int(f.get("m_iTeamNum", 0)))
            if tn:
                row[TEAM[tn]] += int(f.get("m_iGoldNetWorth", 0))
        if row["t"] >= 0:
            nw.append(row)

    chatout = [
        {
            "time": mmss(max(0, gtime(c["_tick"]))),
            "playerSlot": c.get("playerSlot"),
            "player": slot2.get(c.get("playerSlot"), {}).get("name", "?"),
            "hero": slot2.get(c.get("playerSlot"), {}).get("hero"),
            "all": c.get("allChat", False),
            "text": c.get("text", ""),
        }
        for c in chat
    ]
    pingcnt = collections.Counter(
        slot2.get(p.get("pingData", {}).get("senderPlayerSlot"), {}).get("name", "?")
        for p in pings
        if p.get("pingData", {}).get("senderPlayerSlot") is not None
    )
    start = datetime.datetime.fromtimestamp(
        int(meta["startTime"]), datetime.timezone.utc
    )
    hdr = header
    match = {
        "matchId": meta["matchId"],
        "startUTC": start.isoformat(),
        "durationS": meta["durationS"],
        "duration": mmss(meta["durationS"]),
        "winner": TEAM[meta["winningTeam"]],
        "outcome": meta["matchOutcome"],
        "gameMode": meta["gameMode"],
        "matchMode": meta["matchMode"],
        "server": hdr.get("server_name"),
        "build": hdr.get("game_directory"),
        "midBoss": [
            {
                "killedBy": TEAM[m["teamKilled"]],
                "claimedBy": TEAM[m["teamClaimed"]],
                "time": mmss(m["destroyedTimeS"]),
            }
            for m in meta.get("midBoss", [])
        ],
        "teamTotals": {
            TEAM[t]: {
                k: sum(p[k] for p in players if p["teamKey"] == t)
                for k in (
                    "kills",
                    "deaths",
                    "assists",
                    "netWorth",
                    "heroDamage",
                    "objDamage",
                    "healing",
                    "lastHits",
                )
            }
            for t in TEAM
        },
    }
    data = {
        "match": match,
        "players": players,
        "objectives": objs,
        "killFeed": feed,
        "damageMatrix": {
            str(s): {str(t): mat.get(s, {}).get(t, 0) for t in slot2}
            for s in slot2
        },
        "netWorthTimeline": nw,
        "chat": chatout,
        "pings": dict(pingcnt),
        "chatWheelCount": len(wheel),
        "messageTypeCounts": {str(k): v for k, v in msgcount.items()},
    }
    data["playerTimeline"] = player_timeline(snaps, game_start, slot2)
    data["goldTimeline"] = {
        str(p["playerSlot"]): [
            {
                "t": st["timeStampS"],
                **{
                    g.get("source", "k_eOther").removeprefix("k_e"): g.get("gold", 0)
                    for g in st.get("goldSources", [])
                },
            }
            for st in p["stats"]
        ]
        for p in meta["players"]
    }
    write_json(out_dir / "match.json", data)
    with (out_dir / "players.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        cols = [
            "team",
            "name",
            "hero",
            "lane",
            "level",
            "kills",
            "deaths",
            "assists",
            "kda",
            "netWorth",
            "gpm",
            "lastHits",
            "denies",
            "heroDamage",
            "damageTaken",
            "objDamage",
            "healing",
            "selfHealing",
            "accuracyHero",
            "headshotRate",
            "parrySuccess",
            "steamid64",
            "accountId",
            "slot",
        ]
        w = csv_writer(fh)
        w.writerow(cols)
        for p in players:
            w.writerow([p[c] for c in cols])
    with (out_dir / "items.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv_writer(fh)
        w.writerow(
            ["player", "hero", "time", "item", "item_en", "slot", "tier", "sold"]
        )
        for p in players:
            for it in p["items"]:
                w.writerow(
                    [
                        p["name"],
                        p["hero"],
                        it["time"],
                        it["name"],
                        it["en"],
                        it["slot"],
                        it["tier"],
                        it["sold"] or "",
                    ]
                )
    with (out_dir / "deaths.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv_writer(fh)
        w.writerow(
            [
                "time",
                "victim",
                "victimHero",
                "killer",
                "killerHero",
                "x",
                "y",
                "respawn_s",
            ]
        )
        for p in players:
            for d in p["deathDetails"]:
                pos = d.get("pos") or {}
                w.writerow(
                    [
                        d["time"],
                        p["name"],
                        p["hero"],
                        d["killer"],
                        d["killerHero"] or "",
                        round(pos.get("x", 0)),
                        round(pos.get("y", 0)),
                        d["respawn_s"],
                    ]
                )
    with (out_dir / "chat.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv_writer(fh)
        w.writerow(["time", "player", "hero", "allChat", "text"])
        for c in chatout:
            w.writerow([c["time"], c["player"], c["hero"], c["all"], c["text"]])

    write_gold(out_dir / "gold.csv", players)
    return {
        **{k: match[k] for k in ("matchId", "startUTC", "duration", "winner")},
        "heroes": [{k: p[k] for k in ("hero", "team", "name")} for p in players],
        "score": {
            "amber": match["teamTotals"][TEAM["k_ECitadelLobbyTeam_Team0"]]["kills"],
            "sapphire": match["teamTotals"][TEAM["k_ECitadelLobbyTeam_Team1"]]["kills"],
        },
    }
