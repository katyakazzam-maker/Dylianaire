"""
discovery.py
Core logic: pulls seed artists, expands the related-artist graph on Spotify,
scores candidates, and resolves contact info where available.
"""

import os
import re
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

MAX_POPULARITY = 55
MIN_FOLLOWERS = 500
HOPS = 2

DATA_PATH = os.path.join(os.path.dirname(__file__), "candidate_artists.csv")
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


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


def expand_related(sp, seed_ids, hops=HOPS):
    seen = set(seed_ids)
    frontier = set(seed_ids)
    candidates = {}

    for _ in range(hops):
        next_frontier = set()
        for aid in frontier:
            try:
                related = sp.artist_related_artists(aid)["artists"]
            except Exception:
                continue
            for art in related:
                if art["id"] not in seen:
                    candidates[art["id"]] = art
                    next_frontier.add(art["id"])
            time.sleep(0.1)
        seen |= next_frontier
        frontier = next_frontier

    return candidates


def score_candidate(artist, seed_genres):
    genres = set(g.lower() for g in artist.get("genres", []))
    overlap = len(genres & seed_genres)
    followers = artist["followers"]["total"]
    popularity = artist["popularity"]
    return overlap, followers, popularity


def try_find_contact(artist):
    """
    Best-effort contact resolution. Checks Spotify's linked external URLs.
    Instagram/Linktree bio scraping is intentionally NOT automated here —
    those pages require a logged-in session for reliable access and scraping
    them at scale risks violating platform terms of service. Use the
    Spotify-provided link as a manual jumping-off point instead.
    """
    spotify_url = artist["external_urls"].get("spotify", "")
    return {
        "spotify_url": spotify_url,
        "email_found": "",  # left blank by design; fill in manually after a quick check
    }


def run_discovery():
    sp = get_spotify_client()

    seed_objs = []
    for name in SEED_ARTISTS:
        art = find_artist(sp, name)
        if art:
            seed_objs.append(art)

    seed_ids = [a["id"] for a in seed_objs]
    seed_genres = set()
    for a in seed_objs:
        seed_genres |= set(g.lower() for g in a.get("genres", []))

    candidates = expand_related(sp, seed_ids, hops=HOPS)

    scored = []
    for cid, art in candidates.items():
        overlap, followers, popularity = score_candidate(art, seed_genres)
        if popularity <= MAX_POPULARITY and followers >= MIN_FOLLOWERS:
            contact = try_find_contact(art)
            scored.append({
                "name": art["name"],
                "genre_overlap": overlap,
                "genres": ", ".join(art.get("genres", [])),
                "followers": followers,
                "popularity": popularity,
                "spotify_url": contact["spotify_url"],
                "email_found": contact["email_found"],
                "approved": "",
            })

    scored.sort(key=lambda x: (-x["genre_overlap"], x["popularity"]))

    with open(DATA_PATH, "w", newline="") as f:
        fieldnames = ["name", "genre_overlap", "genres", "followers",
                      "popularity", "spotify_url", "email_found", "approved"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in scored:
            writer.writerow(row)

    return scored


def load_candidates():
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, newline="") as f:
        return list(csv.DictReader(f))
