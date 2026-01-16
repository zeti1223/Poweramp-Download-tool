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

# TUI imports
try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal
    from textual.widgets import Header, Footer, Button, Input, Label, TabbedContent, TabPane, Log, Select, DataTable, ProgressBar
except ImportError:
    print("Hiba: A 'textual' könyvtár hiányzik. Telepítsd: pip install textual")
    exit(1)

CONFIG_FILE = "config.json"

class MusicDownloaderApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    .controls {
        height: auto;
        layout: horizontal;
        margin: 1 0;
        align: left middle;
    }
    Button {
        margin-right: 1;
    }
    #link_entry {
        margin-top: 1;
    }
    DataTable {
        height: 1fr;
        border: solid $accent;
    }
    Log {
        height: 1fr;
        border: solid $accent;
    }
    .settings_field {
        margin-bottom: 1;
    }
    .status_bar {
        height: auto;
        layout: horizontal;
        align: left middle;
        margin: 1 0;
    }
    #overall_progress {
        width: 1fr;
        margin-left: 2;
    }
    """

    TITLE = "Poweramp Music Downloader"
    BINDINGS = [
        ("q", "quit", "Kilépés"),
        ("ctrl+v", "paste_link", "Beillesztés")
    ]

    def __init__(self):
        super().__init__()

        # Logic variables
        self.active_processes = []
        self.process_lock = threading.Lock()
        self.stop_requested = False
        self.pause_requested = False
        self.download_queue = []
        self.is_downloading = False
        self.item_counter = 0

        # Configuration variables
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
                yield Log(id="full_log")

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
                
                yield Label("Max Parallel Downloads:", classes="settings_field")
                parallel_options = [(str(i), str(i)) for i in range(1, 6)]
                yield Select(parallel_options, value=self.cfg_max_parallel, id="select_parallel", allow_blank=False, classes="settings_field")
                
                yield Button("Save Settings", id="btn_save", variant="primary")
        yield Footer()

    def action_paste_link(self):
        """Beilleszti a vágólap tartalmát a link mezőbe."""
        try:
            import pyperclip
            content = pyperclip.paste()
            if content:
                inp = self.query_one("#link_entry", Input)
                inp.value = content.strip()
                inp.focus()
                self.notify("Link beillesztve!")
        except ImportError:
            self.notify("Hiba: Telepítsd a 'pyperclip' modult! (pip install pyperclip)", severity="error")
        except Exception as e:
            self.notify(f"Hiba: {e}", severity="error")

    def on_mount(self):
        table = self.query_one(DataTable)
        table.add_columns("ID", "Status", "Name", "Folder")
        self.log_msg("Application started.", "SYSTEM")

    def on_button_pressed(self, event: Button.Pressed):
        btn_id = event.button.id
        if btn_id == "btn_add":
            self.add_to_queue_thread()
        elif btn_id == "btn_start":
            self.start_downloads()
        elif btn_id == "btn_pause":
            self.toggle_pause()
        elif btn_id == "btn_abort":
            self.abort_process()
        elif btn_id == "btn_clear":
            self.clear_queue_list()
        elif btn_id == "btn_save":
            self.save_settings()

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
        self.cfg_max_parallel = self.query_one("#select_parallel", Select).value

        data = {
            "path": self.cfg_path,
            "sp_id": self.cfg_sp_id,
            "sp_sec": self.cfg_sp_sec,
            "quality": self.cfg_quality,
            "max_parallel": self.cfg_max_parallel
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)
        self.log_msg("Settings saved to local config.", "UI")
        self.notify("Configuration saved!")

    def log_msg(self, message, level="INFO"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        msg = f"[{ts}] [{level}] {message}\n"
        if threading.get_ident() == self._thread_id:
            self.query_one("#full_log", Log).write(msg)
        else:
            self.call_from_thread(self.query_one("#full_log", Log).write, msg)

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
        if total == 0:
            bar.update(progress=0, total=100)
            return
        
        done_count = len([i for i in self.download_queue if i['status'] in ('done', 'error')])
        bar.update(total=total, progress=done_count)

    def _refresh_table(self):
        table = self.query_one(DataTable)
        table.clear()
        
        # Group items by folder
        grouped = {}
        for item in self.download_queue:
            folder = item['folder'] if item['folder'] else "Egyéb / Nincs mappa"
            if folder not in grouped:
                grouped[folder] = []
            grouped[folder].append(item)

        # Add rows with headers
        for folder, items in grouped.items():
            # Add a "Header" row for the folder
            table.add_row("", "", f"[bold yellow]📁 {folder}[/]", "", key=f"hdr_{folder}")
            
            for item in items:
                status_styled = item['status'].upper()
                if item['status'] == 'done':
                    status_styled = "[green]DONE[/]"
                elif item['status'] == 'error':
                    status_styled = "[red]ERROR[/]"
                elif item['status'] == 'working':
                    status_styled = "[blue]WORK[/]"
                
                table.add_row(
                    str(item['id']), 
                    status_styled, 
                    item['display_name'], 
                    folder
                )

    def toggle_pause(self):
        self.pause_requested = not self.pause_requested
        btn = self.query_one("#btn_pause", Button)
        btn.label = "Resume" if self.pause_requested else "Pause"
        btn.variant = "primary" if self.pause_requested else "warning"
        self.log_msg(f"Process {'PAUSED' if self.pause_requested else 'RESUMED'}", "USER")

    def abort_process(self):
        self.stop_requested = True
        with self.process_lock:
            for proc in self.active_processes:
                if proc.poll() is None: proc.terminate()
        self.is_downloading = False
        self.query_one("#speed_label", Label).update("Aborted | Speed: 0 KiB/s | Progress: 0%")
        self.log_msg("Download process aborted.", "SYSTEM")

    def clear_queue_list(self):
        if self.is_downloading: return
        self.download_queue.clear()
        self.refresh_queue_ui()
        self.query_one("#speed_label", Label).update("Ready | Speed: 0 KiB/s | Progress: 0%")
        self.log_msg("Queue cleared.", "UI")

    def init_spotify(self):
        cid = self.cfg_sp_id
        sec = self.cfg_sp_sec
        if not cid or not sec:
            self.log_msg("ERROR: Missing Spotify ID or Secret in Settings!", "ERROR")
            return None
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            return spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=cid, client_secret=sec))
        except Exception as e:
            self.log_msg(f"Spotify Authentication Failed: {e}", "ERROR")
            return None

    def add_to_queue_thread(self):
        link_input = self.query_one("#link_entry", Input)
        link = link_input.value.strip()
        if not link: return
        
        self.query_one("#btn_add", Button).disabled = True
        threading.Thread(target=self.process_input, args=(link,), daemon=True).start()
        link_input.value = ""

    def process_input(self, link):
        self.log_msg(f"Analyzing: {link}", "ANALYZER")
        clean_link = link.split('?')[0]

        # Spotify detection
        if "spotify.com" in clean_link:
            sp = self.init_spotify()
            if not sp:
                self.call_from_thread(lambda: setattr(self.query_one("#btn_add", Button), "disabled", False))
                return

            try:
                if "/playlist/" in clean_link:
                    playlist_id = clean_link.split("/playlist/")[1].split("/")[0]
                    pl = sp.playlist(playlist_id)
                    f_name = re.sub(r'[\\/*?:"<>|]', "", pl['name'])
                    results = sp.playlist_items(playlist_id)
                    tracks = results['items']
                    while results['next']:
                        results = sp.next(results)
                        tracks.extend(results['items'])

                    for item in tracks:
                        if item.get('track'):
                            t = item['track']
                            name = f"{t['artists'][0]['name']} - {t['name']}"
                            self.download_queue.append({"query": name, "display_name": name, "folder": f_name, "status": "waiting", "id": self.item_counter})
                            self.item_counter += 1
                    self.log_msg(f"Added {len(tracks)} tracks from playlist: {pl['name']}", "SUCCESS")

                elif "/track/" in clean_link:
                    track_id = clean_link.split("/track/")[1].split("/")[0]
                    t = sp.track(track_id)
                    name = f"{t['artists'][0]['name']} - {t['name']}"
                    self.download_queue.append({"query": name, "display_name": name, "folder": None, "status": "waiting", "id": self.item_counter})
                    self.item_counter += 1
                    self.log_msg(f"Added track: {name}", "SUCCESS")
            except Exception as e:
                self.log_msg(f"Spotify API error: {e}", "ERROR")

        elif "youtube.com" in link or "youtu.be" in link:
            try:
                cmd = ["yt-dlp", "--flat-playlist", "--dump-single-json", link]
                res = subprocess.run(cmd, capture_output=True, text=True)
                data = json.loads(res.stdout)
                if 'entries' in data:
                    f_name = re.sub(r'[\\/*?:"<>|]', "", data.get('title', 'YT_Playlist'))
                    for entry in data['entries']:
                        title = entry.get('title', 'Unknown Title')
                        url = f"https://www.youtube.com/watch?v={entry['id']}" if 'id' in entry else title
                        self.download_queue.append({"query": url, "display_name": title, "folder": f_name, "status": "waiting", "id": self.item_counter})
                        self.item_counter += 1
                    self.log_msg(f"Added YouTube playlist: {f_name}", "SUCCESS")
                else:
                    title = data.get('title', link)
                    self.download_queue.append({"query": link, "display_name": title, "folder": None, "status": "waiting", "id": self.item_counter})
                    self.item_counter += 1
                    self.log_msg(f"Added YouTube video: {title}", "SUCCESS")
            except Exception as e: self.log_msg(f"YouTube analyzer error: {e}", "ERROR")

        elif "soundcloud.com" in link or "on.soundcloud.com" in link:
            try:
                cmd = ["yt-dlp", "--flat-playlist", "--dump-single-json", link]
                res = subprocess.run(cmd, capture_output=True, text=True)
                data = json.loads(res.stdout)
                if 'entries' in data:
                    f_name = re.sub(r'[\\/*?:"<>|]', "", data.get('title', 'SC_Playlist'))
                    for entry in data['entries']:
                        title = entry.get('title', 'Unknown Title')
                        url = entry.get('url') or entry.get('webpage_url') or title
                        self.download_queue.append({"query": url, "display_name": title, "folder": f_name, "status": "waiting", "id": self.item_counter})
                        self.item_counter += 1
                    self.log_msg(f"Added SoundCloud playlist: {f_name}", "SUCCESS")
                else:
                    title = data.get('title', link)
                    self.download_queue.append({"query": link, "display_name": title, "folder": None, "status": "waiting", "id": self.item_counter})
                    self.item_counter += 1
                    self.log_msg(f"Added SoundCloud track: {title}", "SUCCESS")
            except Exception as e: self.log_msg(f"SoundCloud analyzer error: {e}", "ERROR")

        else: # Generic search
            self.download_queue.append({"query": link, "display_name": link, "folder": None, "status": "waiting", "id": self.item_counter})
            self.item_counter += 1
            self.log_msg(f"Added search query: {link}", "SUCCESS")

        self.refresh_queue_ui()
        self.call_from_thread(lambda: setattr(self.query_one("#btn_add", Button), "disabled", False))

    def start_downloads(self):
        if not self.download_queue or self.is_downloading: return
        self.is_downloading = True
        self.stop_requested = False
        # Run download loop in a thread to not block TUI
        threading.Thread(target=self.download_loop, daemon=True).start() 

    def download_loop(self):
        quality_cfg = self.quality_map[self.cfg_quality]
        base_path = Path(self.cfg_path)
        active_threads = []

        while True:
            active_threads = [t for t in active_threads if t.is_alive()]

            if self.stop_requested: break

            waiting_items = [i for i in self.download_queue if i["status"] == "waiting"]
            working_items = [i for i in self.download_queue if i["status"] == "working"]

            if not waiting_items and not working_items and not active_threads:
                break

            if self.pause_requested:
                time.sleep(0.5)
                continue

            max_p = int(self.cfg_max_parallel)
            if waiting_items and len(active_threads) < max_p:
                item = waiting_items[0]
                item["status"] = "working"
                self.refresh_queue_ui()
                
                t = threading.Thread(target=self.process_single_item, args=(item, quality_cfg, base_path))
                t.start()
                active_threads.append(t)
                continue

            time.sleep(0.5)

        for t in active_threads:
            t.join()

        self.run_post_processing()
        self.is_downloading = False
        self.log_msg("All tasks completed.", "SYSTEM")

    def process_single_item(self, item, quality_cfg, base_path):
        save_dir = base_path / item['folder'] if item['folder'] else base_path
        save_dir.mkdir(parents=True, exist_ok=True)

        self.log_msg(f"Downloading: {item['display_name']}", "PROCESS")
        success = self.run_yt_dlp(item['query'], quality_cfg, save_dir)

        item["status"] = "done" if success else "error"
        self.refresh_queue_ui()

    def run_yt_dlp(self, query, cfg, out_dir):
        url = query if query.startswith("http") else f"ytsearch1:{query}"

        # Base command
        cmd = ["yt-dlp", url, "-x", "--audio-format", cfg["format"], "--newline", "-o", str(out_dir / "%(title)s.%(ext)s")]

        # WebM handling - usually we want the best audio without recoding if possible
        if cfg["format"] == "webm":
            # Just extract best audio that is webm/opus
            cmd = ["yt-dlp", url, "-f", "bestaudio[ext=webm]/bestaudio", "--newline", "-o", str(out_dir / "%(title)s.%(ext)s")]
        elif cfg["bitrate"] != "0":
            cmd.extend(["--audio-quality", cfg["bitrate"]])

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        with self.process_lock:
            self.active_processes.append(proc)
        for line in proc.stdout:
            if self.stop_requested: break
            match = re.search(r'\[download\]\s+(\d+\.\d+)%.*at\s+([\d\.]+\w+/s)', line)
            if match:
                p, s = match.groups()
                self.call_from_thread(self.query_one("#speed_label", Label).update, f"Speed: {s} | Progress: {p}%")
        proc.wait()
        
        with self.process_lock:
            if proc in self.active_processes:
                self.active_processes.remove(proc)
        return proc.returncode == 0

    def run_post_processing(self):
        """Futtatja a metadata frissítőt és szükség esetén a playlist generátort."""
        self.log_msg("Starting post-processing...", "SYSTEM")
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # 1. Metadata frissítés futtatása (Auto módban)
        try:
            meta_script = os.path.join(script_dir, "metadata.py")
            self.log_msg("Running metadata updater (Auto Mode)...", "PROCESS")
            # -u kapcsoló a bufferelés kikapcsolásához, hogy lássuk a logokat
            proc = subprocess.Popen([sys.executable, "-u", meta_script, "--auto"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            for line in proc.stdout:
                if line.strip():
                    self.log_msg(f"[Meta] {line.strip()}", "META")
            proc.wait()
            self.log_msg("Metadata update finished.", "SUCCESS")
        except Exception as e:
            self.log_msg(f"Error running metadata.py: {e}", "ERROR")

        # 2. Playlist generátor futtatása (csak ha volt mappás letöltés)
        has_folder = any(item.get('folder') for item in self.download_queue if item.get('status') == 'done')
        
        if has_folder:
            try:
                pl_script = os.path.join(script_dir, "playlist-generator.py")
                self.log_msg("Running playlist generator...", "PROCESS")
                proc = subprocess.Popen([sys.executable, "-u", pl_script],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    if line.strip():
                        self.log_msg(f"[PL] {line.strip()}", "PL_GEN")
                proc.wait()
                self.log_msg("Playlist generation finished.", "SUCCESS")
            except Exception as e:
                self.log_msg(f"Error running playlist-generator.py: {e}", "ERROR")

if __name__ == "__main__":
    app = MusicDownloaderApp()
    app.run()