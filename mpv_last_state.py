from dataclasses import dataclass, field

from subtitle_manager import Sub


@dataclass
class MpvLastState:
    media_path: str = None
    audio_track: int = -1
    subs_delay: int = 0
    resx: int = 1920
    resy: int = 1080
    subs: list[Sub] = field(default_factory=lambda: [])
    secondary_subs: list[Sub] = field(default_factory=lambda: [])
