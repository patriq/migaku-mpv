import configparser


class Config:
    def __init__(self):
        self.browser = "default"
        self.reuse_last_tab = True
        self.reuse_last_tab_timeout = 1.5
        self.host = "localhost"
        self.port = 8080
        self.port_max = 65535
        self.skip_empty_subs = True
        self.subtitle_export_timeout = 0
        self.mpv_path = None
        self.anki_image_width = -1
        self.anki_image_height = -1
        self.anki_image_format = "png"
        self.anki_audio_format = "wav"
        self.dev_mode = False
        # Anki fields
        self.sentence_meaning_field = None
        self.sentence_audio_field = None
        self.picture_field = None

    def load(self, file_path):
        # Load configuration from a file using configparser
        parser = configparser.ConfigParser(allow_unnamed_section=True)
        parser.read(file_path)

        # Load configuration values from the parser
        self.browser = parser.get(configparser.UNNAMED_SECTION, 'browser', fallback=self.browser)
        self.reuse_last_tab = parser.getboolean(configparser.UNNAMED_SECTION, 'reuse_last_tab',
                                                fallback=self.reuse_last_tab)
        self.reuse_last_tab_timeout = parser.getfloat(configparser.UNNAMED_SECTION, 'reuse_last_tab_timeout',
                                                      fallback=self.reuse_last_tab_timeout)
        self.host = parser.get(configparser.UNNAMED_SECTION, 'host', fallback=self.host)
        self.port = parser.getint(configparser.UNNAMED_SECTION, 'port', fallback=self.port)
        self.port_max = parser.getint(configparser.UNNAMED_SECTION, 'port_max', fallback=self.port_max)
        self.skip_empty_subs = parser.getboolean(configparser.UNNAMED_SECTION, 'skip_empty_subs',
                                                 fallback=self.skip_empty_subs)
        self.subtitle_export_timeout = parser.getint(configparser.UNNAMED_SECTION, 'subtitle_export_timeout',
                                                     fallback=self.subtitle_export_timeout)
        self.mpv_path = parser.get(configparser.UNNAMED_SECTION, 'mpv_path', fallback=self.mpv_path)
        self.anki_image_width = parser.getint(configparser.UNNAMED_SECTION, 'anki_image_width',
                                              fallback=self.anki_image_width)
        self.anki_image_height = parser.getint(configparser.UNNAMED_SECTION, 'anki_image_height',
                                               fallback=self.anki_image_height)
        self.anki_image_format = parser.get(configparser.UNNAMED_SECTION, 'anki_image_format',
                                            fallback=self.anki_image_format)
        self.anki_audio_format = parser.get(configparser.UNNAMED_SECTION, 'anki_audio_format',
                                            fallback=self.anki_audio_format)
        self.dev_mode = parser.getboolean(configparser.UNNAMED_SECTION, 'dev_mode', fallback=self.dev_mode)
        self.sentence_meaning_field = parser.get(configparser.UNNAMED_SECTION, 'sentence_meaning_field',
                                                 fallback=self.sentence_meaning_field)
        self.sentence_audio_field = parser.get(configparser.UNNAMED_SECTION, 'sentence_audio_field',
                                               fallback=self.sentence_audio_field)
        self.picture_field = parser.get(configparser.UNNAMED_SECTION, 'picture_field', fallback=self.picture_field)

        # Validate the configuration after loading
        self.validate()

    def validate(self):
        # Ensure max port is greater than min port
        if self.port < 0 or self.port > self.port_max:
            raise ValueError(f"Port {self.port} must be between 0 and {self.port_max}")
