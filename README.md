# Speech Feedback System

A tool to help you with all your speech needs. Records and reports your speaking pace, 
and how often you use filler words such as "like" or "um". Built an app to track multiple 
sessions to help you improve over time.

## Setup

Run: 
pip install numpy scipy sounddevice faster-whisper


For most file types that you import in, you will need ffmpeg
it's a system binary, not a pip package:

- **Windows:** `winget install Gyan.FFmpeg`, then open a new terminal
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg`


## Running it
Run:
python app.py


Record, add a file, or add a whole folder. Click a session to see its stats and
transcript.

## Notes
- First run downloads the Whisper model (~140 MB). It looks frozen — it isn't.
- Recording stops on its own after a few seconds of silence.
- Settings live at the top of `speech_test_V1.py` — filler word lists, silence
  threshold, and the 5-minute recording cap.
- Data is stored in `~/.speech_coach.db`, audio in `~/speech_sessions/`.
