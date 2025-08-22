import collections
import json
import os
import platform
import queue
import shutil
import sys
import threading
import time
import traceback
import typing
import webbrowser

import psutil

import subtitle_manager
import utils.browser_support as browser_support
from config import Config
from executables import Executables
from mpv_last_state import MpvLastState
from queue_handler import QueueHandler
from subtitle_manager import load_subs_from_info, SubtitleLoadError
from utils.ankiexport import AnkiExporter
from utils.mpv_ipc import MpvIpc
from utils.server import HttpServer, HttpResponse

# Plugin dir
if getattr(sys, 'frozen', False):
    plugin_dir = os.path.dirname(sys.executable)
else:
    plugin_dir = os.path.dirname(os.path.abspath(__file__))

# Temporary directory to store stuff, gets cleared on startup and shutdown
tmp_dir = os.path.join(plugin_dir, 'tmp')

# Log file handle, None if not used (in dev mode we use stdout/stderr)
log_file: typing.TextIO | None = None

# MPC IPC object
mpv: MpvIpc

# Config-like objects
config: Config
executables: Executables

# Anki exporter object
anki_exporter: AnkiExporter

# Server
server: HttpServer | None = None

# Last state of MPV, used to store the last imported media and subtitle tracks
mpv_last_state: MpvLastState = MpvLastState()

# Queue handler for all the data streams
queue_handler = QueueHandler()

# Last time a subtitle request was made, used to determine if we should force open a new tab
last_subs_request = 0


### Handlers for GET requests

# Handler to provide main subtitles
def get_handler_subs(socket):
    global last_subs_request
    last_subs_request = time.time()

    # Turn them into json
    subs_json = json.dumps(mpv_last_state.subs, default=vars)
    r = HttpResponse(content=subs_json.encode(), content_type='text/html')
    r.send(socket)


# Handler to provide secondary subtitles
def get_handler_secondary_subs(socket):
    global last_subs_request
    last_subs_request = time.time()

    # Turn them into json
    secondary_subs_json = json.dumps(mpv_last_state.secondary_subs, default=vars)
    r = HttpResponse(content=secondary_subs_json.encode(), content_type='text/html')
    r.send(socket)


# Event source registration handler for data streams
def get_handler_data(socket):
    r = HttpResponse(content_type='text/event-stream', headers={'Cache-Control': 'no-cache'})
    r.send(socket)

    q = queue.Queue()

    # Register the queue to the queue handler
    queue_handler.add_queue(q)

    keep_listening = True
    while keep_listening:
        data = q.get()

        if len(data) < 1:
            keep_listening = False
        else:
            cmd = data[0]

            if cmd in ['s', 'r']:
                send_msg = 'data: ' + data + '\r\n\r\n'
                try:
                    socket.sendall(send_msg.encode())
                except:
                    keep_listening = False
            else:
                keep_listening = False

        q.task_done()

    # Remove the queue from the queue handler
    queue_handler.remove_queue(q)


### Handlers for POST requests

# Handler to update last added Anki card
def post_handler_anki(socket, data):
    r = HttpResponse()
    r.send(socket)

    if mpv_last_state.audio_track < 0:
        mpv.show_text('Please select an audio track before opening Migaku MPV if you want to export Anki cards.')
        return

    # Get the provided card
    cards = json.loads(data.decode())
    if len(cards) != 1:
        mpv.show_text('Please select only one card to export to Anki.', 8.0)
        return

    # Fetch the card data to apply to the last note
    card = cards[0]
    text = card['text']
    translation_text = card['translation_text']
    start = card['start'] / 1000.0
    end = card['end'] / 1000.0

    try:
        anki_exporter.update_last_note(
            mpv_last_state.media_path, mpv_last_state.audio_track, text, translation_text, start, end)
        mpv.show_text('Last card updated successfully.', 8.0)
    except AnkiExporter.ExportError as e:
        mpv.show_text('Exporting card failed:\n\n' + str(e), 8.0)


# Handler to control MPV from the browser (hate how we just forward everything to MPV IPC)
def post_handler_mpv_control(socket, data):
    mpv.send_json_txt(data.decode())

    r = HttpResponse()
    r.send(socket)


### Managing data streams

def send_subtitle_time(arg):
    # Send the current subtitle time to the browser + delay
    time_millis = (int(round(float(arg) * 1000)) + mpv_last_state.subs_delay) // 10 * 10
    queue_handler.send_data('s' + str(time_millis))


def open_webbrowser_new_tab():
    url = 'http://' + str(server.host) + ':' + str(server.port)
    browser_exec = None if config.browser == 'default' else browser_support.expand_browser_name(config.browser)
    try:
        webbrowser.get(browser_exec).open(url, new=0, autoraise=True)
    except:
        mpv.show_text(
            'Warning: Opening the subtitle browser with configured browser failed.\n\nPlease review your config.')
        webbrowser.open(url, new=0, autoraise=True)


def tab_reload_timeout():
    time.sleep(config.reuse_last_tab_timeout)

    if last_subs_request < (time.time() - (config.reuse_last_tab_timeout + 0.25)):
        print('BRS: Tab timed out.')
        open_webbrowser_new_tab()


### Called when user presses the migaku key in mpv, transmits info about playing environment

# TODO: Split this
def load_and_open_migaku(mpv_pid, mpv_media_path, mpv_audio_track, mpv_sub_info, mpv_secondary_sub_info,
                         mpv_subs_delay, mpv_resx, mpv_resy):
    global mpv_last_state

    if server is None:
        mpv.show_text('Initializing server still... Please wait.')
        return

    if not mpv_sub_info:
        mpv.show_text('Please select a subtitle track.')
        return

    # Update the mpv executable in Anki exporter. Prefer local mpv executable instead of external one, which might not
    # even exist.
    mpv_process_executable = psutil.Process(int(mpv_pid)).cmdline()[0]
    if os.path.split(mpv_process_executable)[-1].lower() in ['mpv', 'mpv.exe', 'mpv.com', 'mpv-bundle']:
        anki_exporter.mpv_executable = mpv_process_executable
    else:
        if not executables.mpv_external:
            mpv.show_text('Please set mpv_path in the config file.')
            return

    # Store received state
    mpv_last_state = MpvLastState(
        mpv_media_path, int(mpv_audio_track), int(round(float(mpv_subs_delay) * 1000)),
        int(mpv_resx), int(mpv_resy), [], [])

    # Load main subs
    try:
        mpv_last_state.subs = load_subs_from_info(
            mpv, tmp_dir, executables, config, mpv_media_path, mpv_sub_info,
            mpv_last_state.subs_delay)
    except SubtitleLoadError as e:
        mpv.show_text(str(e))
        return

    # Load secondary subs
    if mpv_secondary_sub_info:
        try:
            mpv_last_state.secondary_subs = load_subs_from_info(
                mpv, tmp_dir, executables, config, mpv_media_path,
                mpv_secondary_sub_info, mpv_last_state.subs_delay)
        except SubtitleLoadError:
            pass

    # Open or refresh frontend
    open_or_refresh_frontend()


def open_or_refresh_frontend():
    mpv.show_text('Opening in Browser...', 2.0)

    open_new_tab = False

    with queue_handler.data_queues_lock:
        finalize_queues = queue_handler.data_queues

        if config.reuse_last_tab and len(queue_handler.data_queues) > 0:
            # Refresh last opened queue
            queue_handler.data_queues[-1].put('r')
            # Remove it from the finalize queues (all but last)
            finalize_queues = finalize_queues[:-1]
            # Start a timeout thread to open a new tab if no new request comes in within the timeout
            t = threading.Thread(target=tab_reload_timeout)
            t.start()
        else:
            open_new_tab = True

        # Disconnect all the finalize queues
        for q in finalize_queues:
            q.put('q')

    if open_new_tab:
        open_webbrowser_new_tab()


def resync_subtitle(resync_sub_path, resync_reference_path, resync_reference_track):
    if executables.ffmpeg is None:
        mpv.show_text('Subtitle syncing requires ffmpeg to be located in the plugin directory.')
        return

    mpv.show_text('Syncing subtitles to reference track. Please wait...', duration=150.0)

    # Run actual syncing in thread
    def sync_thread_func():
        synced_path = subtitle_manager.resync_subtitle(tmp_dir, executables, resync_sub_path, resync_reference_path,
                                                       resync_reference_track)
        if synced_path is not None:
            mpv.command('sub-add', synced_path)
            mpv.show_text('Syncing finished.')
        else:
            mpv.show_text('Syncing failed.')

    # Start the thread
    t = threading.Thread(target=sync_thread_func)
    t.start()


def exception_hook(exc_type, exc_value, exc_traceback):
    print('--------------')
    print('UNHANDLED EXCEPTION OCCURED:\n')
    print('Platform:', platform.platform())
    print('Python:', sys.version.replace('\n', ' '))
    traceback_strs = traceback.format_exception(exc_type, exc_value, exc_traceback)
    traceback_str = ''.join(traceback_strs)
    print(traceback_str)
    print('EXITING')

    # What folllows is pretty dirty, but all threads need to die and I'm lazy right now
    # TODO

    try:
        sys.stdout.flush()
        sys.stderr.flush()

        if log_file:
            log_file.flush()
            log_file.close()
    except:
        pass

    os._exit(1)


def exception_hook_threads(args):
    exception_hook(args.exc_type, args.exc_value, args.exc_traceback)


def install_except_hooks():
    sys.excepthook = exception_hook

    if hasattr(threading, 'excepthook'):
        threading.excepthook = exception_hook_threads

    else:
        run_old = threading.Thread.run

        LegacyExceptHookArgs = collections.namedtuple('LegacyExceptHookArgs', 'exc_type exc_value exc_traceback thread')

        def run_new(*args, **kwargs):
            try:
                run_old(*args, **kwargs)
            except:
                exception_hook_threads(LegacyExceptHookArgs(*sys.exc_info(), None))

        threading.Thread.run = run_new


def main():
    global log_file
    global mpv
    global config
    global executables
    global anki_exporter
    global server

    install_except_hooks()

    # Load config
    config_path = plugin_dir + '/migaku_mpv.ini'
    if len(sys.argv) >= 3:
        config_path = sys.argv[2]
    config = Config()
    config.load(config_path)

    # Redirect stdout/stderr to log file if built for release
    if not config.dev_mode:
        print('Redirecting stout and stderr to log.txt...')
        log_file = open(plugin_dir + '/log.txt', 'w', encoding='utf8')
        sys.stdout = log_file
        sys.stderr = log_file

    print('ARGS:', sys.argv)
    print('CONFIG', vars(config))

    # Check command line args
    if len(sys.argv) != 2:
        print('ARGS: Usage: %s mpv-ipc-handle' % sys.argv[0])
        return

    # Clear/create temp dir
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)

    # Find executables
    executables = Executables(plugin_dir, config)
    print('EXES:', vars(executables))

    # Init Anki exporter
    anki_exporter = AnkiExporter(config, executables)
    print('ANKI:', vars(anki_exporter))

    # Init mpv IPC
    mpv = MpvIpc(sys.argv[1])

    # Setup server
    server = HttpServer(config.host, range(config.port, config.port_max + 1))
    server.set_get_file_server('/', plugin_dir + '/migaku_mpv.html')
    for path in ['/icons/migakufavicon.png', '/icons/anki.png', '/icons/bigsearch.png']:
        server.set_get_file_server(path, plugin_dir + path)
    server.set_get_handler('/subs', get_handler_subs)
    server.set_get_handler('/secondary_subs', get_handler_secondary_subs)
    server.set_get_handler('/data', get_handler_data)
    server.set_post_handler('/anki', post_handler_anki)
    server.set_post_handler('/mpv_control', post_handler_mpv_control)
    server.open()

    # Main loop, exits when IPC connection closes
    for data in mpv.listen():
        print('MPV:', data)
        if ('event' in data) and (data['event'] == 'client-message'):
            event_args = data.get('args', [])
            if len(event_args) >= 2 and event_args[0] == '@migaku':
                cmd = event_args[1]
                if cmd == 'sub-start':
                    send_subtitle_time(event_args[2])
                elif cmd == 'open':
                    load_and_open_migaku(*event_args[2:9 + 1])
                elif cmd == 'resync':
                    resync_subtitle(*event_args[2:4 + 1])

    # Close server
    server.close()

    # Disconnect all queues
    queue_handler.send_data('q')

    # Close mpv IPC
    mpv.close()

    # Delete temp dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
