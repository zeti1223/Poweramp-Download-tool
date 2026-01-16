import subprocess
import json
import re
import ytmusicapi
import ping3


def check_network():
    is_online = ping3.ping("1.1.1.1")
    return is_online


def spotify_get_initial(link):
    pass

def youtube_get_initial(link):
    try:
        if "list" not in link:
            raise ValueError("Not Playlist Link!")
        youtube_id = link.split("/")[-1].split("?list=")[-1]
        yt_music_api = ytmusicapi.YTMusic()
        if not check_network():
            raise ConnectionError("No internet connection!")
        if youtube_id is None:
            raise ValueError("No youtube id given!")
        if len(youtube_id) != 34:
            ValueError("Invalid youtube id given!")

        try:
            data = yt_music_api.get_playlist(playlistId=youtube_id, limit=None)
            return_dict = {}

            return_dict["tracks"] = []
            for i,track in enumerate(data["tracks"]):
                print(track)
                try:
                    track_dict = {}
                    track_dict["title"] = track.get("title", "Unknown title")
                    track_dict["artists"] = [i.get("name", "Unknown artist") for i in track.get("artists")] if track.get(
                        "artists") is not None else ["Unknown artist"]

                    track_dict["album"] = track.get("album", {}).get("name", "Unknown album") if not track["album"] is None else "Unknown album"

                    track_dict["duration_seconds"] = track.get("duration", 0)
                    track_dict["thumbnail"] = track.get("thumbnails")[0]["url"].split("=")[0] + "=w600-h600" if track.get(
                        "thumbnails") is not None else None
                    track_dict["youtube_id"] = track["videoId"]
                    track_dict["track_number"] = i+1
                    track_dict["status"] = "waiting"
                    return_dict["tracks"].append(track_dict)

                except Exception as e:
                    print(e)
            try:
                return_dict["title"] = data.get("title", "Unknwon Title")
                return_dict["thumbnail"] = data.get("thumbnails", [])[-1].get("url", None)
            except Exception as e:
                print(e)

            return return_dict
        except Exception as e:
            raise e
    except Exception as e:
        raise e

def soundcloud_get_initial(link):
    pass

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
