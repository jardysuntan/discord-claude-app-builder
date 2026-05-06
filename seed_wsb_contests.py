#!/usr/bin/env python3
"""One-shot: pre-seed the 9 bachelor party contests into a WSB trip.

Idempotent — skips contests already present (matched by title within the trip).
"""
import json
import os
import subprocess
import sys
import urllib.request

PROJECT_REF = "ajhkqssxpdjqnasgoxqq"
TRIP_ID = "b0c317e1-4e69-45f2-a51e-8e17b085c6f1"
PREFIX = TRIP_ID.split("-")[0]  # 8-char scope prefix per ImportCommitter convention

CONTESTS = [
    ("smash-bros", "Smash Bros tournament", "smash_bros", False,
     "Single elimination bracket. Standard items, no FS."),
    ("popsicle-relay", "Popsicle relay", "popsicle_relay", True,
     "Team relay — pass the popsicle without using your hands."),
    ("yangzi-jeopardy", "Yangzi Jeopardy", "yangzi_jeopardy", False,
     "Trivia game about the groom's life."),
    ("pokemon-tcg", "Pokémon card pack opening battle", "custom", False,
     "11 packs each, 20-card decks, TCG Pocket rules, energy zone, single elimination bracket."),
    ("beer-die", "Beer die tournament", "beer_die", True,
     "Standard rules. Bracket play."),
    ("beer-ball", "Beer ball", "beer_ball", True,
     "Open challenge format."),
    ("drinking-games", "Drinking games", "custom", False,
     "Beer pong, flip cup, etc. — rotating throughout the trip."),
    ("golf-games", "Golf games", "golf_skins", False,
     "Scramble, wolf, skins. Albert organizing per round."),
    ("olympics-events", "Olympics-style games", "olympics_event", True,
     "Relay races, competitions — schedule TBD."),
]


def run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out.stdout


def fetch_service_role() -> str:
    env_path = "/Users/jaredtanpersonal/bots/discord-claude-bridge/.env"
    mgmt_key = ""
    with open(env_path) as f:
        for line in f:
            if line.startswith("SUPABASE_MANAGEMENT_KEY="):
                mgmt_key = line.strip().split("=", 1)[1]
                break
    if not mgmt_key:
        sys.exit("SUPABASE_MANAGEMENT_KEY not in .env")
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/api-keys",
        headers={
            "Authorization": f"Bearer {mgmt_key}",
            "User-Agent": "wsb-seed-script",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        keys = json.load(r)
    for k in keys:
        if k["name"] == "service_role":
            return k["api_key"]
    sys.exit("service_role key not found")


def rest_get(path: str, svc: str) -> list:
    url = f"https://{PROJECT_REF}.supabase.co/rest/v1/{path}"
    req = urllib.request.Request(
        url,
        headers={"apikey": svc, "Authorization": f"Bearer {svc}"},
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def rest_post(path: str, body: dict, svc: str):
    url = f"https://{PROJECT_REF}.supabase.co/rest/v1/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "apikey": svc,
            "Authorization": f"Bearer {svc}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main() -> None:
    svc = fetch_service_role()
    existing = rest_get(
        f'contests?select=id,title&"tripId"=eq.{TRIP_ID}',
        svc,
    )
    have = {c["title"].lower() for c in existing}
    skipped = 0
    inserted = 0
    for slug, title, game_type, is_team, description in CONTESTS:
        if title.lower() in have:
            print(f"  · skip (exists): {title}")
            skipped += 1
            continue
        body = {
            "id": f"{PREFIX}-contest-{slug}",
            "tripId": TRIP_ID,
            "title": title,
            "description": description,
            "type": "team" if is_team else "solo",
            "status": "active",
            "gameType": game_type,
            "isTeamGame": is_team,
            "topNAwarded": 3,
            "punishLast": False,
        }
        rest_post("contests", body, svc)
        print(f"  + inserted: {title}")
        inserted += 1
    print(f"\nDone. inserted={inserted} skipped={skipped} total={len(CONTESTS)}")


if __name__ == "__main__":
    main()
