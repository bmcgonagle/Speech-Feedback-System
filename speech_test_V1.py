#Brady McGonagle
#Version 1.0
#8/17/2026

#Actual file for listening, transcribing, and displaying audio stats

import argparse
import os
import queue
import re
from datetime import datetime

import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wav

import storage

#Settings
DEVICE = None
SAMPLERATE = 16000
BLOCK = 0.1            #Seconds per audio chunk
PLAYBACK = False
WAV_FILE = "session.wav"
RECORDINGS = os.path.expanduser("~/speech_sessions")

#Stopping Detection
SILENCE_DB = -45       
SILENCE_STOP = 3.5     # stop after this much continuous quiet
MAX_DURATION = 300     # safety cap in seconds


FILLERS = ["um", "uh", "er", "erm", "ah", "hmm"]
MARKERS = ["like", "basically", "actually", "you know", "i mean"]


# Record audio in chunks, returning the full sample array and a list of speech flags.
def record(max_duration=MAX_DURATION):
    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"audio warning: {status}")
        q.put(indata[:, 0].copy())

    chunks = []
    silence_run = 0
    started = False

    with sd.InputStream(device=DEVICE, channels=1, samplerate=SAMPLERATE,
                        blocksize=int(SAMPLERATE * BLOCK), dtype="float32",
                        callback=callback):
        print(f"Recording. Go quiet for {SILENCE_STOP:.0f}s to finish.")
        try:
            while len(chunks) * BLOCK < max_duration:
                block = q.get()
                chunks.append(block)

                level = 20 * np.log10(np.sqrt(np.mean(block ** 2)) + 1e-10)
                
                #Stop detection
                if level > SILENCE_DB:
                    started = True
                    silence_run = 0
                else:
                    silence_run += 1

                if started and silence_run * BLOCK >= SILENCE_STOP:
                    break

                if len(chunks) % 10 == 0:
                    print(f"  {len(chunks) * BLOCK:.0f}s", end="\r", flush=True)
        except KeyboardInterrupt:
            print("\nStopped early.")

    if not chunks:                      # Ctrl-C before any audio arrived
        return np.zeros(0, dtype="float32")

    audio = np.concatenate(chunks)

    trim = int(silence_run * BLOCK * SAMPLERATE)   # drop the trailing silence
    if 0 < trim < len(audio):
        audio = audio[:-trim]
    return audio


#One file per session so recordings accumulate instead of overwriting
def session_path():
    os.makedirs(RECORDINGS, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    return os.path.join(RECORDINGS, f"{stamp}.wav")


#Saves audio to an output wav file
def save(audio, path=WAV_FILE):

    wav.write(path, SAMPLERATE, np.int16(np.clip(audio, -1, 1) * 32767))


def transcribe(path=WAV_FILE):

    try:
        #neccessary for transcription
        from faster_whisper import WhisperModel
        model = WhisperModel("base.en", device="cpu", compute_type="int8")
        
        segments, _ = model.transcribe(
            path, beam_size=5,
            initial_prompt="Um, so, uh, this transcript keeps every um and uh.")
        return " ".join(s.text.strip() for s in segments), True, "whisper"
    
    #Exception handling
    except ImportError:
        pass
    import speech_recognition as sr
    r = sr.Recognizer()
    with sr.AudioFile(path) as source:
        audio = r.record(source)
    try:
        return r.recognize_google(audio), False, "google"
    except sr.UnknownValueError:
        print("Could not understand audio.")
    except sr.RequestError as e:
        print(f"Recognition failed: {e}")
    return "", False, "google"


def count_fillers(text):

    #Convert everything to lower to avoid issues later
    text = text.lower()
    words = re.findall(r"[\w']+", text) #Makes sure words with apostrophes/underscores don't get split
    detail = {}

    #Find fillers
    fillers = 0
    for f in FILLERS:
        n = words.count(f)
        if n:
            detail[f] = n
        fillers += n

    #Find markers
    markers = 0
    for m in MARKERS:
        n = len(re.findall(r"\b" + m.replace(" ", r"\s+") + r"\b", text))
        if n:
            detail[m] = n
        markers += n

    #Specific case - "so" only counts when it opens a sentence
    for s in re.split(r"[.!?]+", text):
        w = re.findall(r"[\w']+", s)
        
        if w and w[0] == "so":
            detail["so (opener)"] = detail.get("so (opener)", 0) + 1
            markers += 1

    return len(words), fillers, markers, detail


def report(elapsed, text, verbatim):
    total, fillers, markers, detail = count_fillers(text)
    
    #Error check
    if not total:
        print("\nNo words transcribed.")
        return total, fillers, markers, detail

    #Words per minute analysis
    wpm = total / (elapsed / 60)
    verdict = "slow" if wpm < 110 else "fast - slow down" if wpm > 160 else "good"

    #Print Statistics
    print(f"\n{'=' * 46}")
    print(f"  {total} words in {elapsed:.0f}s")
    print(f"{'=' * 46}")
    print(f"  Pace           {wpm:6.0f} wpm  ({verdict})")
    print(f"  Fillers        {fillers:6d}   ({fillers / (elapsed / 60):.1f}/min)")
    print(f"  Markers        {markers:6d}   (context-dependent)")

    if detail:
        print()
        for k, v in sorted(detail.items(), key=lambda x: -x[1]):
            print(f"    {k:<16} {v}")

    if not verbatim and fillers == 0:
        print("\n  Zero filled pauses usually means Google stripped them.")
        print("  pip install faster-whisper for a real count.")

    print(f"\n  {text}\n")
    return total, fillers, markers, detail


#Calls all functions neccesary
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Speaking pace and filler analysis.")
    p.add_argument("mode", nargs="?", default="record",
                   choices=["record", "file", "history"],
                   help="record from mic, analyze a file, or show past sessions")
    p.add_argument("path", nargs="?", help="audio or video file, for 'file' mode")
    p.add_argument("--label", help="optional tag, e.g. standup or demo")
    p.add_argument("--limit", type=int, default=10, help="rows to show in history")
    p.add_argument("--no-log", action="store_true", help="analyze but don't save")
    args = p.parse_args()

    #History needs no audio, so handle it before touching the mic
    if args.mode == "history":
        storage.print_history(storage.connect(), limit=args.limit)
        raise SystemExit

    if args.mode == "file":
        if not args.path:
            p.error("file mode needs a path")
        import audio_io                     # only here: needs ffmpeg
        try:
            audio = audio_io.load_audio(args.path, samplerate=SAMPLERATE)
        except audio_io.DecodeError as e:
            raise SystemExit(str(e))
        source, source_path = "file", os.path.abspath(args.path)
    else:
        audio = record()
        source, source_path = "mic", None

    if audio.size == 0:
        raise SystemExit("No audio captured.")

    #Both paths converge on a wav, since transcribe() reads from disk
    wav_path = session_path()
    save(audio, wav_path)
    print(f"Saved {wav_path}")

    if PLAYBACK and source == "mic":
        sd.play(audio, samplerate=SAMPLERATE)
        sd.wait()

    print("Transcribing...")
    elapsed = len(audio) / SAMPLERATE
    text, verbatim, engine = transcribe(wav_path)
    total, fillers, markers, detail = report(elapsed, text, verbatim)

    #Log it. An empty transcript still gets recorded so gaps stay visible.
    if not args.no_log:
        row_id = storage.log_session(
            storage.connect(), duration_sec=elapsed, source=source,
            source_path=source_path, audio_path=wav_path, engine=engine,
            verbatim=verbatim, transcript=text, word_count=total,
            filler_count=fillers, marker_count=markers, detail=detail,
            label=args.label)
        print(f"  logged as session {row_id}")