import codecs
import os
import pathlib
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

import cchardet as chardet
import pysubs2
import requests

from config import Config
from executables import Executables
from utils.mpv_ipc import MpvIpc


@dataclass
class Sub:
    text: str
    start: int
    end: int


def _subtitle_path_clean(path: str) -> str:
    if path.startswith('file:'):
        uri_path = urllib.parse.urlparse(path).path
        return urllib.request.url2pathname(uri_path)
    return path


class SubtitleLoadError(Exception):
    pass


def _dump_internal_subs(
        mpv: MpvIpc, tmp_dir: str, executables: Executables, config: Config, media_path: str,
        track: str, sub_codec: str
) -> str:
    if sub_codec in ['subrip', 'ass']:
        if not executables.ffmpeg:
            raise SubtitleLoadError(
                'Using internal subtitles requires ffmpeg to be located in the plugin directory.')
        mpv.show_text('Exporting internal subtitle track...', duration=150.0)  # Next osd message will close it
        if sub_codec == 'subrip':
            sub_extension = 'srt'
        else:
            sub_extension = sub_codec
        sub_path = tmp_dir + '/' + str(pathlib.Path(media_path).stem) + '.' + sub_extension
        args = [executables.ffmpeg, '-y', '-loglevel', 'error', '-i', media_path, '-map', '0:' + track, sub_path]
        try:
            timeout = config.subtitle_export_timeout if config.subtitle_export_timeout > 0 else None
            subprocess.run(args, timeout=timeout)
            if not os.path.isfile(sub_path):
                raise FileNotFoundError
            return sub_path
        except TimeoutError:
            raise SubtitleLoadError('Exporting internal subtitle track timed out.')
        except Exception:
            raise SubtitleLoadError('Exporting internal subtitle track failed.')
    else:
        raise SubtitleLoadError(
            'Selected internal subtitle track is not supported.\n\nOnly SRT and ASS tracks are supported.\n\nSelected track is ' + sub_codec)


def _determine_subs_encoding(subs_path: str) -> str:
    try:
        subs_f = open(subs_path, 'rb')
        subs_data = subs_f.read()
        subs_f.close()

        boms_for_enc = [
            ('utf-32', (codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)),
            ('utf-16', (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)),
            ('utf-8-sig', (codecs.BOM_UTF8,)),
        ]

        for enc, boms in boms_for_enc:
            if any(subs_data.startswith(bom) for bom in boms):
                print('SUBS: Detected encoding (bom):', enc)
                return enc
        else:
            chardet_ret = chardet.detect(subs_data)
            print('SUBS: Detected encoding (chardet):', chardet_ret)
            return chardet_ret['encoding']
    except Exception:
        print('SUBS: Detecting encoding failed. Defaulting to utf-8')
    return 'utf-8'


def load_subs_from_info(
        mpv: MpvIpc, tmp_dir: str, executables: Executables, config: Config, media_path: str,
        sub_info: str, subs_delay: int
) -> list[Sub]:
    # Turn the info into a path
    if '*' in sub_info:
        internal_sub_info = sub_info.split('*')
        if len(internal_sub_info) == 2:
            ffmpeg_track = internal_sub_info[0]
            sub_codec = internal_sub_info[1]
            sub_path = _dump_internal_subs(mpv, tmp_dir, executables, config, media_path, ffmpeg_track, sub_codec)
        else:
            raise SubtitleLoadError('Unknown sub info' + sub_info)
    else:
        sub_path = sub_info

    # Support drag & drop subtitle files on some systems
    sub_path = _subtitle_path_clean(sub_path)

    # Web subtitle?
    is_websub = False
    if sub_path.startswith('edl://'):
        i = sub_path.rfind('http')
        if i >= 0:
            url = sub_path[i:]

            try:
                response = requests.get(url)
                tmp_sub_path = os.path.join(tmp_dir, 'websub_%d.vtt' % round(time.time() * 1000))
                with open(tmp_sub_path, 'wb') as f:
                    f.write(response.content)

                sub_path = tmp_sub_path
                is_websub = True
            except Exception:
                raise SubtitleLoadError('Downloading web subtitles failed.')

    elif sub_path.startswith('http'):
        try:
            response = requests.get(sub_path)
            tmp_sub_path = os.path.join(tmp_dir, 'websub_%d' % round(time.time() * 1000))
            with open(tmp_sub_path, 'wb') as f:
                f.write(response.content)

            sub_path = tmp_sub_path
        except Exception:
            raise SubtitleLoadError('Downloading web subtitles failed.')

    if not os.path.isfile(sub_path):
        print('SUBS Not found:', sub_path)
        raise SubtitleLoadError('The subtitle file "%s" was not found.' % sub_path)

    # Determine subs encoding
    subs_encoding = _determine_subs_encoding(sub_path)

    # Parse subs and generate json for frontend
    try:
        with open(sub_path, encoding=subs_encoding, errors='replace') as fp:
            subs = pysubs2.SSAFile.from_file(fp)
    except Exception:
        raise SubtitleLoadError('Loading subtitle file "%s" failed.' % sub_path)

    subs.sort()
    subs_list = []

    for s in subs:
        text = s.plaintext.strip()

        # Temporary to correct pysubs2 parsing mistakes
        if is_websub:
            text = text.split('\n\n')[0]

        if not config.skip_empty_subs or text.strip():
            sub_start = max(s.start + subs_delay, 0) // 10 * 10
            sub_end = max(s.end + subs_delay, 0) // 10 * 10
            subs_list.append(Sub(text, sub_start, sub_end))

    return subs_list


def resync_subtitle(tmp_dir: str, executables: Executables, resync_sub_path: str, resync_reference_path: str,
                    resync_reference_track: str):
    # Support drag & drop subtitle files on some systems
    resync_sub_path = _subtitle_path_clean(resync_sub_path)

    path_ext_split = os.path.splitext(resync_sub_path)  # [path_without_extension, extension_with_dot]

    name = pathlib.Path(resync_sub_path).stem  # Get file name without extension
    out_base_path = tmp_dir + '/' + name + '-resynced'  # Out path without index or extension
    out_path = out_base_path + path_ext_split[1]

    # If the out path already exists count up until free file is found
    try_i = 1
    while os.path.exists(out_path):
        out_path = out_base_path + '-' + str(try_i) + path_ext_split[1]
        try_i += 1

    # Run resync
    r = subprocess.run(
        [executables.ffsubsync, resync_reference_path, '-i', resync_sub_path, '-o', out_path, '--reftrack',
         resync_reference_track, '--ffmpeg-path', os.path.dirname(executables.ffmpeg)])
    if r.returncode == 0:
        return out_path
    return None
