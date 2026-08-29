#!/usr/bin/env python3
"""
Fetch live DraftKings CFB data and write:
  - data/drafttable.csv   (full rich columns for Google Sheets)
  - data/dk_salaries.csv  (simplified hub file)
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.draftkings.com/lobby#/CFB",
    "Origin": "https://www.draftkings.com",
}

DRAFTTABLE_FIELDS = [
    "Player Name - Slate Type",
    "Contest IDs",
    "Player ID",
    "Draftable ID",
    "Player Name",
    "First Name",
    "Last Name",
    "Salary",
    "Position",
    "Team",
    "Game",
    "Game Start Time",
    "Player Image",
    "Tournament",
    "Slate Type",
    "Game Type",
    "Date",
    "Role",
    "Contest Names",
    "Contest IDs (Full)",
    "Slate Header",
]

DK_SALARIES_FIELDS = [
    "player_name",
    "salary",
    "position",
    "team",
    "game",
    "slate_type",
    "player_image",
    "player_id",
    "draftable_id",
    "game_start",
    "updated_at",
]


def norm_name(name: str) -> str:
    if not name:
        return ""
    s = str(name).strip()
    if "," in s and not s.lower().startswith("de "):
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            s = f"{parts[1]} {parts[0]}"
    return re.sub(r"\s+", " ", s).strip()


def format_datetime(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%m/%d/%Y %I:%M %p")
    except Exception:
        return iso_str


def format_date_only(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%-m/%-d")
    except Exception:
        return ""


def format_time_only(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%-I:%M%p")
    except Exception:
        return ""


def format_day(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%a")
    except Exception:
        return ""


def classify_contest(name: str) -> str:
    n = (name or "").lower()
    if "madden" in n or "best ball" in n:
        return "SKIP"
    if "showdown" in n and ("late" in n or "2h" in n or "in-game" in n or "2nd half" in n):
        return "2nd Half Showdown Captain Mode" if ("2h" in n or "2nd half" in n or "in-game" in n) else "Late Showdown Captain Mode"
    if "showdown" in n:
        return "Showdown Captain Mode"
    if "turbo" in n:
        return "Turbo"
    if "snake" in n:
        return "Snake"
    if "single stat" in n:
        return "Single Stat"
    if "thursday" in n:
        return "Thursday"
    if "night" in n:
        return "Night"
    if "late" in n:
        return "Late"
    if "early" in n:
        return "Early"
    return "Classic"


def classify_group(contest_names: list[str]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for name in contest_names:
        st = classify_contest(name)
        if st != "SKIP":
            counts[st] += 1
    if not counts:
        return "Classic"
    priority = [
        "2nd Half Showdown Captain Mode",
        "Late Showdown Captain Mode",
        "Showdown Captain Mode",
        "Thursday",
        "Night",
        "Late",
        "Early",
        "Turbo",
        "Snake",
        "Single Stat",
        "Classic",
    ]
    for p in priority:
        if counts.get(p, 0) > 0:
            return p
    return max(counts.items(), key=lambda kv: kv[1])[0]


def main() -> None:
    print("Fetching DraftKings CFB contests…")
    contest_url = "https://www.draftkings.com/lobby/getcontests?sport=CFB"

    try:
        r = requests.get(contest_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"Failed to fetch contests: {e}")
        return

    contests = data.get("Contests", [])
    if not contests:
        print("No contests found")
        return

    draft_groups: dict[str, dict] = {}
    for c in contests:
        cname = c.get("n") or c.get("Name") or ""
        if classify_contest(cname) == "SKIP":
            continue
        dg = str(c.get("dg") or c.get("DraftGroupId") or "")
        cid = str(c.get("id") or c.get("ContestId") or "")
        if not dg or not cid:
            continue
        if dg not in draft_groups:
            draft_groups[dg] = {"contest_ids": [], "contest_names": []}
        draft_groups[dg]["contest_ids"].append(cid)
        draft_groups[dg]["contest_names"].append(cname)

    for group in draft_groups.values():
        group["slate_type"] = classify_group(group["contest_names"])

    print(f"Found {len(draft_groups)} draft groups")
    for dg_id, g in draft_groups.items():
        print(f"  DG {dg_id}: {g['slate_type']} ({len(g['contest_ids'])} contests)")

    rich_rows = []
    simple_rows = []
    seen_simple = set()

    for dg_id, group in draft_groups.items():
        slate_type = group["slate_type"]
        print(f"  Fetching draftables for DG {dg_id} ({slate_type})…")
        url = f"https://api.draftkings.com/draftgroups/v1/draftgroups/{dg_id}/draftables?format=json"

        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                print(f"    Failed {dg_id}: {r.status_code}")
                continue
            salary_data = r.json()
        except Exception as e:
            print(f"    Error {dg_id}: {e}")
            continue

        draftables = salary_data.get("draftables", [])
        if not draftables:
            continue

        comps = {}
        start_times = []
        for p in draftables:
            comp = p.get("competition") or {}
            cid = str(comp.get("competitionId") or "")
            if cid and cid not in comps:
                home = (
                    (comp.get("homeTeam") or {}).get("abbreviation")
                    or comp.get("homeTeamAbbreviation")
                    or ""
                )
                away = (
                    (comp.get("awayTeam") or {}).get("abbreviation")
                    or comp.get("awayTeamAbbreviation")
                    or ""
                )
                start = comp.get("startTime") or ""
                matchup = f"{away} @ {home}".strip(" @") or (comp.get("name") or "")
                comps[cid] = {"matchup": matchup, "name": comp.get("name") or matchup, "startTime": start}
                if start:
                    try:
                        start_times.append(datetime.fromisoformat(start.replace("Z", "+00:00")))
                    except Exception:
                        pass

        num_games = len(comps)
        # Single-game slates are Showdown even if the contest name omitted it
        if num_games == 1 and "Showdown" not in slate_type and slate_type not in ("Snake", "Single Stat"):
            slate_type = "Showdown Captain Mode"
            group["slate_type"] = slate_type

        names_lower = " ".join(group["contest_names"]).lower()
        if ("in-game" in names_lower or "2h" in names_lower or "2nd half" in names_lower) and "Showdown" in slate_type:
            slate_type = "2nd Half Showdown Captain Mode"
            group["slate_type"] = slate_type

        slate_header = slate_type
        min_start = None
        if start_times:
            min_start = min(start_times)
            max_start = max(start_times)
            date_part = min_start.astimezone(ZoneInfo("America/New_York")).strftime("%-m/%-d")
            time_part = min_start.astimezone(ZoneInfo("America/New_York")).strftime("%-I:%M%p")
            days_range = ""
            if min_start.date() != max_start.date():
                days_range = f" ({min_start.astimezone(ZoneInfo('America/New_York')).strftime('%a')}-{max_start.astimezone(ZoneInfo('America/New_York')).strftime('%a')})"
            if num_games == 1:
                matchup = list(comps.values())[0]["matchup"]
                slate_header = f"{date_part} {time_part}{days_range} ({matchup})"
            else:
                slate_header = f"{date_part} {time_part}{days_range}, {num_games} Games"

        player_versions = defaultdict(list)
        for p in draftables:
            player_id = str(p.get("playerId") or "")
            draftable_id = str(p.get("draftableId") or "")
            name = p.get("displayName") or "Unknown"
            first = p.get("firstName") or ""
            last = p.get("lastName") or ""
            salary = p.get("salary") or 0
            pos = p.get("position") or ""
            team = p.get("teamAbbreviation") or p.get("team") or ""
            image = p.get("playerImage50") or p.get("imageUrl") or ""
            comp = p.get("competition") or {}
            comp_id = str(comp.get("competitionId") or "")
            game = comps.get(comp_id, {}).get("matchup", "")
            start = comps.get(comp_id, {}).get("startTime", "")
            tournament = comps.get(comp_id, {}).get("name", "") or game

            if salary <= 0 or not player_id or not draftable_id or name == "Unknown":
                continue

            player_versions[player_id].append({
                "draftable_id": draftable_id,
                "name": name,
                "first": first,
                "last": last,
                "salary": salary,
                "pos": pos,
                "team": team,
                "image": image,
                "game": game,
                "start": start,
                "tournament": tournament,
                "date": format_date_only(start),
            })

        is_showdown = "Showdown" in slate_type

        for player_id, versions in player_versions.items():
            versions.sort(key=lambda x: x["salary"], reverse=True)

            if is_showdown and len(versions) >= 2:
                pairs = [("Captain", versions[0]), ("Flex", versions[1])]
            else:
                seen = {}
                for v in versions:
                    key = (v["salary"], v["pos"], v["team"], v["name"], v["game"])
                    if key not in seen or int(v["draftable_id"]) < int(seen[key]["draftable_id"]):
                        seen[key] = v
                pairs = [("Standard", v) for v in seen.values()]

            for role, v in pairs:
                pos = "CPT" if role == "Captain" else v["pos"]
                rich_rows.append({
                    "Player Name - Slate Type": f"{v['name']} - {slate_type}" + (f" ({role})" if role in ("Captain", "Flex") else ""),
                    "Contest IDs": ";".join(group["contest_ids"][:20]),
                    "Player ID": player_id,
                    "Draftable ID": v["draftable_id"],
                    "Player Name": v["name"],
                    "First Name": v["first"],
                    "Last Name": v["last"],
                    "Salary": v["salary"],
                    "Position": pos,
                    "Team": v["team"],
                    "Game": v["game"],
                    "Game Start Time": format_datetime(v["start"]),
                    "Player Image": v["image"],
                    "Tournament": v["tournament"],
                    "Slate Type": slate_type,
                    "Game Type": "CFB",
                    "Date": v["date"],
                    "Role": role,
                    "Contest Names": ";".join(group["contest_names"][:10]),
                    "Contest IDs (Full)": ";".join(group["contest_ids"][:20]),
                    "Slate Header": slate_header,
                })

                if role != "Captain" and slate_type not in ("Snake", "Single Stat"):
                    skey = (norm_name(v["name"]).lower(), slate_type, v["game"], pos)
                    if skey not in seen_simple:
                        seen_simple.add(skey)
                        simple_rows.append({
                            "player_name": norm_name(v["name"]),
                            "salary": int(v["salary"]),
                            "position": pos,
                            "team": v["team"],
                            "game": v["game"],
                            "slate_type": slate_type,
                            "player_image": v["image"],
                            "player_id": player_id,
                            "draftable_id": v["draftable_id"],
                            "game_start": format_datetime(v["start"]),
                            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        })

    if not rich_rows:
        print("No player rows generated")
        return

    rich_rows.sort(key=lambda x: int(x["Salary"]) if str(x["Salary"]).isdigit() else 0, reverse=True)
    simple_rows.sort(key=lambda x: x["salary"], reverse=True)

    drafttable_path = DATA_DIR / "drafttable.csv"
    with drafttable_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DRAFTTABLE_FIELDS)
        writer.writeheader()
        writer.writerows(rich_rows)
    print(f"Wrote {len(rich_rows)} rows → {drafttable_path}")

    salaries_path = DATA_DIR / "dk_salaries.csv"
    with salaries_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DK_SALARIES_FIELDS)
        writer.writeheader()
        writer.writerows(simple_rows)
    print(f"Wrote {len(simple_rows)} rows → {salaries_path}")


if __name__ == "__main__":
    main()
