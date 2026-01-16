import subprocess
import json
import re

def fetch_info(link):
    """Lekéri az információkat a linkről yt-dlp segítségével."""
    cmd = ["yt-dlp", "--flat-playlist", "--dump-single-json", link]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise Exception(f"yt-dlp error: {res.stderr}")
    return json.loads(res.stdout)

def get_spotify_tracks(link, client_id, client_secret):
    """Spotify link feldolgozása."""
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret))
    results = []
    
    if "/playlist/" in link:
        pid = link.split("/playlist/")[1].split("/")[0]
        pl = sp.playlist(pid)
        f_name = re.sub(r'[\\/*?:"<>|]', "", pl['name'])
        tracks = pl['tracks']['items'] # Egyszerűsítve
        for item in tracks:
            if item.get('track'):
                t = item['track']
                name = f"{t['artists'][0]['name']} - {t['name']}"
                results.append({"query": name, "display_name": name, "folder": f_name})
    elif "/track/" in link:
        tid = link.split("/track/")[1].split("/")[0]
        t = sp.track(tid)
        name = f"{t['artists'][0]['name']} - {t['name']}"
        results.append({"query": name, "display_name": name, "folder": None})
        
    return results

def run_yt_dlp(query, cfg, out_dir, process_lock, active_processes, is_stopped_func, progress_callback):
    """Futtatja a yt-dlp-t a megadott paraméterekkel."""
    url = query if query.startswith("http") else f"ytsearch1:{query}"
    
    # Parancs összeállítása
    cmd = ["yt-dlp", url, "-x", "--audio-format", cfg["format"], "--newline", "-o", str(out_dir / "%(title)s.%(ext)s")]
    
    if cfg["format"] == "webm":
        cmd = ["yt-dlp", url, "-f", "bestaudio[ext=webm]/bestaudio", "--newline", "-o", str(out_dir / "%(title)s.%(ext)s")]
    elif cfg["bitrate"] != "0":
        cmd.extend(["--audio-quality", cfg["bitrate"]])

    # Folyamat indítása
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    with process_lock:
        active_processes.append(proc)
    
    downloaded_file = None
    
    try:
        for line in proc.stdout:
            if is_stopped_func():
                proc.terminate()
                break
            
            # Fájlnév detektálása a kimenetből
            if "[download] Destination:" in line:
                downloaded_file = line.split("Destination:", 1)[1].strip()
            elif "[Merger] Merging formats into" in line:
                downloaded_file = line.split('"', 1)[1].rsplit('"', 1)[0]
            elif "[ExtractAudio] Destination:" in line:
                downloaded_file = line.split("Destination:", 1)[1].strip()
            # Ha már létezik
            elif "has already been downloaded" in line:
                downloaded_file = line.split("[download] ", 1)[1].split(" has already")[0].strip()

            # Progress bar frissítés callback
            match = re.search(r'(\d+\.\d+)%', line)
            if match and progress_callback:
                progress_callback(f"Progress: {match.group(1)}%")
    except Exception as e:
        print(f"Stream error: {e}")
    finally:
        # Biztos, ami biztos, várjuk meg a végét vagy a killt
        if proc.poll() is None:
            proc.wait()
            
        # Törlés az aktív listából
        with process_lock: 
            if proc in active_processes:
                active_processes.remove(proc)
            
    return (proc.returncode == 0, downloaded_file)
