import os
import re
import musicbrainzngs
from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC
from mutagen.mp3 import MP3

class MetadataManager:
    def __init__(self):
        musicbrainzngs.set_useragent("MusicDownloaderApp", "2.0", "email@example.com")

    def search_track(self, query):
        try:
            artist_query = None
            recording_query = query
            
            if " - " in query:
                parts = query.split(" - ", 1)
                artist_query = parts[0].strip()
                recording_query = parts[1].strip()
                result = musicbrainzngs.search_recordings(artist=artist_query, recording=recording_query, limit=5)
            else:
                result = musicbrainzngs.search_recordings(query=query, limit=5)

            if not result.get('recording-list'):
                return None

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


    def apply_metadata(self, filepath, search_query, logger_func):
        if not os.path.exists(filepath) or not filepath.lower().endswith(".mp3"):
            return

        logger_func(f"Searching metadata for: '{search_query}'...", "META")
        track_data = self.search_track(search_query)

        if not track_data:
            logger_func("IDK", "META")
            return

        try:
            audio = MP3(filepath, ID3=ID3)
            if audio.tags is None: audio.add_tags()

            audio.tags.add(TPE1(encoding=3, text=[track_data['artist']]))
            audio.tags.add(TIT2(encoding=3, text=[track_data['title']]))
            audio.tags.add(TALB(encoding=3, text=[track_data['album']]))
            if track_data['year']:
                audio.tags.add(TDRC(encoding=3, text=[track_data['year']]))


            audio.save()
            logger_func(f"Metadata updated: {track_data['artist']} - {track_data['title']}", "SUCCESS")
        except Exception as e:
            logger_func(f"Metadata write error: {e}", "ERROR")
