"""
discovery.py
Core logic: pulls seed artists, derives their genres, then finds candidates via:
  1. Genre-based search (Spotify's related-artists endpoint is restricted to
     approved partners only, so we don't use it)
  2. Playlist mining: finds public playlists containing seed artists, then
     pulls every other artist featured on those playlists

Candidates are scored by genre overlap + how many seed-playlists they
co-occur in, then filtered by popularity/follower thresholds.
"""

import os
import time
import csv
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

MAX_POPULARITY = 55
MIN_FOLLOWERS = 500
PLAYLISTS_PER_SEED = 5       # how many playlists to check per seed artist
MAX_TRACKS_PER_PLAYLIST = 100

DATA_PATH = os.path.join(os.path.dirname(__file__), "candidate_artists.csv")


def get_spotify_client():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET environment variables."
        )
    auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    return spotipy.Spotify(client_credentials_manager=auth)


def find_artist(sp, name):
    results = sp.search(q=name, type="artist", limit=5)
    items = results["artists"]["items"]
    if not items:
        return None
    for item in items:
        if item["name"].lower() == name.lower():
            return item
    return items[0]


def search_by_genre(sp, genres, limit_per_genre=25):
    """Search Spotify directly for artists tagged with seed genres."""
    candidates = {}
    for genre in genres:
        try:
            query = f'genre:"{genre}"'
            results = sp.search(q=query, type="artist", limit=limit_per_genre)
            for art in results["artists"]["items"]:
                candidates[art["id"]] = art
        except Exception as e:
            print(f"  ⚠ genre search failed for '{genre}': {e}")
        time.sleep(0.1)
    return candidates


def mine_playlists(sp, seed_names, candidates_accum, co_occurrence):
    """Find playlists featuring seed artists, then pull other artists from them."""
    for name in seed_names:
        try:
            results = sp.search(q=name, type="playlist", limit=PLAYLISTS_PER_SEED)
            playlists = results.get("playlists", {}).get("items", [])
        except Exception as e:
            print(f"  ⚠ playlist search failed for '{name}': {e}")
            continue

        for pl in playlists:
            if not pl:
                continue
            try:
                tracks = sp.playlist_tracks(pl["id"], limit=MAX_TRACKS_PER_PLAYLIST)
            except Exception:
                continue
            for item in tracks.get("items", []):
                track = item.get("track")
                if not track:
                    continue
                for art in track.get("artists", []):
                    aid = art["id"]
                    co_occurrence[aid] = co_occurrence.get(aid, 0) + 1
                    if aid not in candidates_accum:
                        candidates_accum[aid] = art  # partial object, hydrated later
            time.sleep(0.1)


def hydrate_artists(sp, artist_ids):
    """Spotify search/playlist results don't include genres/followers —
    fetch full artist objects in batches of 50."""
    full = {}
    ids = list(artist_ids)
    for i in range(0, len(ids), 50):
        batch = ids[i:i+50]
        try:
            result = sp.artists(batch)
            for art in result["artists"]:
                if art:
                    full[art["id"]] = art
        except Exception as e:
            print(f"  ⚠ hydrate batch failed: {e}")
        time.sleep(0.1)
    return full


def score_candidate(artist, seed_genres, co_occurrence_count):
    genres = set(g.lower() for g in artist.get("genres", []))
    overlap = len(genres & seed_genres)
    followers = artist["followers"]["total"]
    popularity = artist["popularity"]
    return overlap, followers, popularity, co_occurrence_count


def run_discovery():
    sp = get_spotify_client()

    print("Looking up seed artists...")
    seed_objs = []
    for name in SEED_ARTISTS:
        art = find_artist(sp, name)
        if art:
            seed_objs.append(art)

    seed_ids = set(a["id"] for a in seed_objs)
    seed_genres = set()
    for a in seed_objs:
        seed_genres |= set(g.lower() for g in a.get("genres", []))

    print(f"Seed genre pool: {seed_genres}")

    print("Searching by genre...")
    genre_candidates = search_by_genre(sp, seed_genres) if seed_genres else {}

    print("Mining playlists for co-occurring artists...")
    co_occurrence = {}
    playlist_candidates = {}
    mine_playlists(sp, SEED_ARTISTS, playlist_candidates, co_occurrence)

    # Merge candidate ID pools, hydrate playlist-derived ones (genre search already full objects)
    all_ids = set(genre_candidates.keys()) | set(playlist_candidates.keys())
    all_ids -= seed_ids  # don't recommend the seeds themselves

    print(f"Hydrating {len(all_ids)} total candidates...")
    hydrated = dict(genre_candidates)
    needs_hydration = [aid for aid in all_ids if aid not in hydrated]
    hydrated.update(hydrate_artists(sp, needs_hydration))

    print("Scoring candidates...")
    scored = []
    for aid in all_ids:
        art = hydrated.get(aid)
        if not art:
            continue
        co_count = co_occurrence.get(aid, 0)
        overlap, followers, popularity, co_count = score_candidate(art, seed_genres, co_count)

        if popularity <= MAX_POPULARITY and followers >= MIN_FOLLOWERS:
            scored.append({
                "name": art["name"],
                "genre_overlap": overlap,
                "playlist_co_occurrence": co_count,
                "genres": ", ".join(art.get("genres", [])),
                "followers": followers,
                "popularity": popularity,
                "spotify_url": art["external_urls"]["spotify"],
                "email_found": "",
                "approved": "",
            })

    # Rank: prioritize playlist co-occurrence (real curation signal), then genre overlap, then underground-ness
    scored.sort(key=lambda x: (-x["playlist_co_occurrence"], -x["genre_overlap"], x["popularity"]))

    with open(DATA_PATH, "w", newline="") as f:
        fieldnames = ["name", "genre_overlap", "playlist_co_occurrence", "genres",
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
