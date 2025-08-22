import os
import subprocess
import time
from enum import Enum

import requests

import executables
from config import Config


class Errors(Enum):
    FFMPEG_SCREENSHOT_ERROR = 1
    MPV_SCREENSHOT_ERROR = 2
    FFMPEG_AUDIO_ERROR = 3
    MPV_AUDIO_ERROR = 4


class AnkiExporter:
    class ExportError(Exception):
        pass

    def __init__(self, config: Config, executables: executables.Executables):
        self.ffmpeg_executable = executables.ffmpeg
        self.image_format = config.anki_image_format
        self.audio_format = config.anki_audio_format
        self.image_width = config.anki_image_width
        self.image_height = config.anki_image_height
        # Use mpv_external by default, but it might be changed once MPV loads
        self.mpv_executable = executables.mpv_external
        self.sentence_meaning_field = config.sentence_meaning_field
        self.sentence_audio_field = config.sentence_audio_field
        self.picture_field = config.picture_field

    def _invoke_anki_connect(self, action, **params):
        try:
            r = requests.post(F'http://127.0.0.1:8765', json={'action': action, 'version': 6, 'params': params})
            r.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise self.ExportError(
                'Could not connect to Anki.\nMake sure Anki is running and the latest AnkiConnect add-on is installed.')
        response = r.json()
        if len(response) != 2:
            raise self.ExportError('Invalid response from AnkiConnect.')
        if response['error'] is not None:
            raise self.ExportError(response['error'])
        return response['result']

    def get_last_added_notes(self):
        return sorted(self._invoke_anki_connect('findNotes', query='added:1'), reverse=True)

    def get_media_path(self):
        return self._invoke_anki_connect('getMediaDirPath')

    def update_last_note(self, media_file, audio_track, text_primary, text_secondary, time_start, time_end):
        # Warning: You must not be viewing the note that you are updating on your Anki browser, otherwise the fields
        # will not update. See this issue for further details: https://github.com/FooSoft/anki-connect/issues/82
        self._invoke_anki_connect('guiBrowse', query='nid:1')

        last_notes = self.get_last_added_notes()
        if len(last_notes) == 0:
            raise self.ExportError('No recently created notes. Please add a note first.')

        if not media_file.startswith('http'):
            media_file = os.path.normpath(media_file)

        # Prepare base file name and fetch media collection path
        file_base = 'mpv-' + str(int(round(time.time() * 1000)))
        anki_media_collection_path = self.get_media_path()

        # Get image
        img_name = file_base + '.' + self.image_format
        img_path = os.path.join(anki_media_collection_path, img_name)
        img_path = os.path.normpath(img_path)
        error = self.make_screenshot(media_file, time_start, time_end, img_path)
        if error:
            raise self.ExportError('Generating image failed: ' + str(error))

        # Get audio
        audio_name = file_base + '.' + self.audio_format
        audio_path = os.path.join(anki_media_collection_path, audio_name)
        audio_path = os.path.normpath(audio_path)
        error = self.make_audio(media_file, audio_track, time_start, time_end, audio_path)
        if error:
            raise self.ExportError('Generating audio failed: ' + str(error))

        # Make sure that the files were created
        if not os.path.exists(img_path) or not os.path.exists(audio_path):
            raise self.ExportError('Generating image/audio failed.')

        # Prepare fields to update
        fields = {}
        if self.sentence_meaning_field:
            fields[self.sentence_meaning_field] = text_secondary
        if self.sentence_audio_field:
            fields[self.sentence_audio_field] = '[sound:' + audio_name + ']'
        if self.picture_field:
            fields[self.picture_field] = '<img src="' + img_name + '">'
        if len(fields) == 0:
            raise self.ExportError('No fields to update.')

        # Update the last note
        self._invoke_anki_connect('updateNoteFields', note={
            "id": last_notes[0],
            "fields": fields,
        })

        # Browse to it
        self._invoke_anki_connect('guiBrowse', query='nid:' + str(last_notes[0]))

    def ffmpeg_audio(self, media_file, audio_track, start, end, out_path):
        args = [
            self.ffmpeg_executable,
            '-y', '-loglevel', 'error',
            '-ss', str(start),
            '-to', str(end),
            '-i', media_file,
            '-map', '0:' + str(audio_track),
            '-acodec', 'mp3',
            out_path
        ]

        try:
            proc = subprocess.Popen(args)
            proc.wait()
        except FileNotFoundError:
            return Errors.FFMPEG_AUDIO_ERROR

        # Check that image was saved
        if not os.path.exists(out_path):
            return Errors.FFMPEG_AUDIO_ERROR
        return None

    def mpv_audio(self, media_file, audio_track, start, end, out_path):
        if not self.mpv_executable:
            return Errors.MPV_AUDIO_ERROR

        args = [self.mpv_executable, '--load-scripts=no',  # start mpv without scripts
                media_file, '--loop-file=no', '--video=no', '--no-ocopy-metadata', '--no-sub',  # just play audio
                '--aid=' + str(audio_track),
                '--start=' + str(start), '--end=' + str(end),
                '--o=' + out_path]

        proc = subprocess.Popen(args)
        proc.wait()

        # Check that image was saved
        if not os.path.exists(out_path):
            return Errors.FFMPEG_SCREENSHOT_ERROR
        return None

    def make_audio(self, media_file, audio_track, start, end, out_path):
        # Default to using ffmpeg for audio
        error = self.ffmpeg_audio(media_file, audio_track, start, end, out_path)

        # Fall back to mpv if ffmpeg fails
        if error is not None:
            print("AUDIO: Falling back to mpv")
            error = self.mpv_audio(media_file, audio_track, start, end, out_path)
        return error

    def ffmpeg_screenshot(self, media_file, start, end, out_path):
        args = [
            self.ffmpeg_executable,
            '-y', '-loglevel', 'error',
            '-ss', str((start + end) / 2),
            '-i', media_file,
            '-vframes', '1',
            out_path
        ]

        # See https://ffmpeg.org/ffmpeg-filters.html#scale-1 for scaling options

        # None or values smaller than 1 set the axis to auto
        w = self.image_width
        if w is None or w < 1:
            w = -1
        h = self.image_height
        if h is None or h < 1:
            h = -1

        # Only apply filter if any axis is set to non-auto
        if w > 0 or h > 0:
            args[-1:-1] = [
                '-filter:v',
                'scale=w=\'min(iw,%d)\':h=\'min(ih,%d)\':force_original_aspect_ratio=decrease'
                % (w, h)
            ]

        try:
            proc = subprocess.Popen(args)
            proc.wait()
        except FileNotFoundError:
            return Errors.FFMPEG_SCREENSHOT_ERROR

        # Check that image was saved
        if not os.path.exists(out_path):
            return Errors.FFMPEG_SCREENSHOT_ERROR
        return None

    def mpv_screenshot(self, media_file, start, end, out_path):
        if not self.mpv_executable:
            return Errors.MPV_SCREENSHOT_ERROR

        args = [self.mpv_executable, '--load-scripts=no',  # start mpv without scripts
                media_file, '--loop-file=no', '--audio=no', '--no-ocopy-metadata', '--no-sub',  # just play video
                '--frames=1',  # for one frame
                '--start=' + str((start + end) / 2),  # start in the middle
                '--o=' + out_path]

        # See https://ffmpeg.org/ffmpeg-filters.html#scale-1 for scaling options

        # None or values smaller than 1 set the axis to auto
        w = self.image_width
        if w is None or w < 1:
            w = -1
        h = self.image_height
        if h is None or h < 1:
            h = -1

        # Only apply filter if any axis is set to non-auto
        if w > 0 or h > 0:
            # best would be 'min(iw,w)' but mpv doesn't allow passing filters with apostrophes
            scale_arg = '--vf-add=scale=w=%d:h=%d:force_original_aspect_ratio=decrease' % (w, h)
            args.append(scale_arg)

        proc = subprocess.Popen(args)
        proc.wait()

        # Check that image was saved
        if not os.path.exists(out_path):
            return Errors.MPV_SCREENSHOT_ERROR
        return None

    def make_screenshot(self, media_file, start, end, out_path):
        # Default to using ffmpeg for screenshots
        error = self.ffmpeg_screenshot(media_file, start, end, out_path)

        # Fall back to mpv if ffmpeg fails
        if error is not None:
            print("SCREENSHOT: Falling back to mpv")
            error = self.mpv_screenshot(media_file, start, end, out_path)
        return error
