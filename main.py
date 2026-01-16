import sys

# Függőségek ellenőrzése
try:
    import requests
    import musicbrainzngs
    import mutagen
    import textual
    from ui import MusicDownloaderApp
except ImportError as e:
    print(f"Hiba: Hiányzó könyvtár: {e}")
    print("Telepítsd a függőségeket: pip install textual requests musicbrainzngs mutagen spotipy yt-dlp")
    exit(1)

if __name__ == "__main__":
    app = MusicDownloaderApp()
    app.run()