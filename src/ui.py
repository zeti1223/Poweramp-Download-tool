import datetime
from pathlib import Path

from rich.text import Text
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Footer, Button, Input, Label, TabbedContent, TabPane, RichLog, Select, DataTable, \
    ProgressBar, Switch

from consts import CONFIG_FILE
from downloader import *
from playlist import *
from threader import *
import threading

class MusicDownloaderApp(App):
    CSS = """
        Screen { layout: vertical; }
        .controls { height: auto; layout: horizontal; margin: 1 0; align: left middle; }
        Button { margin-right: 1; }
        #link_entry { margin-top: 1; }
        DataTable { height: 1fr; border: solid $accent; }
        DataTable > .datatable--cursor { background: $primary; color: white; }
        RichLog { height: 1fr; border: solid $accent; }
        .settings_field { margin-bottom: 1; }
        .status_bar { height: auto; layout: horizontal; align: left middle; margin: 1 0; }
        #overall_progress { width: 1fr; margin-left: 2; }
        #switch_dev { margin-bottom: 1; }
        """

    TITLE = "Music Downloader"
    BINDINGS = [("q", "quit", "Exit"), ("ctrl+v", "paste_link", "Paste")]

    def __init__(self):
        super().__init__()
        self.active_processes = []
        self.process_lock = threading.Lock()
        self.stop_requested = False
        self.pause_requested = False
        self.download_queue = []
        self.is_downloading = False
        self.item_counter = 0
        self.log_history = []

        self.expanded_folders = set()

        # Config defaults
        self.cfg_path = str(Path.home() / "MusicDownloader")
        self.cfg_sp_id = ""
        self.cfg_sp_sec = ""
        self.cfg_quality = "MP3 256kbps"
        self.cfg_max_parallel = "1"
        self.cfg_dev_mode = False
        self.cfg_template = "$artist$ - $title$"

        self.quality_map = {
            "MP3 128kbps": {"format": "mp3", "bitrate": "128K"},
            "MP3 256kbps": {"format": "mp3", "bitrate": "256K"},
            "MP3 320kbps": {"format": "mp3", "bitrate": "320K"},
            "OGG": {"format": "vorbis", "bitrate": "192K"},
            "M4A": {"format": "m4a", "bitrate": "192K"},
            "FLAC": {"format": "flac", "bitrate": "0"},
        }
        self.load_settings()

        self.thread_system = QueueSystem(max_processes=int(self.cfg_max_parallel))


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
                    yield Label("Progress:", id="overall_progress_label")
                    yield ProgressBar(total=100, show_eta=True, id="overall_progress")
                yield DataTable(id="queue_table")
            with TabPane("Detailed Log", id="tab_log"):
                yield Button("Copy Log to Clipboard", id="btn_copy_log", classes="settings_field")
                yield RichLog(id="full_log", markup=True)
            with TabPane("Settings", id="tab_settings"):
                yield Label("Download Root Folder:", classes="settings_field")
                yield Input(value=self.cfg_path, id="input_path", classes="settings_field")
                yield Label("Filename Template:", id="lbl_template", classes="settings_field")
                yield Input(value=self.cfg_template, id="template", classes="settings_field")
                yield Label("Spotify Client ID:", classes="settings_field")
                yield Input(value=self.cfg_sp_id, password=True, id="input_sp_id", classes="settings_field")
                yield Label("Spotify Client Secret:", classes="settings_field")
                yield Input(value=self.cfg_sp_sec, password=True, id="input_sp_sec", classes="settings_field")
                yield Label("Format & Quality:", classes="settings_field")
                options = [(k, k) for k in self.quality_map.keys()]
                yield Select(options, value=self.cfg_quality, id="select_quality", allow_blank=False, classes="settings_field")
                yield Label("Max Parallel Downloads (1-20):", classes="settings_field")
                yield Input(value=self.cfg_max_parallel, id="input_parallel", classes="settings_field", type="integer")
                yield Label("Developer options:", classes="settings_field")
                yield Switch(value=self.cfg_dev_mode, id="switch_dev")
                yield Button("Save Settings", id="btn_save", variant="primary")
        yield Footer()

    def on_mount(self):
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Status", "Name", "Folder")
        self.query_one("#btn_copy_log").display = self.cfg_dev_mode
        self.query_one("#lbl_template").display = self.cfg_dev_mode
        self.query_one("#template").display = self.cfg_dev_mode
        self.log_msg("Application started.", "SYSTEM")

    def action_paste_link(self):
        try:
            import pyperclip
            content = pyperclip.paste()
            if content:
                inp = self.query_one("#link_entry", Input)
                inp.value = content.strip()
                inp.focus()
                self.notify("Link pasted!")
        except:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.row_key is None:
            return

        row_key = str(event.row_key.value)
        if row_key and str(row_key).startswith("folder:"):
            folder_name = str(row_key).replace("folder:", "")

            if folder_name in self.expanded_folders:
                self.expanded_folders.remove(folder_name)
            else:
                self.expanded_folders.add(folder_name)

            self.refresh_queue_ui()

    def on_switch_changed(self, event: Switch.Changed):
        if event.switch.id == "switch_dev":
            self.query_one("#btn_copy_log").display = event.value
            self.query_one("#lbl_template").display = event.value
            self.query_one("#template").display = event.value

    def on_button_pressed(self, event: Button.Pressed):
        btn_id = event.button.id
        if btn_id == "btn_add": self.add_to_queue_thread()
        elif btn_id == "btn_start": self.start_downloads()
        elif btn_id == "btn_pause": self.toggle_pause()
        elif btn_id == "btn_abort": self.abort_process()
        elif btn_id == "btn_clear": self.clear_queue_list()
        elif btn_id == "btn_save": self.save_settings()
        elif btn_id == "btn_copy_log": self.copy_log_to_clipboard()

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
                    self.cfg_template = data.get("filename_template", "$artist$ - $title$")
                    self.cfg_dev_mode = data.get("dev_mode", False)
            except: pass

    def save_settings(self):
        self.cfg_path = self.query_one("#input_path", Input).value
        self.cfg_sp_id = self.query_one("#input_sp_id", Input).value.strip()
        self.cfg_sp_sec = self.query_one("#input_sp_sec", Input).value.strip()
        self.cfg_quality = self.query_one("#select_quality", Select).value
        self.cfg_template = self.query_one("#template", Input).value
        self.cfg_dev_mode = self.query_one("#switch_dev", Switch).value
        try:
            val = int(self.query_one("#input_parallel", Input).value)
            self.cfg_max_parallel = str(max(1, min(20, val)))
        except ValueError:
            self.cfg_max_parallel = "1"
        data = {
            "path": self.cfg_path,
            "sp_id": self.cfg_sp_id,
            "sp_sec": self.cfg_sp_sec,
            "quality": self.cfg_quality,
            "max_parallel": self.cfg_max_parallel,
            "filename_template": self.cfg_template,
            "dev_mode": self.cfg_dev_mode
        }
        with open(CONFIG_FILE, "w") as f: json.dump(data, f, indent=4)
        self.notify("Settings saved!")

    def copy_log_to_clipboard(self):
        try:
            import pyperclip
            content = "\n".join(self.log_history)
            pyperclip.copy(content)
            self.notify("Log copied to clipboard!")
        except ImportError:
            self.notify("Please install 'pyperclip' module (pip install pyperclip)", severity="error")
        except Exception as e:
            self.notify(f"Clipboard error: {e}", severity="error")

    def log_msg(self, message, level="INFO"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        color = "white"
        if level == "SUCCESS": color = "green"
        elif level == "WARNING": color = "yellow"
        elif level == "ERROR": color = "red"
        elif level == "SYSTEM": color = "blue"
        self.log_history.append(f"[{ts}] [{level}] {str(message)}")
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
        try:
            bar = self.query_one("#overall_progress", ProgressBar)

            total_tracks = 0
            done_tracks = 0

            for folder in self.download_queue:
                tracks = folder.get('tracks', [])
                total_tracks += len(tracks)

                for track in tracks:
                    if track.get('status') in ('done', 'error'):
                        done_tracks += 1

            bar.update(total=total_tracks if total_tracks > 0 else 100, progress=done_tracks)
        except Exception as e:
            self.log_msg(f"Progress bar error: {e}", "ERROR")

    def _refresh_table(self):
        table = self.query_one(DataTable)
        table.clear()

        for item in self.download_queue:
            if item["item-type"] == "track":
                status_styled = {
                    "done": "[green]DONE[/]",
                    "error": "[red]ERROR[/]",
                    "working": "[blue]WORK[/]",
                    "waiting": "WAIT"
                }.get(item['status'], item['status'])

                table.add_row(str(item['track_number']), status_styled, f"   {item['title']}", "")
            if item["item-type"] == "playlist":
                title = item['title']
                is_expanded = title in self.expanded_folders
                icon = "📂" if is_expanded else "📁"

                table.add_row(
                    "",
                    "",
                    f"[bold yellow]{icon} {title}[/]",
                    "",
                    key=f"folder:{title}"
                )

                if is_expanded:
                    for track in item['tracks']:
                        status_styled = {
                            "done": "[green]DONE[/]",
                            "error": "[red]ERROR[/]",
                            "working": "[blue]WORK[/]",
                            "waiting": "WAIT"
                        }.get(track['status'], track['status'])

                        table.add_row(str(track['track_number']), status_styled, f"   {track['title']}", title)

    def toggle_pause(self):
        if self.pause_requested:
            self.thread_system.pause()
        else:
            self.thread_system.resume()
        self.pause_requested = not self.pause_requested
        btn = self.query_one("#btn_pause", Button)
        btn.label = "Resume" if self.pause_requested else "Pause"
        btn.variant = "primary" if self.pause_requested else "warning"

    def abort_process(self):
        self.stop_requested = True
        self.thread_system.abort()
        self.is_downloading = False
        self.log_msg("Aborted.", "SYSTEM")

    def clear_queue_list(self):
        if self.is_downloading: return
        self.download_queue.clear()
        self.expanded_folders.clear()
        self.refresh_queue_ui()

    def add_to_queue_thread(self):
        link = self.query_one("#link_entry", Input).value.strip()
        if not link: return
        self.query_one("#btn_add", Button).disabled = True
        threading.Thread(target=self.process_input, args=(link,), daemon=True).start()
        self.query_one("#link_entry", Input).value = ""

    def process_input(self, link):
        self.log_msg(f"Analyzing: {link}", "ANALYZER")
        domain = link.removeprefix("https://").removeprefix("http://").split("/")[0]
        try:
            if "youtube.com" in domain or "youtu.be" in domain:
                result_dict = youtube_get_initial(link)
                self.download_queue.append(result_dict)
            elif "soundcloud.com" in domain:
                self.notify("We are working on this platform", severity="warning")
            elif "spotify.com" in domain:
                result_dict = spotify_get_initial(link)
                self.download_queue.append(result_dict)
            elif "cigoria.eu" in domain:
                self.notify("Creators: Zeti_1223 and SkyFonix")
            else:
                self.log_msg(f"Error: service at {domain} is not supported!", "ERROR")
                self.notify(f"Error: service at {domain} is not supported!", severity="error")

        except Exception as e:
            self.log_msg(f"Error: {e}", "ERROR")
            self.notify(f"Error: {e}", severity="error")

        self.refresh_queue_ui()
        self.call_from_thread(lambda: setattr(self.query_one("#btn_add", Button), "disabled", False))

    def change_state(self,state,q_num,q_s_num):
        if q_s_num is not None:
            self.download_queue[q_num][q_s_num]["state"] = state
        else:
            self.download_queue[q_num]["state"] = state

    def _download_wrapper(self,queue_num,queue_sub_num):
        self.log_msg(f"Started job {queue_sub_num} in {queue_num}","INFO")
        if queue_sub_num is not None:
            self.change_state("downloading", queue_num, queue_sub_num)
            callback = lambda state: self.change_state(state, queue_num, queue_sub_num)
            folder_name = sanitize(self.download_queue[queue_num]["title"])
            try:
                download_single(song_dict=self.download_queue[queue_num][queue_sub_num],folder_name=folder_name, callback=callback)
            except Exception as e:
                self.change_state("error", queue_num, queue_sub_num)
                raise e
        elif queue_num is None:
            self.change_state("downloading",queue_num,queue_sub_num)
            callback = lambda state: self.change_state(state,queue_num,queue_sub_num)
            try:
                download_single(song_dict=self.download_queue[queue_num],callback=callback)
            except Exception as e:
                self.change_state("error", queue_num, queue_sub_num)
                raise e

    def start_downloads(self):

        job_queue = []

        for queue_num,data in enumerate(self.download_queue):
            if data["item-type"] == "track":
                if data["status"] == "waiting":
                    job_queue.append(lambda q_num=queue_num: self._download_wrapper(q_num,None))
                self.log_msg(len(job_queue), "DEBUG")
            if data["item-type"] == "playlist":
                for queue_sub_num, data2 in enumerate(data["tracks"]):
                    if data["status"] == "waiting" or data["status"] == "error":
                        job_queue.append(lambda q_num=queue_num,q_s_num=queue_sub_num: self._download_wrapper(q_num, queue_sub_num))
        self.log_msg(job_queue,"DEBUG")
        self.thread_system.submit_jobs(job_queue)
        self.log_msg("Starting job queue","INFO")
        t = threading.Thread(target=self.thread_system.wait_completion)
        t.start()


