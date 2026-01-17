import os

def update_folder_playlist(folder_path):
    if not os.path.isdir(folder_path): return False
    
    audio_formats = {".mp3", ".flac", ".ogg", ".m4a"}
    audio_files = []

    for root, _, files in os.walk(folder_path):
        for file in files:
            if os.path.splitext(file)[1].lower() in audio_formats:
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
