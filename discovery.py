"""
discovery.py
Core logic:
  - Last.fm provides the similarity engine (artist.getSimilar, artist.getTopTags) —
    Spotify restricted related-artists and genre tags to approved partners only,
    so Last.fm's public, unrestricted API does this job instead.
  - Spotify is used only for metadata that still works for any app: search,
    follower counts, popularity score, and the artist's Spotify link.
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

MAX_POPULARITY = 55       # Spotify popularity 0-100, lower = more underground
MIN_FOLLOWERS = 500
SIMILAR_PER_SEED = 30      # how many similar artists to pull per seed from Last.fm

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
        print(f"  ⚠ Last.fm similar-artist lookup failed for '{artist_name}': {e}")
        return []


def find_spotify_artist(sp, name):
    try:
        results = sp.search(q=name, type="artist", limit=5)
        items = results["artists"]["items"]
    except Exception as e:
        print(f"  ⚠ Spotify search failed for '{name}': {type(e).__name__}: {e}")
        return None
    if not items:
        print(f"  ⚠ No Spotify match found for '{name}'")
        return None
    for item in items:
        if item["name"].lower() == name.lower():
            return item
    return items[0]


def run_discovery():
    sp = get_spotify_client()
    lastfm_key = get_lastfm_key()

    print("Gathering similar artists from Last.fm...")
    # candidate_name -> best (highest) match score seen across all seeds
    candidate_scores = {}
    # candidate_name -> count of seeds that surfaced them (a stronger signal than raw match score)
    candidate_seed_count = {}

    for seed in SEED_ARTISTS:
        similar = lastfm_similar_artists(seed, lastfm_key)
        print(f"  {seed}: {len(similar)} similar artists found")
        for name, match_score in similar:
            if name.lower() in (s.lower() for s in SEED_ARTISTS):
                continue  # skip if it's just another seed artist
            candidate_scores[name] = max(candidate_scores.get(name, 0), match_score)
            candidate_seed_count[name] = candidate_seed_count.get(name, 0) + 1
        time.sleep(0.2)

    print(f"\n{len(candidate_scores)} unique candidates found.")

    # Cap how many we hydrate via Spotify — each one is a network call, and too many
    # will blow past the web server's request timeout. Prioritize candidates that
    # showed up across multiple seeds (strongest similarity signal) first.
    MAX_TO_HYDRATE = 60
    ranked_names = sorted(
        candidate_scores.keys(),
        key=lambda n: (-candidate_seed_count[n], -candidate_scores[n])
    )[:MAX_TO_HYDRATE]

    print(f"Hydrating top {len(ranked_names)} via Spotify...")

    scored = []
    skipped = 0
    debug_printed = False
    for name in ranked_names:
        try:
            art = find_spotify_artist(sp, name)
            if not art:
                continue  # not on Spotify, skip

            if not debug_printed:
                print(f"DEBUG raw artist object for '{name}': {art}")
                debug_printed = True

            followers = art.get("followers", {}).get("total")
            popularity = art.get("popularity")

            if followers is None or popularity is None:
                skipped += 1
                continue  # malformed/incomplete result from Spotify, skip safely

            if popularity <= MAX_POPULARITY and followers >= MIN_FOLLOWERS:
                scored.append({
                    "name": art.get("name", name),
                    "lastfm_match_score": round(candidate_scores[name], 3),
                    "seed_overlap_count": candidate_seed_count[name],
                    "genres": ", ".join(art.get("genres", [])),
                    "followers": followers,
                    "popularity": popularity,
                    "spotify_url": art.get("external_urls", {}).get("spotify", ""),
                    "email_found": "",
                    "approved": "",
                })
        except Exception as e:
            skipped += 1
            print(f"  ⚠ Skipping '{name}' due to error: {e}")
        time.sleep(0.15)

    if skipped:
        print(f"Skipped {skipped} candidates due to missing/malformed Spotify data.")

    # Rank: prioritize how many seed artists pointed to this candidate, then raw match strength,
    # then favor lower popularity (more underground / earlier to find)
    scored.sort(key=lambda x: (-x["seed_overlap_count"], -x["lastfm_match_score"], x["popularity"]))

    with open(DATA_PATH, "w", newline="") as f:
        fieldnames = ["name", "lastfm_match_score", "seed_overlap_count", "genres",
                      "followers", "popularity", "spotify_url", "email_found", "approved"]
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
