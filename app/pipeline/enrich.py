"""Deterministic report data derived only from saved match artifacts."""

from collections import Counter


FANTASY = {
    "kills": 0.3,
    "assists": 0.15,
    "deathsBase": 3.0,
    "deaths": -0.3,
    "lastHits": 0.003,
    "denies": 0.003,
    "gpm": 0.002,
    "heroDamage": 0.05 / 1000,
    "healing": 0.05 / 1000,
    "objDamage": 0.1 / 1000,
    "participation": 3.0,
    "midboss": 1.0,
    "lane": {"won": 2.0, "even": 0.0, "lost": -1.0},
}
SOURCES = ("Players", "Assists", "LaneCreeps", "Neutrals", "Bosses", "Treasure", "Denies")


def enrich(match: dict, postmatch_raw: dict) -> dict:
    """Add/replace derived fields in match, without I/O or modifying postmatch."""
    postmatch = postmatch_raw
    players = match.get("players", [])
    raw = {str(p.get("playerSlot", 0)): p for p in postmatch.get("players", [])}
    names = {str(c.get("id", 0)): c.get("name", "") for c in postmatch.get("customUserStats", [])}
    info = match.setdefault("match", {})
    team = lambda p: p.get("teamKey", p.get("team"))
    kills = Counter()
    lanes = {}
    snapshots = {}
    for p in players:
        slot = str(p["slot"])
        lane = p.get("lane", 0)
        lanes[slot] = int(raw.get(slot, {}).get("assignedLane", lane.get("id", 0) if isinstance(lane, dict) else lane))
        points = match.get("playerTimeline", {}).get(slot, [])
        eligible = [point for point in points if point["t"] <= 600]
        snapshots[slot] = max(eligible, key=lambda point: point["t"]) if eligible else {"t": 0, "netWorth": 0}
        kills[team(p)] += p.get("kills", 0)

    lane_nw = Counter()
    for p in players:
        slot = str(p["slot"])
        lane_nw[lanes[slot], team(p)] += snapshots[slot].get("netWorth", 0)
    rates = []
    for p in players:
        slot = str(p["slot"])
        lane_id = lanes[slot]
        own = lane_nw[lane_id, team(p)]
        enemy = sum(nw for (lane, side), nw in lane_nw.items() if lane == lane_id and side != team(p))
        # Compare against the opposing lane total, as specified.
        diff = 100 * (own - enemy) / max(1, enemy)
        result = "won" if diff > 15 else "lost" if diff < -15 else "even"
        p["lane"] = {"id": lane_id, "result": result, "diffPct": round(diff, 2),
                     "teamNW": int(own), "enemyNW": int(enemy), "at": int(snapshots[slot]["t"])}
        p["hsTimeline"] = []
        p["sourceTimeline"] = []
        stats = sorted(raw.get(slot, {}).get("stats", []), key=lambda st: st.get("timeStampS", 0))
        for index, st in enumerate(stats):
            t = st.get("timeStampS", 0)
            custom = {names.get(str(c.get("id", 0))): c.get("value", 0) for c in st.get("customUserStats", [])}
            shots, hits, headshots = (custom.get("Enemy Hero Accuracy##" + name, 0) for name in ("Shots", "Hits", "Headshots"))
            p["hsTimeline"].append({"t": t, "shots": shots, "hits": hits, "headshots": headshots,
                                    "hsRate": 100 * headshots / hits if hits else None,
                                    "accuracy": 100 * hits / shots if shots else None})
            gold = {source: 0 for source in SOURCES}
            orbs = 0
            for source in st.get("goldSources", []):
                name = source.get("source", "k_eOther").removeprefix("k_e")
                if name in gold:
                    gold[name] += source.get("gold", 0)
                orbs += source.get("goldOrbs", 0)
            # Final player totals can include income after the last stats sample.
            nw = p.get("netWorth", st.get("netWorth", 0)) if index == len(stats) - 1 else st.get("netWorth", 0)
            p["sourceTimeline"].append({"t": t, **gold, "Orbs": orbs,
                                        "Passive": max(0, nw - sum(gold.values()) - orbs)})
        rate = p["hsTimeline"][-1]["hsRate"] if p["hsTimeline"] else p.get("headshotRate")
        if p["hsTimeline"]:
            final = p["hsTimeline"][-1]
            p["heroShots"] = final["shots"]
            p["heroHits"] = final["hits"]
            p["heroHeadshots"] = final["headshots"]
            p["headshotRate"] = round(rate, 1) if rate is not None else None
        if rate is not None:
            rates.append(rate)
        parts = {key: p.get(key, 0) * FANTASY[key] for key in
                 ("kills", "assists", "lastHits", "denies", "gpm", "heroDamage", "objDamage")}
        parts["deaths"] = max(0, FANTASY["deathsBase"] + p.get("deaths", 0) * FANTASY["deaths"])
        healing = p.get("heroHealing", p.get("healing", 0))
        parts["healing"] = max(0, healing - p.get("selfHealing", 0)) * FANTASY["healing"]
        parts["participation"] = ((p.get("kills", 0) + p.get("assists", 0)) / kills[team(p)] * FANTASY["participation"] if kills[team(p)] else 0.0)
        parts["midboss"] = sum(b.get("claimedBy") in {team(p), p.get("team", team(p))} for b in info.get("midBoss", [])) * FANTASY["midboss"]
        parts["lane"] = FANTASY["lane"][result]
        parts = {key: round(float(value), 2) for key, value in parts.items()}
        p["fantasy"] = {"total": round(sum(parts.values()), 2), "parts": parts}
    for rank, p in enumerate(sorted(players, key=lambda p: (-p["fantasy"]["total"], p["slot"])), 1):
        p["rank"] = rank
        p["grade"] = "S" if rank <= 2 else "A" if rank <= 4 else "B" if rank <= 7 else "C" if rank <= 10 else "D"
        p["mvp"] = rank == 1
        if rank == 1:
            info["mvpSlot"] = p["slot"]
    if not players:
        info["mvpSlot"] = None
    info["hsAverage"] = sum(rates) / len(rates) if rates else None
    return match
