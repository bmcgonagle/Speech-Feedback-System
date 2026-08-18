#Brady McGonagle
#Version 1.0
#8/17/2026

#Handles: wav, flac, ogg (soundfile) and m4a, mp3, mp4, mov, webm (ffmpeg).
#Video containers work because ffmpeg just ignores the video stream.

import os
import shutil
import subprocess
import tempfile

import numpy as np

SAMPLERATE = 16000

# What ffmpeg will realistically be asked for
AUDIO_EXTS = {".wav", ".flac", ".ogg", ".opus", ".mp3", ".m4a", ".aac",
              ".wma", ".mp4", ".mov", ".webm", ".mkv", ".avi"}


class DecodeError(RuntimeError):
    pass


def load_audio(path, samplerate=SAMPLERATE):
    
    #Return a 1-D float32 numpy array in [-1, 1] at `samplerate`.
    
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise DecodeError(f"No such file: {path}")
    if os.path.getsize(path) == 0:
        raise DecodeError(f"File is empty: {path}")

    audio = _try_scipy_wav(path, samplerate)
    if audio is None:
        audio = _try_soundfile(path, samplerate)
    if audio is None:
        audio = _try_ffmpeg(path, samplerate)

    if audio.size == 0:
        raise DecodeError(f"Decoded 0 samples from {path} - no audio stream?")
    return audio


def _try_scipy_wav(path, samplerate):
    
    #Plain WAV via scipy - no extra dependency, since the project already needs
    #scipy to write session files. Returns None for anything scipy can't parse
    
    if not path.lower().endswith(".wav"):
        return None
    try:
        import scipy.io.wavfile as wav
        sr, data = wav.read(path)
    except Exception:
        return None

    #scipy returns int16/int32/uint8/float32 depending on the file's encoding
    if data.dtype.kind == "i":
        mono = data.astype(np.float32) / float(2 ** (8 * data.dtype.itemsize - 1))
    elif data.dtype.kind == "u":                       #8-bit wav is unsigned
        mono = (data.astype(np.float32) - 128.0) / 128.0
    else:
        mono = data.astype(np.float32)

    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    if sr != samplerate:
        mono = _resample(mono, sr, samplerate)
    return mono.astype(np.float32)


def _try_soundfile(path, samplerate):
    try:
        import soundfile as sf
    except ImportError:
        return None

    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
    except Exception:
        return None

    mono = data.mean(axis=1)
    if sr != samplerate:
        mono = _resample(mono, sr, samplerate)
    return mono.astype(np.float32)


def _try_ffmpeg(path, samplerate):
    if not shutil.which("ffmpeg"):
        raise DecodeError(
            f"Cannot decode {os.path.basename(path)}. "
            f"Install ffmpeg (brew install ffmpeg / apt install ffmpeg), "
            f"or pip install soundfile for wav/flac/ogg only."
        )

    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", path,
             "-vn",                      # drop video stream if mp4/mov
             "-ac", "1",                 # mono
             "-ar", str(samplerate),     # resample
             "-f", "wav", tmp],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise DecodeError(
                f"ffmpeg failed on {os.path.basename(path)}:\n"
                f"{proc.stderr.strip()[:400]}"
            )
        import scipy.io.wavfile as wav
        _, data = wav.read(tmp)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return (data.astype(np.float32) / 32768.0)
    finally:
        os.unlink(tmp)


def _resample(x, sr_in, sr_out):
    """Resample with scipy if available, else linear interpolation."""
    n = int(round(len(x) * sr_out / sr_in))
    try:
        import scipy.signal as sig
        return sig.resample(x, n)
    except ImportError:
        old = np.linspace(0, 1, len(x), endpoint=False)
        new = np.linspace(0, 1, n, endpoint=False)
        return np.interp(new, old, x)


def duration(audio, samplerate=SAMPLERATE):
    """Seconds of audio. Always measure this, never trust file metadata."""
    return len(audio) / samplerate


def find_audio(folder):
    """List decodable files in a folder, sorted. For batch ingestion."""
    folder = os.path.expanduser(folder)
    hits = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
            if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
    return hits