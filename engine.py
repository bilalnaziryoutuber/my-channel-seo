import os
import yt_dlp
from google import genai
import json
import time
from datetime import datetime

# --- CONFIGURATION ---
CHANNEL_HANDLE = "@BilalNazir-76"       # Your actual @handle
DB_FILE = "video_db.json"
DAILY_INDEX_LIMIT = 100                  # Videos AI-polished per day
BATCH_SIZE = 5
COOLDOWN = 30                            # Seconds between batches

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4)

def get_channel_videos():
    """POWER SCRAPE: Uses the Uploads Playlist to find 100% of content."""
    base_url = f"https://www.youtube.com/{CHANNEL_HANDLE}/shorts"
    ydl_opts = {'quiet': True, 'extract_flat': True, 'playlist_items': '1:500'}

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            channel_info = ydl.extract_info(base_url, download=False)
            channel_id = channel_info.get('id')
            if channel_id and channel_id.startswith('UC'):
                # Convert Channel ID to Uploads Playlist ID
                uploads_id = 'UU' + channel_id[2:]
                uploads_url = f"https://www.youtube.com/playlist?list={uploads_id}"
                print(f"Accessing Master List: {uploads_url}")
                full_result = ydl.extract_info(uploads_url, download=False)
                return full_result.get('entries', [])
            return channel_info.get('entries', [])
        except Exception as e:
            print(f"Scrape Error: {e}")
            return []

def ai_polish(title):
    client = genai.Client()
    prompt = (
        f"Optimize this title for Pakistani and Indian trending YouTube SEO. "
        f"Use Hinglish + English keywords. Return ONLY the title, nothing else: {title}"
    )
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash", contents=prompt   # Stable model (not preview)
        )
        return response.text.strip().replace('"', '')
    except:
        return title

def build_html(videos_to_display):
    """Generates the full HTML page from a list of (vid, data) tuples."""

    cards_html = "".join([f"""
        <div class="card">
            <script type="application/ld+json">
            {{"@context":"https://schema.org","@type":"VideoObject","name":"{v['title'].replace('"', '')}","thumbnailUrl":"https://i.ytimg.com/vi/{vid}/hqdefault.jpg","uploadDate":"{v['last_refreshed']}"}}
            </script>
            <h3>{v['title']}</h3>
            <div class="video-container">
                <iframe src="https://www.youtube.com/embed/{vid}" loading="lazy" frameborder="0" allowfullscreen></iframe>
            </div>
            <a class="btn" href="{v['url']}" target="_blank">Watch Video</a>
        </div>""" for vid, v in videos_to_display])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset='UTF-8'>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="google-site-verification" content="QyDSUhm4hfRGneKuDBC1rA-p19E7Y2lI_Eh9krE5nVY" />
    <title>Hassan SEO Hub | Viral Shorts & Vlogs</title>
    <style>
        body {{ font-family: sans-serif; background: #f0f2f5; display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; padding: 25px; margin: 0; }}
        .card {{ background: #fff; padding: 15px; border-radius: 15px; box-shadow: 0 5px 15px rgba(0,0,0,0.05); }}
        .video-container {{ position: relative; padding-bottom: 56.25%; height: 0; }}
        .video-container iframe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; border-radius: 10px; border: none; }}
        .btn {{ display: block; text-align: center; background: #ff0000; color: #fff; text-decoration: none; padding: 12px; margin-top: 10px; border-radius: 8px; font-weight: bold; }}
        h3 {{ font-size: 15px; height: 40px; overflow: hidden; color: #333; margin: 0 0 10px 0; }}
    </style>
</head>
<body>
    {cards_html if cards_html else '<p style="grid-column:1/-1;text-align:center;color:#999;padding:60px;">No videos yet — check back after the first engine run.</p>'}
</body>
</html>"""

def run_rolling_cycle():
    db = load_db()
    all_videos = get_channel_videos()
    today_str = datetime.now().strftime("%Y-%m-%d")

    # --- Step 1: Register any new videos in the DB (unpolished) ---
    new_count = 0
    for v in all_videos:
        v_id = v.get('id')
        if v_id and v_id not in db:
            db[v_id] = {
                "title": v.get('title', 'YouTube Video'),
                "last_refreshed": "2000-01-01",   # Marks as unpolished
                "url": f"https://www.youtube.com/watch?v={v_id}"
            }
            new_count += 1

    print(f"Total in library: {len(db)} | New found today: {new_count}")

    # --- Step 2: Pick the OLDEST videos first for AI polishing (rolling cycle) ---
    sorted_ids = sorted(db.keys(), key=lambda x: db[x].get('last_refreshed', "2000-01-01"))
    to_process = sorted_ids[:DAILY_INDEX_LIMIT]

    print(f"Polishing {len(to_process)} videos today...")

    # --- Step 3: AI polish in batches with cooldown ---
    for i in range(0, len(to_process), BATCH_SIZE):
        batch = to_process[i:i + BATCH_SIZE]
        for v_id in batch:
            original = db[v_id]['title']
            db[v_id]['title'] = ai_polish(original)
            db[v_id]['last_refreshed'] = today_str
            print(f"  Polished: {db[v_id]['title'][:60]}...")
        save_db(db)
        if i + BATCH_SIZE < len(to_process):
            print(f"  Batch {i // BATCH_SIZE + 1} done. Cooling down {COOLDOWN}s...")
            time.sleep(COOLDOWN)

    # --- Step 4: Build display list — ONLY polished videos, sorted newest first ---
    # Unpolished videos (2000-01-01) are NEVER shown on the page.
    # Page grows organically day by day as more videos get polished.
    polished = [
        (vid, v) for vid, v in db.items()
        if v.get('last_refreshed', '2000-01-01') != '2000-01-01'
    ]
    sorted_for_display = sorted(
        polished,
        key=lambda x: x[1]['last_refreshed'],
        reverse=True
    )

    print(f"Displaying {len(sorted_for_display)} polished videos on page.")

    # --- Step 5: Write index.html ---
    html = build_html(sorted_for_display)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Done! index.html written successfully.")

if __name__ == "__main__":
    run_rolling_cycle()
