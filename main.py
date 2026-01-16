import os
import sys
import subprocess
import threading
import shutil
import json
import re
import datetime
import time
from pathlib import Path

# Külső könyvtárak importálása
try:
    import requests
    import musicbrainzngs
    from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC, APIC
    from mutagen.mp3 import MP3
    from rich.text import Text
    from rich.markup import escape
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal
    from textual.widgets import Header, Footer, Button, Input, Label, TabbedContent, TabPane, RichLog, Select, DataTable, ProgressBar
except ImportError as e:
    print(f"Hiba: Hiányzó könyvtár: {e}")
    print("Telepítsd a függőségeket: pip install textual requests musicbrainzngs mutagen spotipy yt-dlp")
    exit(1)

CONFIG_FILE = "config.json"
USER_AGENT_STRING = "MusicDownloaderApp/2.0 (email@example.com)"

# --- Metadata Kezelő Osztály ---
class MetadataManager:
    def __init__(self):
        musicbrainzngs.set_useragent("MusicDownloaderApp", "2.0", "email@example.com")

    def search_track(self, query):
        """Keresés MusicBrainz-en a pontos query (Artist - Title) alapján."""
        try:
            # Először próbáljuk meg felbontani a query-t Artist és Title részre, ha van benne kötőjel
            artist_query = None
            recording_query = query
            
            if " - " in query:
                parts = query.split(" - ", 1)
                artist_query = parts[0].strip()
                recording_query = parts[1].strip()
                # Keresés előadó és cím alapján (pontosabb)
                result = musicbrainzngs.search_recordings(artist=artist_query, recording=recording_query, limit=5)
            else:
                # Általános keresés
                result = musicbrainzngs.search_recordings(query=query, limit=5)

            if not result.get('recording-list'):
                return None

            # Az első találat feldolgozása (Auto mód)
            recording = result['recording-list'][0]
            track_info = {
                'title': recording.get('title'),
                'artist': recording.get('artist-credit-phrase'),
                'album': 'Unknown Album',
                'year': None,
                'release_id': None
            }

            if 'release-list' in recording and recording['release-list']:
                release = recording['release-list'][0]
                track_info['album'] = release.get('title', track_info['album'])
                track_info['release_id'] = release.get('id')
                if 'date' in release:
                    match = re.match(r'(\d{4})', release['date'])
                    if match:
                        track_info['year'] = match.group(1)
            
            return track_info
        except Exception as e:
            return None

    def get_cover_art(self, release_id):
        if not release_id: return None, None
        url = f"http://coverartarchive.org/release/{release_id}/front"
        try:
            resp = requests.get(url, headers={'User-Agent': USER_AGENT_STRING}, timeout=10)
            if resp.status_code == 200:
                return resp.content, resp.headers.get('Content-Type')
        except: pass
        return None, None

    def apply_metadata(self, filepath, search_query, logger_func):
        """Metadata alkalmazása a fájlra a keresési kifejezés alapján."""
        if not os.path.exists(filepath) or not filepath.lower().endswith(".mp3"):
            return

        logger_func(f"Searching metadata for: '{search_query}'...", "META")
        track_data = self.search_track(search_query)

        if not track_data:
            logger_func("No metadata found on MusicBrainz.", "META")
            return

        try:
            audio = MP3(filepath, ID3=ID3)
            if audio.tags is None: audio.add_tags()

            audio.tags.add(TPE1(encoding=3, text=[track_data['artist']]))
            audio.tags.add(TIT2(encoding=3, text=[track_data['title']]))
            audio.tags.add(TALB(encoding=3, text=[track_data['album']]))
            if track_data['year']:
                audio.tags.add(TDRC(encoding=3, text=[track_data['year']]))

            # Borítókép
            if track_data['release_id']:
                img_data, mime = self.get_cover_art(track_data['release_id'])
                if img_data:
                    # Régi borítók törlése
                    audio.tags.delall("APIC")
                    audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=img_data))
            
            audio.save()
            logger_func(f"Metadata updated: {track_data['artist']} - {track_data['title']}", "SUCCESS")
        except Exception as e:
            logger_func(f"Metadata write error: {e}", "ERROR")

# --- Playlist Generáló Logika ---
def update_folder_playlist(folder_path):
    """Frissíti az .m3u8 playlistet az adott mappában."""
    if not os.path.isdir(folder_path): return False
    
    audio_exts = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac"}
    audio_files = []

    for root, _, files in os.walk(folder_path):
        for file in files:
            if os.path.splitext(file)[1].lower() in audio_exts:
                # Relatív útvonal a playlist fájlhoz képest
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, folder_path)
                audio_files.append(rel_path)

    if not audio_files: return False

    audio_files.sort()
    folder_name = os.path.basename(folder_path)
    playlist_path = os.path.join(folder_path, f"{folder_name}.m3u8")

    try:
        with open(playlist_path, "w", encoding="utf-8") as pl:
            pl.write("#EXTM3U\n")
            for track in audio_files:
                pl.write(track + "\n")
        return True
    except:
        return False

# --- Fő Alkalmazás ---
class MusicDownloaderApp(App):
    CSS = """
    Screen { layout: vertical; }
    .controls { height: auto; layout: horizontal; margin: 1 0; align: left middle; }
    Button { margin-right: 1; }
    #link_entry { margin-top: 1; }
    DataTable { height: 1fr; border: solid $accent; }
    RichLog { height: 1fr; border: solid $accent; }
    .settings_field { margin-bottom: 1; }
    .status_bar { height: auto; layout: horizontal; align: left middle; margin: 1 0; }
    #overall_progress { width: 1fr; margin-left: 2; }
    """

    TITLE = "Poweramp Music Downloader (Unified)"
    BINDINGS = [("q", "quit", "Kilépés"), ("ctrl+v", "paste_link", "Beillesztés")]

    def __init__(self):
        super().__init__()
        self.active_processes = []
        self.process_lock = threading.Lock()
        self.stop_requested = False
        self.pause_requested = False
        self.download_queue = []
        self.is_downloading = False
        self.item_counter = 0
        self.meta_manager = MetadataManager()

        # Config defaults
        self.cfg_path = str(Path.home() / "MusicDownloader")
        self.cfg_sp_id = ""
        self.cfg_sp_sec = ""
        self.cfg_quality = "MP3 320kbps"
        self.cfg_max_parallel = "1"

        self.quality_map = {
            "MP3 128kbps": {"format": "mp3", "bitrate": "128K"},
            "MP3 256kbps": {"format": "mp3", "bitrate": "256K"},
            "MP3 320kbps": {"format": "mp3", "bitrate": "320K"},
            "WebM (Best Audio)": {"format": "webm", "bitrate": "0"},
            "OGG": {"format": "vorbis", "bitrate": "192K"},
            "M4A": {"format": "m4a", "bitrate": "192K"},
            "FLAC": {"format": "flac", "bitrate": "0"},
        }
        self.load_settings()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Queue & Download", id="tab_queue"):
                yield Input(placeholder="Paste a link or search query...", id="link_entry")
                with Horizontal(classes="controls"):
                    yield Button("Add", id="btn_add", variant="primary")
                    yield Button("Start All", id="btn_start", variant="success")
                    yield Button("Pause", id="btn_pause", variant="warning")
                    yield Button("Abort", id="btn_abort", variant="error")
                    yield Button("Clear List", id="btn_clear")
                with Horizontal(classes="status_bar"):
                    yield Label("Ready | Speed: 0 KiB/s | Progress: 0%", id="speed_label")                
                    yield Label("   Overall progress:", id="overall_progress_label")
                    yield ProgressBar(total=100, show_eta=True, id="overall_progress")
                yield DataTable(id="queue_table")
            with TabPane("Detailed Log", id="tab_log"):
                yield RichLog(id="full_log", markup=True)
            with TabPane("Settings", id="tab_settings"):
                yield Label("Download Root Folder:", classes="settings_field")
                yield Input(value=self.cfg_path, id="input_path", classes="settings_field")
                yield Label("Spotify Client ID:", classes="settings_field")
                yield Input(value=self.cfg_sp_id, password=True, id="input_sp_id", classes="settings_field")
                yield Label("Spotify Client Secret:", classes="settings_field")
                yield Input(value=self.cfg_sp_sec, password=True, id="input_sp_sec", classes="settings_field")
                yield Label("Format & Quality:", classes="settings_field")
                options = [(k, k) for k in self.quality_map.keys()]
                yield Select(options, value=self.cfg_quality, id="select_quality", allow_blank=False, classes="settings_field")
                yield Label("Max Parallel Downloads (1-20):", classes="settings_field")
                yield Input(value=self.cfg_max_parallel, id="input_parallel", classes="settings_field", type="integer")
                yield Button("Save Settings", id="btn_save", variant="primary")
        yield Footer()

    def on_mount(self):
        table = self.query_one(DataTable)
        table.add_columns("ID", "Status", "Name", "Folder")
        self.log_msg("Application started.", "SYSTEM")

    def action_paste_link(self):
        try:
            import pyperclip
            content = pyperclip.paste()
            if content:
                inp = self.query_one("#link_entry", Input)
                inp.value = content.strip()
                inp.focus()
        except: pass

    def on_button_pressed(self, event: Button.Pressed):
        btn_id = event.button.id
        if btn_id == "btn_add": self.add_to_queue_thread()
        elif btn_id == "btn_start": self.start_downloads()
        elif btn_id == "btn_pause": self.toggle_pause()
        elif btn_id == "btn_abort": self.abort_process()
        elif btn_id == "btn_clear": self.clear_queue_list()
        elif btn_id == "btn_save": self.save_settings()

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.cfg_path = data.get("path", self.cfg_path)
                    self.cfg_sp_id = data.get("sp_id", "")
                    self.cfg_sp_sec = data.get("sp_sec", "")
                    self.cfg_quality = data.get("quality", "MP3 320kbps")
                    self.cfg_max_parallel = data.get("max_parallel", "1")
            except: pass

    def save_settings(self):
        self.cfg_path = self.query_one("#input_path", Input).value
        self.cfg_sp_id = self.query_one("#input_sp_id", Input).value.strip()
        self.cfg_sp_sec = self.query_one("#input_sp_sec", Input).value.strip()
        self.cfg_quality = self.query_one("#select_quality", Select).value
        
        try:
            val = int(self.query_one("#input_parallel", Input).value)
            self.cfg_max_parallel = str(max(1, min(20, val)))
        except ValueError:
            self.cfg_max_parallel = "1"
            
        data = {"path": self.cfg_path, "sp_id": self.cfg_sp_id, "sp_sec": self.cfg_sp_sec, "quality": self.cfg_quality, "max_parallel": self.cfg_max_parallel}
        with open(CONFIG_FILE, "w") as f: json.dump(data, f, indent=4)
        self.notify("Settings saved!")

    def log_msg(self, message, level="INFO"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        
        color = "white"
        if level == "SUCCESS": color = "green"
        elif level == "WARNING": color = "yellow"
        elif level == "ERROR": color = "red"
        elif level == "SYSTEM": color = "blue"
        
        msg = Text.from_markup(f"[{color}][{ts}] [{level}] {escape(str(message))}[/{color}]")
        
        if threading.get_ident() == self._thread_id: self.query_one("#full_log", RichLog).write(msg)
        else: self.call_from_thread(self.query_one("#full_log", RichLog).write, msg)

    def refresh_queue_ui(self):
        if threading.get_ident() == self._thread_id:
            self._refresh_table()
            self._update_progress_bar()
        else:
            self.call_from_thread(self._refresh_table)
            self.call_from_thread(self._update_progress_bar)

    def _update_progress_bar(self):
        bar = self.query_one("#overall_progress", ProgressBar)
        total = len(self.download_queue)
        done_count = len([i for i in self.download_queue if i['status'] in ('done', 'error')])
        bar.update(total=total if total > 0 else 100, progress=done_count)

    def _refresh_table(self):
        table = self.query_one(DataTable)
        table.clear()
        grouped = {}
        for item in self.download_queue:
            folder = item['folder'] if item['folder'] else "Egyéb / Nincs mappa"
            if folder not in grouped: grouped[folder] = []
            grouped[folder].append(item)

        for folder, items in grouped.items():
            table.add_row("", "", f"[bold yellow]📁 {folder}[/]", "", key=f"hdr_{folder}")
            for item in items:
                status_styled = {"done": "[green]DONE[/]", "error": "[red]ERROR[/]", "working": "[blue]WORK[/]", "waiting": "WAIT"}.get(item['status'], item['status'])
                table.add_row(str(item['id']), status_styled, item['display_name'], folder)

    def toggle_pause(self):
        self.pause_requested = not self.pause_requested
        btn = self.query_one("#btn_pause", Button)
        btn.label = "Resume" if self.pause_requested else "Pause"
        btn.variant = "primary" if self.pause_requested else "warning"

    def abort_process(self):
        self.stop_requested = True
        with self.process_lock:
            for proc in self.active_processes:
                if proc.poll() is None: proc.terminate()
        self.is_downloading = False
        self.log_msg("Aborted.", "SYSTEM")

    def clear_queue_list(self):
        if self.is_downloading: return
        self.download_queue.clear()
        self.refresh_queue_ui()

    def add_to_queue_thread(self):
        link = self.query_one("#link_entry", Input).value.strip()
        if not link: return
        self.query_one("#btn_add", Button).disabled = True
        threading.Thread(target=self.process_input, args=(link,), daemon=True).start()
        self.query_one("#link_entry", Input).value = ""

    def process_input(self, link):
        self.log_msg(f"Analyzing: {link}", "ANALYZER")
        clean_link = link.split('?')[0]
        
        # Spotify és egyéb logika (rövidítve a példa kedvéért, de a te kódodban maradjon meg)
        # Itt csak a lényeget emelem ki: a display_name beállítása kritikus!
        
        if "spotify.com" in clean_link:
            self.handle_spotify(clean_link)
        else:
            # YouTube / Generic
            try:
                cmd = ["yt-dlp", "--flat-playlist", "--dump-single-json", link]
                res = subprocess.run(cmd, capture_output=True, text=True)
                data = json.loads(res.stdout)
                if 'entries' in data:
                    f_name = re.sub(r'[\\/*?:"<>|]', "", data.get('title', 'Playlist'))
                    for entry in data['entries']:
                        title = entry.get('title', 'Unknown')
                        url = entry.get('url') or entry.get('webpage_url') or f"https://youtube.com/watch?v={entry.get('id')}"
                        self.download_queue.append({"query": url, "display_name": title, "folder": f_name, "status": "waiting", "id": self.item_counter})
                        self.item_counter += 1
                else:
                    title = data.get('title', link)
                    self.download_queue.append({"query": link, "display_name": title, "folder": None, "status": "waiting", "id": self.item_counter})
                    self.item_counter += 1
            except Exception as e:
                self.log_msg(f"Error: {e}", "ERROR")

        self.refresh_queue_ui()
        self.call_from_thread(lambda: setattr(self.query_one("#btn_add", Button), "disabled", False))

    def handle_spotify(self, link):
        # Spotify logika (a te eredeti kódod alapján, csak a lényeg)
        if not self.cfg_sp_id or not self.cfg_sp_sec:
            self.log_msg("Missing Spotify credentials!", "ERROR")
            return
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=self.cfg_sp_id, client_secret=self.cfg_sp_sec))
            
            if "/playlist/" in link:
                pid = link.split("/playlist/")[1].split("/")[0]
                pl = sp.playlist(pid)
                f_name = re.sub(r'[\\/*?:"<>|]', "", pl['name'])
                tracks = pl['tracks']['items'] # Egyszerűsítve, lapozás kellhet
                for item in tracks:
                    if item.get('track'):
                        t = item['track']
                        name = f"{t['artists'][0]['name']} - {t['name']}"
                        self.download_queue.append({"query": name, "display_name": name, "folder": f_name, "status": "waiting", "id": self.item_counter})
                        self.item_counter += 1
            elif "/track/" in link:
                tid = link.split("/track/")[1].split("/")[0]
                t = sp.track(tid)
                name = f"{t['artists'][0]['name']} - {t['name']}"
                self.download_queue.append({"query": name, "display_name": name, "folder": None, "status": "waiting", "id": self.item_counter})
                self.item_counter += 1
        except Exception as e:
            self.log_msg(f"Spotify Error: {e}", "ERROR")

    def start_downloads(self):
        if not self.download_queue or self.is_downloading: return
        self.is_downloading = True
        self.stop_requested = False
        threading.Thread(target=self.download_loop, daemon=True).start()

    def download_loop(self):
        quality_cfg = self.quality_map[self.cfg_quality]
        base_path = Path(self.cfg_path)
        active_threads = []

        while True:
            active_threads = [t for t in active_threads if t.is_alive()]
            if self.stop_requested: break
            waiting = [i for i in self.download_queue if i["status"] == "waiting"]
            if not waiting and not active_threads: break
            
            if self.pause_requested:
                time.sleep(0.5)
                continue

            if waiting and len(active_threads) < int(self.cfg_max_parallel):
                item = waiting[0]
                item["status"] = "working"
                self.refresh_queue_ui()
                t = threading.Thread(target=self.process_single_item, args=(item, quality_cfg, base_path))
                t.start()
                active_threads.append(t)
                continue
            time.sleep(0.5)

        for t in active_threads: t.join()
        self.is_downloading = False
        self.log_msg("All tasks finished.", "SYSTEM")

    def process_single_item(self, item, quality_cfg, base_path):
        save_dir = base_path / item['folder'] if item['folder'] else base_path
        save_dir.mkdir(parents=True, exist_ok=True)

        self.log_msg(f"Downloading: {item['display_name']}", "DL")
        
        # 1. Letöltés és fájlnév kinyerése
        success, filename = self.run_yt_dlp(item['query'], quality_cfg, save_dir)

        if success and filename:
            # 2. Metadata frissítés AZONNAL, a pontos névvel (display_name)
            self.meta_manager.apply_metadata(filename, item['display_name'], self.log_msg)
            
            # 3. Playlist frissítés csak ebben a mappában
            if update_folder_playlist(str(save_dir)):
                self.log_msg(f"Playlist updated in: {save_dir.name}", "PL")
            
            item["status"] = "done"
        else:
            item["status"] = "error"
        
        self.refresh_queue_ui()

    def run_yt_dlp(self, query, cfg, out_dir):
        url = query if query.startswith("http") else f"ytsearch1:{query}"
        # Fontos: --print filename használata a pontos fájlnév elkapásához
        cmd = ["yt-dlp", url, "-x", "--audio-format", cfg["format"], "--newline", "-o", str(out_dir / "%(title)s.%(ext)s")]
        
        if cfg["format"] == "webm":
            cmd = ["yt-dlp", url, "-f", "bestaudio[ext=webm]/bestaudio", "--newline", "-o", str(out_dir / "%(title)s.%(ext)s")]
        elif cfg["bitrate"] != "0":
            cmd.extend(["--audio-quality", cfg["bitrate"]])

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        with self.process_lock: self.active_processes.append(proc)
        
        downloaded_file = None
        
        for line in proc.stdout:
            if self.stop_requested: break
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

            # Progress bar frissítés
            match = re.search(r'(\d+\.\d+)%', line)
            if match:
                self.call_from_thread(self.query_one("#speed_label", Label).update, f"Progress: {match.group(1)}%")

        proc.wait()
        with self.process_lock: 
            if proc in self.active_processes: self.active_processes.remove(proc)
            
        return (proc.returncode == 0, downloaded_file)

if __name__ == "__main__":
    app = MusicDownloaderApp()
    app.run()