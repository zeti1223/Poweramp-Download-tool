import os
import re
import requests
import musicbrainzngs
from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC, APIC
from mutagen.mp3 import MP3
from consts import USER_AGENT_STRING

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
