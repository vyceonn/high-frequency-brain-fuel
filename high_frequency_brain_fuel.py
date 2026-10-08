"""
High-Frequency Brain Fuel
-------------------------
Ranks your Spotify "Liked Songs" by a mood-boost score built from audio features
(valence, energy, danceability, tempo, major/minor key) and saves the
qualifying songs to a playlist called "High-Frequency Brain Fuel".

Why two APIs?
  Spotify closed its /audio-features endpoint to apps created after
  27 Nov 2024. So this script:
    1. Uses Spotify (spotipy) to read your Liked Songs and write the playlist.
    2. Uses ReccoBeats (free, no key) to look up the same audio features
       by Spotify track ID.
  It also uses the playlist endpoints Spotify introduced in Feb 2026
  (POST /me/playlists, /playlists/{id}/items), called directly so it does
  not depend on which spotipy version you have installed.

Setup: see README section in the chat, or the SETUP notes at the bottom.
"""

import os
import sys
import time

import requests
import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Where to pull songs from:
#   a playlist name (e.g. "arabe")  -> scans that playlist (must be one YOU own)
#   None                            -> scans your Liked Songs
SOURCE_PLAYLIST = None

# Output playlist. Gets its own name so it doesn't overwrite the Liked Songs one.
PLAYLIST_NAME = (f"High-Frequency Brain Fuel - {SOURCE_PLAYLIST}"
                 if SOURCE_PLAYLIST else "High-Frequency Brain Fuel")
PLAYLIST_DESCRIPTION = (
    f"Every uplifting track from {SOURCE_PLAYLIST or 'my Liked Songs'} that passes "
    "the filters, ranked by positivity, energy, groove, tempo and key."
)

# How many songs to keep:
#   None -> every song that passes all filters (no cap)
#   20   -> top 20 (near-misses fill in if fewer than 20 pass)
TOP_N = None

# --- Mood-boost model, based on music-and-emotion research -----------------
# Every song here is already one you liked, and enjoying a song is the single
# biggest driver of the mood/reward response. These filters then keep the
# ones whose musical features are linked to feeling happy and energized.

# Filter gates: a song must pass all of these to be included
VALENCE_MIN = 0.5                # positive side of the scale (0.5 = neutral)
ENERGY_MIN, ENERGY_MAX = 0.4, 0.9  # lively, but below harsh/abrasive territory

# Ranking weights (sum to 1.0). These only affect ORDER, not who gets in.
W_VALENCE = 0.40   # sounds happy: strongest predictor of perceived happiness
W_ENERGY = 0.20    # moderate-high arousal lifts mood; extremes less so
W_GROOVE = 0.20    # danceability: steady beat that makes you want to move
W_TEMPO = 0.10     # faster tempos are heard as happier
W_MAJOR = 0.10     # major key is heard as happier than minor

ENERGY_SWEET_SPOT = 0.70
TEMPO_SWEET_SPOT = 120   # BPM; upbeat walking/dancing pace
TEMPO_WIDTH = 40         # full credit at 120, zero at 80 or 160

RECCOBEATS_URL = "https://api.reccobeats.com/v1/audio-features"
RECCOBEATS_BATCH = 40     # max IDs per request

SCOPES = "user-library-read playlist-read-private playlist-modify-private playlist-modify-public"


# ---------------------------------------------------------------------------
# 1. Connect to Spotify
# ---------------------------------------------------------------------------
def connect_spotify() -> spotipy.Spotify:
    load_dotenv()
    missing = [k for k in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI")
               if not os.getenv(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")

    auth = SpotifyOAuth(scope=SCOPES, cache_path=".spotify_token_cache", open_browser=True)
    return spotipy.Spotify(auth_manager=auth, requests_timeout=20, retries=5)


# ---------------------------------------------------------------------------
# 2. Fetch Liked Songs
# ---------------------------------------------------------------------------
def fetch_liked_songs(sp: spotipy.Spotify) -> list[dict]:
    tracks, offset = [], 0
    while True:
        page = sp.current_user_saved_tracks(limit=50, offset=offset)
        for item in page["items"]:
            t = item.get("track")
            if not t or not t.get("id"):      # skip local files / removed tracks
                continue
            tracks.append({
                "id": t["id"],
                "uri": t["uri"],
                "name": t["name"],
                "artists": ", ".join(a["name"] for a in t["artists"]),
            })
        print(f"\r  fetched {len(tracks)} liked songs...", end="", flush=True)
        if not page["next"]:
            break
        offset += 50
    print()
    return tracks


def find_playlist(sp: spotipy.Spotify, name: str) -> dict | None:
    """Find one of your playlists by name (case-insensitive)."""
    results = sp.current_user_playlists(limit=50)
    while results:
        for p in results["items"]:
            if p and p["name"].strip().lower() == name.strip().lower():
                return p
        results = sp.next(results) if results.get("next") else None
    return None


def fetch_playlist_songs(sp: spotipy.Spotify, name: str) -> list[dict]:
    playlist = find_playlist(sp, name)
    if not playlist:
        sys.exit(f'No playlist named "{name}" found in your library. Check the spelling.')

    me = sp.current_user()["id"]
    if playlist["owner"]["id"] != me:
        sys.exit(f'"{name}" belongs to someone else. Since Feb 2026 Spotify only lets apps '
                 "read songs from playlists you own. Copy its songs into a playlist you "
                 "made (select all > Add to playlist) and point SOURCE_PLAYLIST at that.")

    tracks, seen, offset = [], set(), 0
    while True:
        # Feb 2026 endpoint: /playlists/{id}/items (field renamed track -> item)
        page = sp._get(f"playlists/{playlist['id']}/items", limit=50, offset=offset)
        for entry in page["items"]:
            t = entry.get("item") or entry.get("track")
            if not t or t.get("type", "track") != "track" or not t.get("id"):
                continue                      # skip podcasts, local files
            if t["id"] in seen:
                continue                      # skip duplicates
            seen.add(t["id"])
            tracks.append({
                "id": t["id"],
                "uri": t["uri"],
                "name": t["name"],
                "artists": ", ".join(a["name"] for a in t["artists"]),
            })
        print(f"\r  fetched {len(tracks)} songs from \"{playlist['name']}\"...", end="", flush=True)
        if not page.get("next"):
            break
        offset += 50
    print()
    return tracks


# ---------------------------------------------------------------------------
# 3. Get audio features (ReccoBeats, keyed by Spotify ID)
# ---------------------------------------------------------------------------
def _spotify_id_from_href(href: str | None) -> str | None:
    # ReccoBeats returns href like https://open.spotify.com/track/<spotify_id>
    if href and "/track/" in href:
        return href.rstrip("/").split("/track/")[-1].split("?")[0]
    return None


def fetch_audio_features(track_ids: list[str]) -> dict[str, dict]:
    features: dict[str, dict] = {}
    session = requests.Session()

    for i in range(0, len(track_ids), RECCOBEATS_BATCH):
        batch = track_ids[i:i + RECCOBEATS_BATCH]
        for attempt in range(5):
            resp = session.get(RECCOBEATS_URL, params={"ids": ",".join(batch)}, timeout=20)
            if resp.status_code == 429:                       # rate limited
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            break

        if resp.status_code != 200:
            print(f"\n  warning: batch {i // RECCOBEATS_BATCH + 1} failed ({resp.status_code}), skipping")
            continue

        for f in resp.json().get("content", []):
            sid = _spotify_id_from_href(f.get("href"))
            if sid:
                features[sid] = f

        print(f"\r  features for {len(features)}/{len(track_ids)} tracks...", end="", flush=True)
        time.sleep(0.3)  # be polite
    print()
    return features


# ---------------------------------------------------------------------------
# 4. Score
# ---------------------------------------------------------------------------
def passes_gates(f: dict) -> bool:
    return (f["valence"] >= VALENCE_MIN
            and ENERGY_MIN <= f["energy"] <= ENERGY_MAX)


def normalize_tempo(bpm: float | None) -> float | None:
    """Beat detectors often report half or double the felt tempo
    (a 128 BPM track read as 64). Fold it into a 75-170 BPM range."""
    if not bpm:
        return None
    while bpm < 75:
        bpm *= 2
    while bpm > 170:
        bpm /= 2
    return bpm


def _peak_fit(value: float, center: float, width: float) -> float:
    """1.0 at center, falling linearly to 0.0 at center +/- width."""
    return max(0.0, 1 - abs(value - center) / width)


def vibe_score(f: dict) -> float:
    """0-100 mood-boost score. Explained in the chat."""
    valence = f["valence"]
    energy_fit = _peak_fit(f["energy"], ENERGY_SWEET_SPOT, (ENERGY_MAX - ENERGY_MIN) / 2)
    groove = f.get("danceability", 0.5)

    bpm = normalize_tempo(f.get("tempo"))
    tempo_fit = _peak_fit(bpm, TEMPO_SWEET_SPOT, TEMPO_WIDTH) if bpm else 0.5

    mode = f.get("mode")                      # 1 = major, 0 = minor
    major = 1.0 if mode == 1 else 0.0 if mode == 0 else 0.5

    raw = (W_VALENCE * valence + W_ENERGY * energy_fit + W_GROOVE * groove
           + W_TEMPO * tempo_fit + W_MAJOR * major)
    return round(raw * 100, 1)


def rank_tracks(tracks: list[dict], features: dict[str, dict]) -> list[dict]:
    scored = []
    for t in tracks:
        f = features.get(t["id"])
        if not f:
            continue
        scored.append({**t,
                       "score": vibe_score(f),
                       "passes": passes_gates(f),
                       "valence": f["valence"], "energy": f["energy"],
                       "dance": f.get("danceability", 0.0),
                       "bpm": normalize_tempo(f.get("tempo")) or 0,
                       "key": {1: "maj", 0: "min"}.get(f.get("mode"), "?")})

    # Songs that pass every gate rank first; near-misses only fill gaps
    scored.sort(key=lambda s: (s["passes"], s["score"]), reverse=True)
    return scored


# ---------------------------------------------------------------------------
# 5. Save playlist (Feb 2026 endpoints)
# ---------------------------------------------------------------------------
def save_playlist(sp: spotipy.Spotify, uris: list[str]) -> str:
    existing = find_playlist(sp, PLAYLIST_NAME)
    if existing:
        playlist_id = existing["id"]
        # PUT replaces the contents (max 100), so re-running refreshes it
        sp._put(f"playlists/{playlist_id}/items", payload={"uris": uris[:100]})
        print("  refreshed existing playlist")
    else:
        created = sp._post("me/playlists", payload={
            "name": PLAYLIST_NAME,
            "description": PLAYLIST_DESCRIPTION,
            "public": False,
        })
        playlist_id = created["id"]
        sp._post(f"playlists/{playlist_id}/items", payload={"uris": uris[:100]})
        print("  created new playlist")

    # Spotify accepts 100 songs per request, so add the rest in chunks
    for i in range(100, len(uris), 100):
        sp._post(f"playlists/{playlist_id}/items", payload={"uris": uris[i:i + 100]})
    print(f"  saved {len(uris)} songs")
    return f"https://open.spotify.com/playlist/{playlist_id}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Connecting to Spotify...")
    sp = connect_spotify()

    if SOURCE_PLAYLIST:
        print(f'Fetching songs from "{SOURCE_PLAYLIST}"...')
        tracks = fetch_playlist_songs(sp, SOURCE_PLAYLIST)
    else:
        print("Fetching Liked Songs...")
        tracks = fetch_liked_songs(sp)
    if not tracks:
        sys.exit("No songs found.")

    print("Fetching audio features from ReccoBeats...")
    features = fetch_audio_features([t["id"] for t in tracks])
    coverage = len(features) / len(tracks) * 100
    print(f"  coverage: {coverage:.0f}% of your library has feature data")

    ranked = rank_tracks(tracks, features)
    if not ranked:
        sys.exit("No tracks had feature data, nothing to rank.")

    n_pass = sum(1 for s in ranked if s["passes"])
    if TOP_N is None:
        top = [s for s in ranked if s["passes"]]     # everything that passes, no cap
        if not top:
            sys.exit("No songs passed all filters. Try lowering VALENCE_MIN or widening the energy range.")
        print(f"\n{n_pass} of {len(ranked)} songs pass all filters:\n")
    else:
        top = ranked[:TOP_N]
        print(f"\n{n_pass} songs pass all filters. Top {len(top)}:\n")
    print(f"{'#':>4}  {'Score':>5}  {'Val':>4} {'Nrg':>4} {'Dnc':>4} {'BPM':>4} {'Key':>3}  Track")
    print("-" * 84)
    for i, s in enumerate(top, 1):
        flag = "" if s["passes"] else "  (near-miss)"
        print(f"{i:>4}  {s['score']:>5}  {s['valence']:.2f} {s['energy']:.2f} "
              f"{s['dance']:.2f} {s['bpm']:>4.0f} {s['key']:>3}  "
              f"{s['name']} - {s['artists']}{flag}")

    print("\nSaving playlist...")
    url = save_playlist(sp, [s["uri"] for s in top])
    print(f"Done: {url}")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# SETUP NOTES
# ---------------------------------------------------------------------------
# pip install spotipy requests python-dotenv
#
# .env file next to this script:
#   SPOTIPY_CLIENT_ID=your_client_id
#   SPOTIPY_CLIENT_SECRET=your_client_secret
#   SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
#
# Never commit .env or .spotify_token_cache to GitHub.
