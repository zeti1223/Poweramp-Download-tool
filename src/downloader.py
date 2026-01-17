import subprocess
import json
import re
import yt_dlp
import ytmusicapi
import ping3
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import imageio_ffmpeg as ffmpeg
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.flac import FLAC, Picture
import requests
import base64

# Constants

quality_map = {
    "MP3 128kbps": {"ext": "mp3", "codec": "libmp3lame", "bitrate": "128K"},
    "MP3 256kbps": {"ext": "mp3", "codec": "libmp3lame", "bitrate": "256K"},
    "MP3 320kbps": {"ext": "mp3", "codec": "libmp3lame", "bitrate": "320K"},
    "OGG": {"ext": "ogg", "codec": "libvorbis", "bitrate": "192K"},
    "M4A": {"ext": "m4a", "codec": "aac", "bitrate": "192K"},
    "FLAC": {"ext": "flac", "codec": "flac", "bitrate": "0"}
}
tag_map = {
    "mp3": {"handler": EasyID3, "title": "title", "artist": "artist", "album": "album", "date": "date"},
    "m4a": {"handler": MP4, "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "date": "\xa9day"},
    "ogg": {"handler": OggVorbis, "title": "TITLE", "artist": "ARTIST", "album": "ALBUM", "date": "DATE"},
    "flac": {"handler": FLAC, "title": "TITLE", "artist": "ARTIST", "album": "ALBUM", "date": "DATE"}
}

# Helper functions
def check_network():
    is_online = ping3.ping("1.1.1.1")
    return is_online

def sanitize(s):
    return re.sub(r'[<>:"/\\|?*\']', '', s)

def download_file(url: str, save_path: str):
    if check_network():
        r = requests.get(url, stream=True)
        r.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return save_path
    return None, None

def template_decoder(template, data: dict = None, magic_char: str = "$"):
    if data is None: data = {}
    final, opened, opened_keyword = "", False, ""
    for char in template:
        if not opened:
            if char == magic_char:
                opened = True
            else:
                final += char
            continue
        if opened:
            if char == magic_char:
                opened = False
                final += str(data.get(opened_keyword, ''))
                opened_keyword = ""
            else:
                opened_keyword += char
            continue

    return re.sub(r'[<>:"/\\|?*\']', '', final).strip()


def transcode_audio(input_file: str, output_path: str, filename: str,
                    quality_preset: str = "MP3 256kbps", overwrite: bool = True):
    
    if not all([input_file, output_path, filename]):
        raise ValueError("Input file, output path, and filename are required.")

    if quality_preset not in quality_map:
        raise ValueError(f"Invalid preset. Choose from: {list(quality_map.keys())}")

    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")

    if not os.path.exists(output_path):
        os.makedirs(output_path, exist_ok=True)

    settings = quality_map[quality_preset]
    output_ext = settings["ext"]
    bitrate = settings["bitrate"]
    codec = settings["codec"]

    clean_filename = "".join([c for c in filename if c.isalnum() or c in (' ', '.', '_')]).rstrip()
    output_file = os.path.join(output_path, f"{clean_filename}.{output_ext}")

    if os.path.exists(output_file) and not overwrite:
        raise FileExistsError(f"Output file already exists: {output_file}")

    ffmpeg_path = ffmpeg.get_ffmpeg_exe()

    command = [
        ffmpeg_path,
        '-loglevel', 'quiet',
        '-i', input_file,
        '-c:a', codec,
    ]
    if bitrate != "0":
        command.extend(['-b:a', bitrate])
    if overwrite:
        command.append('-y')
    else:
        command.append('-n')
    command.append(output_file)
    try:
        subprocess.run(command, check=True)
        return output_file
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg process failed: {e}")


def edit_audio_metadata(input_file: str, data: dict):
    if not input_file or not os.path.exists(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")
    if not data:
        raise ValueError("No metadata provided.")

    ext = os.path.splitext(input_file)[1].lstrip(".").lower()
    if ext not in tag_map:
        raise ValueError(f"Unsupported format: {ext}")

    mapping = tag_map[ext]
    audio = mapping["handler"](input_file)

    tags = audio.tags if ext == "m4a" else audio
    if tags is None and ext == "m4a":
        audio.add_tags()
        tags = audio.tags

    artists = data.get("artists")
    if artists and isinstance(artists, list):
        artist_str = ", ".join(artists)
        tags[mapping["artist"]] = artist_str

        if ext == "mp3":
            tags["albumartist"] = artist_str

    field_mapping = {
        "title": mapping["title"],
        "album": mapping["album"],
        "year": mapping["date"]
    }

    for data_key, tag_key in field_mapping.items():
        val = data.get(data_key)
        if val is not None:
            tags[tag_key] = str(val)
    if ext == "mp3":
        audio.save(v2_version=3)
    else:
        audio.save()

    return data

def add_cover_art(audio_path: str, image_path: str):
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    ext = os.path.splitext(audio_path)[1].lstrip(".").lower()

    with open(image_path, "rb") as img_file:
        image_data = img_file.read()

    if ext == "mp3":
        try:
            audio = ID3(audio_path)
        except Exception:
            audio = ID3()

        audio.add(APIC(
            encoding=3,
            mime='image/png',
            type=3,
            desc='Front Cover',
            data=image_data
        ))
        audio.save(audio_path, v2_version=3)

    elif ext == "m4a":
        audio = MP4(audio_path)
        cover = MP4Cover(image_data, imageformat=MP4Cover.FORMAT_PNG)
        audio.tags["covr"] = [cover]
        audio.save()

    elif ext == "flac":
        audio = FLAC(audio_path)
        picture = Picture()
        picture.data = image_data
        picture.type = 3
        picture.mime = "image/png"
        picture.desc = "Front Cover"
        audio.add_picture(picture)
        audio.save()

    elif ext == "ogg":
        audio = OggVorbis(audio_path)
        picture = Picture()
        picture.data = image_data
        picture.type = 3
        picture.mime = "image/png"
        picture.desc = "Front Cover"

        picture_data = base64.b64encode(picture.write()).decode('ascii')
        audio["metadata_block_picture"] = [picture_data]
        audio.save()

    else:
        raise ValueError(f"Unsupported format for cover art: {ext}")

    return True

def spotify_get_initial(link):
    try:
        if "playlist/" not in link and "album/" not in link and "track/" not in link:
            raise ValueError("Not Playlist Link!")
        if not check_network():
            raise ConnectionError("No internet connection!")
        spotify_id = link.split("/")[-1].split("?")[0]
        if len(spotify_id) != 22:
            ValueError("Invalid spotify id given!")
        if spotify_id is None:
            raise ValueError("Invalid spotify id given!")

        with open("../config.json", "r") as f:config = json.load(f)

        if config["sp_id"] == "" or config["sp_sec"] == "":
            raise ValueError("No spotify tokens given!")


        return_dict = {}
        client_credentials_mgmt = SpotifyClientCredentials(client_id=config["sp_id"],client_secret=config["sp_sec"])
        sp = spotipy.Spotify(client_credentials_manager=client_credentials_mgmt)
        if "playlist/" in link:
            collection_data = sp.playlist(spotify_id)

            return_dict["tracks"] = []
            return_dict["title"] = collection_data.get("name", "Unknown title")
            return_dict["thumbnail"] = collection_data.get("images", [])[0].get("url", None)
            return_dict["type"] = "spotify"
            return_dict["item-type"] = "playlist"

            return_result = sp.playlist_items(spotify_id)

            tracks = return_result['items']
            while return_result['next']: return_result = sp.next(return_result); tracks.extend(return_result['items'])

            for i, track in enumerate(tracks):
                track_dict = {}
                track_dict["title"] = track["track"].get("name", "Unknown title")
                track_dict["artists"] = [i.get("name", "Unknown artist") for i in
                                         track["track"].get("artists")] if track.get(
                    "artists") is not None else ["Unknown artist"]
                track_dict["album"] = track["track"].get("album", {}).get("name", "Unknown album")
                track_dict["duration_seconds"] = str(track["track"].get("duration_ms", 0) // 1000)
                track_dict["release"] = track["track"].get("album", {}).get("release_date", None)
                track_dict["release"] = track_dict["release"].split("-")[0] if track_dict["release"] else None
                track_dict["thumbnail"] = track["track"]["album"]["images"][0].get("url", None)
                track_dict["track_number"] = i + 1
                track_dict["status"] = "waiting"
                track_dict["spotify_id"] = track["track"].get("id", None)
                track_dict["type"] = "spotify"
                track_dict["item-type"] = "track"
                return_dict["tracks"].append(track_dict)
        if "album/" in link:
            collection_data = sp.album(spotify_id)

            return_dict["tracks"] = []
            return_dict["title"] = collection_data.get("name", "Unknown title")
            return_dict["thumbnail"] = collection_data.get("images", [])[0].get("url", None)
            return_dict["type"] = "spotify"
            return_dict["spotify_id"] = spotify_id
            return_dict["item-type"] = "playlist"

            for i, track in enumerate(collection_data["tracks"]["items"]):
                track_dict = {}
                track_dict["title"] = track.get("name", "Unknown title")
                track_dict["artists"] = [i.get("name", "Unknown artist") for i in
                                         track.get("artists")] if track.get(
                    "artists") is not None else ["Unknown artist"]
                track_dict["album"] = collection_data.get("name", "Unknown album")
                track_dict["duration_seconds"] = str(track.get("duration_ms", 0) // 1000)
                track_dict["release"] = collection_data.get("release_date", None)
                track_dict["release"] = track_dict["release"].split("-")[0] if track_dict["release"] else None
                track_dict["thumbnail"] = return_dict.get("thumbnail",None)
                track_dict["track_number"] = i + 1
                track_dict["status"] = "waiting"
                track_dict["spotify_id"] = track.get("id", None)
                track_dict["type"] = "spotify"
                track_dict["item-type"] = "track"
                return_dict["tracks"].append(track_dict)
        if "track/" in link:
            track_data = sp.track(spotify_id)

            return_dict["title"] =track_data.get("name", "Unknown title")
            return_dict["album"] = track_data.get("album", {}).get("name", "Unknown album")
            return_dict["artists"] = [i.get("name", "Unknown artist") for i in
                                     track_data.get("artists")] if track_data.get(
                "artists") is not None else ["Unknown artist"]
            return_dict["duration_seconds"] = str(track_data.get("duration_ms", 0) // 1000)
            return_dict["release"] = track_data["album"].get("release_date", None)
            return_dict["release"] = return_dict["release"].split("-")[0] if return_dict["release"] else None
            return_dict["thumbnail"] = track_data["album"]["images"][0].get("url", None)
            return_dict["track_number"] = 1
            return_dict["status"] = "waiting"
            return_dict["spotify_id"] = track_data.get("id", None)
            return_dict["type"] = "spotify"
            return_dict["item-type"] = "track"

        return return_dict
    except Exception as e:
        raise e

def youtube_get_initial(link):
    try:
        if "list" not in link and "watch?v=" not in link:
            raise ValueError("Not a valid Link!")
        if not check_network():
            raise ConnectionError("No internet connection!")
        yt_music_api = ytmusicapi.YTMusic()
        try:
            return_dict = {}
            if "watch?v=" in link:
                youtube_id = link.split("watch?v=")[1].split("&")[0]
                if youtube_id is None:
                    raise ValueError("No youtube id given!")
                if len(youtube_id) != 11:
                    ValueError("Invalid youtube id given!")
                data = yt_music_api.get_song(youtube_id)
                return_dict["title"] = data["videoDetails"].get("title", "Unknown title")
                return_dict["album"] = "Unknown album"
                return_dict["artists"] = data["videoDetails"].get("author", "Unknown artist").split("&")
                return_dict["release"] = None
                return_dict["thumbnail"] = data["videoDetails"]["thumbnail"]["thumbnails"][-1].get("url", None)
                return_dict["track_number"] = 1
                return_dict["status"] = "waiting"
                return_dict["youtube_id"] = youtube_id
                return_dict["type"] = "youtube"
                return_dict["item-type"] = "track"


            elif "?list=" in link:
                youtube_id = link.split("/")[-1].split("?list=")[-1]
                if youtube_id is None:
                    raise ValueError("No youtube id given!")
                if len(youtube_id) != 34:
                    ValueError("Invalid youtube id given!")

                data = yt_music_api.get_playlist(playlistId=youtube_id, limit=None)
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
                        track_dict["type"] = "youtube"
                        track_dict["item-type"] = "track"
                        track_dict["release"] = None
                        track_dict["track_number"] = i+1
                        track_dict["status"] = "waiting"
                        return_dict["tracks"].append(track_dict)

                    except Exception as e:
                        print(e)
                try:
                    return_dict["title"] = data.get("title", "Unknown Title")
                    return_dict["thumbnail"] = data.get("thumbnails", [])[-1].get("url", None)
                    return_dict["type"] = "youtube"
                    return_dict["item-type"] = "playlist"

                except Exception as e:
                    print(e)

            return return_dict
        except Exception as e:
            raise e
    except Exception as e:
        raise e

def soundcloud_get_initial(link):
    pass

# Download functions for service

def download_spotify(song_dict):
    os.makedirs(".TEMP", exist_ok=True)
    search_query = f"{song_dict['title']} {' '.join(song_dict['artists'])}"
    if not check_network():
        raise ConnectionError("No internet connection!")
    yt_music_api = ytmusicapi.YTMusic()
    result_for_search = yt_music_api.search(search_query,filter="songs",limit=1)
    result = download_youtube(result_for_search[0]["videoId"])
    return result["file_path"]

def download_youtube(youtube_id):
    if not check_network():
        raise ConnectionError("No internet connection!")
    if youtube_id is None:
        raise ValueError("No youtube id given!")
    if len(youtube_id) != 11:
        ValueError("Invalid youtube id given!")
    ydl_config = {
        'format': "bestaudio/best",
        'outtmpl': f".TEMP/{youtube_id}.%(ext)s",
        'quiet': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_config) as ydl:
            info = ydl.extract_info(f"https://music.youtube.com/watch?v={youtube_id}")

        audio_file = info["requested_downloads"][0]["filepath"]
        title = sanitize(info.get('title', "Unknown Title"))
        artist_list = info.get('artists', [info.get('uploader', "Unknown Artist")])
        artist_str = sanitize(", ".join(artist_list))
        album = sanitize(info.get('album', "Unknown Album"))
        release_year = info.get('release_year') or int(info.get('upload_date', "20000000")[0:4] or 0)
        length = info.get('duration', 0)
        covers = info.get('thumbnails')
        cover_url = info.get('thumbnail')
        if covers and len(covers) > 2:
            if covers[2].get('height', 1) == covers[2].get('width', 0):
                cover_url = covers[2]["url"]

        return {
            "id": youtube_id,
            "title": title,
            "artists": artist_list,
            "artist": artist_str,
            "album": album,
            "release": release_year,
            "length": length,
            "cover_url": cover_url,
            "file_path": audio_file,
        }
    except Exception as e: raise e


# A wrapper function for all the download functions

def download_single(song_dict:dict,folder_name:str = None, callback=None):
    # Download initial file from service
    if song_dict["type"] == "youtube":
        result = download_youtube(song_dict["youtube_id"])
        song_dict["album"] = result["album"]
        song_dict["release"] = result["release"]
        music_filename = result["file_path"]
    if song_dict["type"] == "spotify":
        music_filename = download_spotify(song_dict)

    # Figure out folder name

    with open("../config.json", "r") as f:
        config = json.load(f)

    if folder_name is None:output_folder = config["path"]
    else:
        output_folder = os.path.join(config["path"], folder_name)
        os.makedirs(output_folder, exist_ok=True)
    if callback: callback("transcoding")
    templater_data = {"title": song_dict["title"],"artist":", ".join(song_dict["artists"]),"album":song_dict["album"],"year":song_dict["release"],"length":song_dict["duration_seconds"],"platform":song_dict["type"],"track_number": song_dict["track_number"]}
    final_filename = template_decoder(config["filename_template"], data=templater_data)
    # Transcode
    ffmpeg_out = transcode_audio(music_filename, output_folder,final_filename,quality_preset=config["quality"])
    # Add text based metadata
    if callback: callback("metadata")
    edit_audio_metadata(ffmpeg_out,data=templater_data)
    # Add cover
    if song_dict.get("thumbnail"):
        cover_path = os.path.join(".TEMP", f"cover_{sanitize(song_dict['title'])}.jpg")
        cover_file = download_file(song_dict["thumbnail"], cover_path)
        if isinstance(cover_file, str) and os.path.exists(cover_file):
            add_cover_art(ffmpeg_out, cover_file)
            os.remove(cover_file)
    if callback: callback("cleaning")
    os.remove(music_filename)
    if callback: callback("done")