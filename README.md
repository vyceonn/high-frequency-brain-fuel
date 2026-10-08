# High-Frequency Brain Fuel 🎧

A Python script that scans your Spotify **Liked Songs**, finds the ones whose musical features are linked to lifting your mood, and saves them to a playlist on your account.

"High frequency" here is a nickname for *upbeat and energizing*, not a pitch measurement. The script uses audio features that music-and-emotion research connects to feeling happy and energized.

## How it works

1. Reads every song in your Liked Songs through the Spotify Web API.
2. Looks up audio features for each song (positivity, energy, danceability, tempo, key).
3. Keeps songs that pass two filters, then ranks them with a mood-boost score.
4. Creates (or refreshes) a private playlist called **High-Frequency Brain Fuel**.

### Filters: a song must pass both

| Feature | Rule | Meaning |
|---|---|---|
| Valence | ≥ 0.5 | Sounds positive (0.5 is neutral) |
| Energy | 0.4 – 0.9 | Lively, without the harshest extremes |

### Ranking score (0–100)

| Factor | Weight | Why |
|---|---|---|
| Valence | 40% | Strongest predictor of music sounding happy |
| Energy | 20% | Moderate-high energy lifts mood most; peaks at 0.7 |
| Danceability | 20% | A steady, driving beat makes you want to move |
| Tempo | 10% | Faster songs sound happier; peaks at 120 BPM |
| Major key | 10% | Major keys are heard as happier than minor |

Every song comes from your own Liked Songs, and enjoying a song is the biggest factor in how music affects mood. The scoring picks the upbeat ones from music you already like.

## Why it uses two APIs

Spotify closed its `/audio-features` endpoint to new apps in November 2024, so new apps get a `403` error. This project works around that:

- **Spotify Web API** (via `spotipy`): reads your library and writes the playlist.
- **[ReccoBeats](https://reccobeats.com)** (free, no key): returns the same audio features by Spotify track ID.

It also uses the playlist endpoints Spotify introduced in February 2026 (`POST /me/playlists`, `/playlists/{id}/items`), which many older tutorials don't cover.

## Setup

### Requirements
- Python 3.10 or newer
- A **Spotify Premium** account (required for Spotify developer apps)

### 1. Create a Spotify developer app
1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and click **Create app**.
2. Add any name and description.
3. Set **Redirect URI** to `http://127.0.0.1:8888/callback`, then click **Add**.
4. Check **Web API**, accept the terms, and click **Save**.
5. In the app's **Settings**, copy the **Client ID** and **Client secret**.
6. Under **User Management**, add the email for your Spotify account.

### 2. Download and install
```
git clone https://github.com/vyceonn/high-frequency-brain-fuel.git
cd high-frequency-brain-fuel
python -m pip install -r requirements.txt
```

### 3. Add your credentials
Copy `.env.example`, rename the copy to `.env`, and fill in your values:
```
SPOTIPY_CLIENT_ID=your_client_id
SPOTIPY_CLIENT_SECRET=your_client_secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```
Never share your `.env` file or commit it to GitHub.

### 4. Run it
```
python high_frequency_brain_fuel.py
```
A browser tab asks you to approve access. After that, the script prints a ranked table and saves the playlist. Run it again anytime to refresh the playlist.

## Customizing

Edit the settings at the top of `high_frequency_brain_fuel.py`:

- `SOURCE_PLAYLIST = "playlist name"` scans one of your own playlists instead of Liked Songs.
- `TOP_N = 50` caps the playlist at 50 songs (`None` keeps every song that passes).
- `VALENCE_MIN` and `ENERGY_MIN, ENERGY_MAX` loosen or tighten the filters.
- The `W_...` weights change how songs are ranked.

## Limitations

- Audio features are a model's estimates, not exact measurements.
- ReccoBeats doesn't have every song, so some tracks may be skipped. The script prints its coverage.
- Spotify only lets apps read songs from playlists **you own**.

## Author

Conny · [GitHub](https://github.com/vyceonn) · [LinkedIn](https://linkedin.com/in/connylissette)
