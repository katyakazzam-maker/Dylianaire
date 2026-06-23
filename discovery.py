"""
discovery.py
Core logic:
  - Last.fm provides BOTH the similarity engine (artist.getSimilar) AND the
    audience stats (artist.getInfo -> listeners/playcount). Spotify has
    restricted genres, followers, and popularity fields on artist objects to
    apps with "Extended Quota Mode" approval — unapproved apps (like this one)
    get back only bare identity info (name, id, link), nothing else.
  - Spotify is used only to grab the artist's public profile link.
"""

import os
import time
import csv
import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

SEED_ARTISTS = [
    "Sailorr",
    "KWN",
    "Brandy",
    "Kehlani",
    "Jordan Ward",
    "Gabriel Jacoby",
    "3ee",
    "Isaiah Falls",
    "Brent Faiyaz",
]

MAX_LISTENERS = 150000     # Last.fm listener count ceiling — lower = more underground
MIN_LISTENERS = 200         # filter out near-zero-presence accounts
SIMILAR_PER_SEED = 30
MAX_TO_SCORE = 80           # cap total candidates processed per run

DATA_PATH = os.path.join(os.path.dirname(__file__), "candidate_artists.csv")
LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"


def get_spotify_client():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET environment variables."
        )
    auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    return spotipy.Spotify(client_credentials_manager=auth)


def get_lastfm_key():
    key = os.environ.get("LASTFM_API_KEY")
    if not key:
        raise RuntimeError("Missing LASTFM_API_KEY environment variable.")
    return key


def lastfm_similar_artists(artist_name, api_key, limit=SIMILAR_PER_SEED):
    params = {
        "method": "artist.getSimilar",
        "artist": artist_name,
        "api_key": api_key,
        "format": "json",
        "limit": limit,
    }
    try:
        resp = requests.get(LASTFM_API_URL, params=params, timeout=10)
        data = resp.json()
        matches = data.get("similarartists", {}).get("artist", [])
        return [(m["name"], float(m.get("match", 0))) for m in matches]
    except Exception as e:
        print(f"  WARNING: Last.fm similar-artist lookup failed for '{artist_name}': {e}")
        return []


def lastfm_artist_info(artist_name, api_key):
    """Returns (listeners, playcount, top_tags) or None if lookup fails."""
    params = {
        "method": "artist.getInfo",
        "artist": artist_name,
        "api_key": api_key,
        "format": "json",
    }
    try:
        resp = requests.get(LASTFM_API_URL, params=params, timeout=10)
        data = resp.json()
        artist = data.get("artist")
        if not artist:
            return None
        stats = artist.get("stats", {})
        listeners = int(stats.get("listeners", 0))
        playcount = int(stats.get("playcount", 0))
        tags = [t["name"] for t in artist.get("tags", {}).get("tag", [])]
        return listeners, playcount, tags
    except Exception as e:
        print(f"  WARNING: Last.fm artist info failed for '{artist_name}': {e}")
        return None


def find_spotify_link(sp, name):
    """Best-effort: just grab the Spotify profile URL, nothing else."""
    try:
        results = sp.search(q=name, type="artist", limit=5)
        items = results["artists"]["items"]
    except Exception:
        return ""
    if not items:
        return ""
    for item in items:
        if item["name"].lower() == name.lower():
            return item.get("external_urls", {}).get("spotify", "")
    return items[0].get("external_urls", {}).get("spotify", "")


def run_discovery():
    sp = get_spotify_client()
    lastfm_key = get_lastfm_key()

    print("Gathering similar artists from Last.fm...")
    candidate_scores = {}
    candidate_seed_count = {}

    for seed in SEED_ARTISTS:
        similar = lastfm_similar_artists(seed, lastfm_key)
        print(f"  {seed}: {len(similar)} similar artists found")
        for name, match_score in similar:
            if name.lower() in (s.lower() for s in SEED_ARTISTS):
                continue
            candidate_scores[name] = max(candidate_scores.get(name, 0), match_score)
            candidate_seed_count[name] = candidate_seed_count.get(name, 0) + 1
        time.sleep(0.2)

    print(f"\n{len(candidate_scores)} unique candidates found.")

    ranked_names = sorted(
        candidate_scores.keys(),
        key=lambda n: (-candidate_seed_count[n], -candidate_scores[n])
    )[:MAX_TO_SCORE]

    print(f"Pulling Last.fm stats for top {len(ranked_names)} candidates...")

    scored = []
    skipped = 0
    for name in ranked_names:
        try:
            info = lastfm_artist_info(name, lastfm_key)
            if not info:
                skipped += 1
                continue
            listeners, playcount, tags = info

            if MIN_LISTENERS <= listeners <= MAX_LISTENERS:
                spotify_url = find_spotify_link(sp, name)
                scored.append({
                    "name": name,
                    "lastfm_match_score": round(candidate_scores[name], 3),
                    "seed_overlap_count": candidate_seed_count[name],
                    "tags": ", ".join(tags[:5]),
                    "listeners": listeners,
                    "playcount": playcount,
                    "spotify_url": spotify_url,
                    "email_found": "",
                    "approved": "",
                })
        except Exception as e:
            skipped += 1
            print(f"  WARNING: Skipping '{name}' due to error: {e}")
        time.sleep(0.15)

    if skipped:
        print(f"Skipped {skipped} candidates due to missing Last.fm data or errors.")

    scored.sort(key=lambda x: (-x["seed_overlap_count"], -x["lastfm_match_score"], x["listeners"]))

    with open(DATA_PATH, "w", newline="") as f:
        fieldnames = ["name", "lastfm_match_score", "seed_overlap_count", "tags",
                      "listeners", "playcount", "spotify_url", "email_found", "approved"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in scored:
            writer.writerow(row)

    print(f"Done. {len(scored)} candidates written.")
    return scored


def load_candidates():
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, newline="") as f:
        return list(csv.DictReader(f))
