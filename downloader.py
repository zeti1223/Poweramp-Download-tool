import subprocess
import json
import re
import yt_dlp
import uuid
import ytmusicapi
import ping3
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os

# Helper functions
def check_network():
    is_online = ping3.ping("1.1.1.1")
    return is_online

def sanitize(s):
    return re.sub(r'[<>:"/\\|?*\']', '', s)

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


# Initial metadata collection functions

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

        with open("config.json", "r") as f:config = json.load(f)

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
    search_query = f"{song_dict['title']} {song_dict['artists'].join(' ')}"
    if not check_network():
        raise ConnectionError("No internet connection!")
    yt_music_api = ytmusicapi.YTMusic()
    result_for_search = yt_music_api.search(search_query,filter="songs",limit=1)
    result = download_youtube(result_for_search[0]["videoId"])

    return
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
    random_id = str(uuid.uuid4())
    try:
        with yt_dlp.YoutubeDL(ydl_config) as ydl:
            info = ydl.extract_info(f"https://music.youtube.com/watch?v={youtube_id}")

        audio_file = info["requested_downloads"][0]["filepath"]
        title = sanitize(info.get('title', "Unknown Title"))
        artist_list = info.get('artists', [info.get('uploader', "Unknown Artist")])
        artist_str = sanitize(", ".join(artist_list))
        album = sanitize(info.get('album', "Unknown Album"))
        release_year = info.get('release_year') or int(info.get('upload_date', "20000000")[0:4] or 0)
        length = info.get('duration', 200)
        covers = info.get('thumbnails')
        if not covers[2].get('height', 1) == covers[2].get('width', 0):
            cover_url = info.get('thumbnail')
        else:
            cover_url = covers[2]["url"]

        return {
            "id": id,
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

def download_single(song_dict:dict):
    pass

