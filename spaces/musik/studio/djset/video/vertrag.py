"""Der Vertrag der Videomusik: was das Modell waehlt, was Laura bekommt.

LauraClip spiegelt den Body von Lauras `audio-clips` samt ihren Grenzen, damit
ein ungueltiger Clip hier scheitert und nicht erst als HTTP-Fehler bei Laura.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from djset.video.laenge import VideoFehler
from djset.video.zuschnitt import Einstieg


class VideoWahl(BaseModel):
    song_id: str = Field(min_length=1)
    einstieg: Einstieg
    gesang: bool
    begruendung: str = Field(min_length=1)


class LauraClip(BaseModel):
    seq_in_frame: int = Field(default=0, ge=0)
    seq_out_frame_exclusive: int = Field(ge=0)
    asset_in_frame: int = Field(default=0, ge=0)
    gain_percent: int = Field(default=100, ge=0, le=400)
    fade_in_frames: int = Field(default=0, ge=0)
    fade_out_frames: int = Field(default=0, ge=0)
    mix_mode: Literal["mix", "replace_original", "mute_original"] = "mix"
    ducking_percent: int = Field(default=100, ge=0, le=100)
    label: str

    @model_validator(mode="after")
    def _ende_nach_anfang(self) -> "LauraClip":
        if self.seq_out_frame_exclusive <= self.seq_in_frame:
            raise ValueError("seq_out_frame_exclusive muss hinter seq_in_frame liegen")
        return self


class VideomusikErgebnis(BaseModel):
    version: Literal[1] = 1
    datei: str  # Name im Lauf, videomusik.wav
    mp3: str | None
    sekunden: float
    samples: int
    sample_rate: int
    frames: int | None
    fps_num: int | None
    fps_den: int | None
    song_id: str
    titel: str
    herkunft: str
    start_im_song_s: float
    schluss_s: float  # im Stueck, nicht im Song
    takte: int
    bpm: float
    gesang: bool
    lufs: float
    true_peak: float
    laura: LauraClip | None  # nur wenn die Frames bekannt sind


def laura_clip(frames: int, fps_num: int, fps_den: int, label: str) -> LauraClip:
    """Der Clip, der die ganze Videolaenge mit der Musik belegt.

    fade_out_frames bleibt 0: der Ausklang steckt schon in der Datei, ein
    zweites Ausblenden wuerde ihn doppelt absenken. ducking_percent 100 laesst
    den Originalton, wie er ist - das Absenken unter Sprache entscheidet Plan 3.
    fps_num/fps_den sind nicht Teil des Clips; sie stehen im Aufruf, damit
    dort steht, in welcher Rate die Frames gezaehlt sind.
    """
    if fps_num <= 0 or fps_den <= 0:
        raise VideoFehler(f"Ungueltige Bildrate {fps_num}/{fps_den}")
    return LauraClip(seq_out_frame_exclusive=frames, label=label)
