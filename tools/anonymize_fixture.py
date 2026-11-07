"""Anonymize the bundled match fixture in place; safe to run repeatedly."""
import gzip
import json
import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/proto"))
sys.path.insert(0, str(ROOT / "app/pipeline/_vendor"))
import citadel_usermessages_pb2 as um
import citadel_gcmessages_common_pb2 as gc

FIXTURE = ROOT / "tests/fixtures"
BASE = FIXTURE / "133162474"


def rewrite_gzip(path, transform):
    with gzip.open(path, "rt", encoding="utf-8") as src:
        lines = list(transform(src))
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as dst:
            for line in lines:
                dst.write(line.encode("utf-8"))


def entities(lines):
    for line in lines:
        obj = json.loads(line)
        fields = obj["f"]
        if obj["cls"] == "CCitadelPlayerController":
            slot = int(fields.get("m_unLobbyPlayerSlot", -1))
            if slot in range(1, 13):
                if "m_iszPlayerName" in fields:
                    fields["m_iszPlayerName"] = f"Игрок {slot}"
                if "m_steamID" in fields:
                    fields["m_steamID"] = str(76561197960265728 + 100000000 + slot)
                if "m_unEconAccountID" in fields:
                    fields["m_unEconAccountID"] = str(100000000 + slot)
        yield json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def messages(lines):
    count = 0
    for line in lines:
        tick, kind, payload = line.rstrip("\n").split("\t")
        if kind == "316":
            msg = um.CCitadelUserMsg_PostMatchDetails.FromString(bytes.fromhex(payload))
            meta = gc.CMsgMatchMetaDataContents.FromString(msg.match_details)
            for player in meta.match_info.players:
                player.account_id = 100000000 + player.player_slot
            msg.match_details = meta.SerializeToString()
            payload = msg.SerializeToString().hex()
        elif kind == "314":
            count += 1
            msg = um.CCitadelUserMsg_ChatMsg.FromString(bytes.fromhex(payload))
            msg.text = f"сообщение {count}"
            payload = msg.SerializeToString().hex()
        yield f"{tick}\t{kind}\t{payload}\n"


if __name__ == "__main__":
    rewrite_gzip(BASE.with_suffix(".ents.jsonl.gz"), entities)
    rewrite_gzip(BASE.with_suffix(".msgs.tsv.gz"), messages)


    # Rebuild every expected section from the sanitized fixture.
    sys.path.insert(0, str(ROOT))
    from app.pipeline.process import process
    with tempfile.TemporaryDirectory() as temp:
        raw = Path(temp)
        for name in ("ents.jsonl", "msgs.tsv"):
            with gzip.open(FIXTURE / f"133162474.{name}.gz", "rb") as src, (raw / name).open("wb") as dst:
                shutil.copyfileobj(src, dst)
        header = json.loads((FIXTURE / "133162474.header.json").read_text(encoding="utf-8"))
        process(raw, header, raw / "out")
        data = json.loads((raw / "out/match.json").read_text(encoding="utf-8"))
        expected_path = FIXTURE / "133162474.expected.json"
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        for key in expected:
            if key in data:
                expected[key] = data[key]
        expected["players"] = [{k: v for k, v in p.items() if k != "gpm"} for p in data["players"]]
        expected_path.write_text(json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
