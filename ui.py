import os
import threading
import json
import re
import datetime
import time
from pathlib import Path

from rich.text import Text
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Footer, Button, Input, Label, TabbedContent, TabPane, RichLog, Select, DataTable, ProgressBar

from consts import CONFIG_FILE
from metadata import MetadataManager
from playlist import update_folder_playlist
from downloader import *

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
                    yield Label("Progress: 0%", id="speed_label")                
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
        domain = clean_link.strip("https://").strip("http://").split("/")[0]
        try:
            if "youtube.com" in domain or "youtu.be" in domain:
                result_dict = youtube_get_initial(clean_link)
                self.download_queue.append(result_dict)
            if "soundcloud.com" in domain:
                pass
            if "spotify.com" in domain:
                pass


        except Exception as e:
            self.log_msg(f"Error: {e}", "ERROR")

        self.refresh_queue_ui()
        self.call_from_thread(lambda: setattr(self.query_one("#btn_add", Button), "disabled", False))

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
        
        # Callback a progress bar frissítéshez
        def progress_cb(msg):
            self.call_from_thread(self.query_one("#speed_label", Label).update, msg)

        # 1. Letöltés
        success, filename = run_yt_dlp(
            item['query'], 
            quality_cfg, 
            save_dir, 
            self.process_lock, 
            self.active_processes, 
            lambda: self.stop_requested,
            progress_cb
        )

        if success and filename:
            # 2. Metadata frissítés
            self.meta_manager.apply_metadata(filename, item['display_name'], self.log_msg)
            
            # 3. Playlist frissítés
            if update_folder_playlist(str(save_dir)):
                self.log_msg(f"Playlist updated in: {save_dir.name}", "PL")
            
            item["status"] = "done"
        else:
            item["status"] = "error"
        
        self.refresh_queue_ui()
