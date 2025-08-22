import os
import platform
import shutil

from config import Config


class Executables:
    def __init__(self, plugin_dir_path: str, config: Config):
        self.ffmpeg = Executables._find_executable(plugin_dir_path, config, 'ffmpeg')
        self.ffsubsync = Executables._find_executable(plugin_dir_path, config, 'ffsubsync')
        self.mpv_external = Executables._find_executable(plugin_dir_path, config, 'mpv', 'mpv_path')

    @staticmethod
    def _find_executable(plugin_dir_path: str, config: Config, executable_name: str, config_name=None):
        if config_name is None:
            config_name = executable_name

        # Check if defined in config and exists
        if config_name in vars(config):
            config_exe_path = getattr(config, config_name)
            if config_exe_path is not None and os.path.isfile(config_exe_path):
                return config_exe_path

        check_paths = [
            os.path.join(plugin_dir_path, executable_name, executable_name),
            os.path.join(plugin_dir_path, executable_name),
        ]

        # On Windows also check for .exe files
        if platform.system() == 'Windows':
            for cp in check_paths.copy():
                check_paths.append(cp + '.exe')

        for cp in check_paths:
            if os.path.isfile(cp):
                return cp
        return shutil.which(executable_name)  # Set to none when not found
