#Brady McGonagle
#Version 1.0
#8/17/2026

#Reads and writes the session log shared with speech_test_V1.py

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import numpy as np

import storage
import speech_test_V1 as engine


#Catch a stale speech_test_V1 at startup instead of mid-worker
def _check_engine():
    missing = [n for n in ("session_path", "save", "transcribe", "count_fillers")
               if not hasattr(engine, n)]
    if missing:
        raise SystemExit(
            "speech_test_V1.py is out of date - missing: " + ", ".join(missing))


BG = "#1e1e24"
PANEL = "#26262e"
FG = "#e8e8ee"
MUTED = "#8b8b9a"
ACCENT = "#5b9dd9"
WARN = "#d98b5b"
REC = "#d95b5b"


class SpeechApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Speech Feedback System")
        self.geometry("1080x680")
        self.minsize(880, 560)
        self.configure(bg=BG)

        self.conn = storage.connect()
        self.jobs = queue.Queue()       # worker threads -> UI thread
        self.rows = []
        self.busy = False
        self.stop_flag = threading.Event()

        self._style()
        self._build()
        self._reconcile()               # silent; keeps old sessions comparable
        self.refresh()
        self.after(80, self._pump)

    #App setup
    def _style(self):
        
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=FG, rowheight=26, borderwidth=0)
        s.configure("Treeview.Heading", background=BG, foreground=MUTED,
                    borderwidth=0, relief="flat")
        s.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", "#ffffff")])
        s.configure("TButton", background=PANEL, foreground=FG, borderwidth=0,
                    padding=(12, 7), focuscolor=PANEL)
        s.map("TButton", background=[("active", ACCENT)],
              foreground=[("disabled", MUTED)])
        s.configure("Rec.TButton", background=REC, foreground="#ffffff")
        s.map("Rec.TButton", background=[("active", "#e07070")])

    def _build(self):
        
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14, pady=(12, 8))

        self.rec_btn = ttk.Button(bar, text="Record", command=self.toggle_record)
        self.rec_btn.pack(side="left")
        self.file_btn = ttk.Button(bar, text="Add File...", command=self.add_file)
        self.file_btn.pack(side="left", padx=(8, 0))
        self.folder_btn = ttk.Button(bar, text="Add Folder...", command=self.add_folder)
        self.folder_btn.pack(side="left", padx=(8, 0))

        tk.Frame(bar, bg=MUTED, width=1, height=26).pack(side="left", padx=14)

        self.rename_btn = ttk.Button(bar, text="Rename", command=self.rename_selected)
        self.rename_btn.pack(side="left")
        self.del_btn = ttk.Button(bar, text="Delete", command=self.delete_selected)
        self.del_btn.pack(side="left", padx=(8, 0))

        #Live input level, only visible while recording
        self.meter = tk.Canvas(bar, width=120, height=10, bg=PANEL,
                               highlightthickness=0)
        self.status = tk.Label(bar, text="", bg=BG, fg=MUTED, anchor="e")
        self.status.pack(side="right", fill="x", expand=True, padx=(10, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        cols = ("when", "len", "wpm", "fill", "src", "engine", "label")
        
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        widths = {"when": 130, "len": 55, "wpm": 60, "fill": 70,
                  "src": 55, "engine": 80, "label": 110}
        heads = {"when": "When", "len": "Length", "wpm": "WPM", "fill": "Fill/min",
                 "src": "Source", "engine": "Engine", "label": "Name"}
        for c in cols:
            
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c],
                             anchor="w" if c in ("when", "src", "engine", "label") else "e")
        
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Double-1>", self.rename_selected)   # double-click to rename

        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        #Sessions transcribed by an engine that strips fillers are flagged,
        #since their filler counts aren't comparable to the verbatim ones.
        self.tree.tag_configure("stripped", foreground=WARN)

        right = tk.Frame(body, bg=PANEL, width=380)
        right.pack(side="left", fill="both", padx=(12, 0))
        right.pack_propagate(False)

        tk.Label(right, text="SESSION", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(12, 0))
        self.detail_head = tk.Label(right, text="Select a session", bg=PANEL, fg=FG,
                                    font=("Segoe UI", 13), anchor="w", justify="left")
        self.detail_head.pack(anchor="w", padx=14, pady=(2, 8))

        self.detail_stats = tk.Label(right, text="", bg=PANEL, fg=FG, anchor="w",
                                     justify="left", font=("Consolas", 10))
        self.detail_stats.pack(anchor="w", padx=14)

        tk.Label(right, text="TRANSCRIPT", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(14, 4))
        
        #Kept as a Text (not a Label) so the text stays selectable and copyable.
        self.transcript = tk.Text(right, bg=BG, fg=FG, wrap="word", height=12,
                                  relief="flat", padx=10, pady=8,
                                  font=("Segoe UI", 10), state="disabled",
                                  cursor="arrow")
        
        self.transcript.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        
        self.play_btn = ttk.Button(right, text="Play Audio", command=self.play_audio,
                                   state="disabled")
        
        self.play_btn.pack(anchor="w", padx=14, pady=(0, 14))

        self.summary = tk.Label(self, text="", bg=BG, fg=MUTED, anchor="w",
                                font=("Consolas", 10), justify="left")
        
        self.summary.pack(fill="x", padx=14, pady=(0, 12))
        self.summary.bind("<Configure>",
                          lambda e: self.summary.config(wraplength=e.width - 8))


    #Data
    def _reconcile(self):

        try:
            for r in storage.recent(self.conn, limit=10000):
                
                if not r["transcript"]:
                    continue
                total, fill, mark, detail = engine.count_fillers(r["transcript"])
                
                if (total, fill, mark) != (r["word_count"], r["filler_count"],
                                           r["marker_count"]):
                    storage.update_counts(self.conn, r["id"], total, fill, mark, detail)
        
        except Exception:
            pass        # never let a startup nicety block the app

    def refresh(self):
        
        self.rows = storage.recent(self.conn, limit=500)
        self.tree.delete(*self.tree.get_children())
        
        for r in self.rows:
            mins = r["duration_sec"] / 60 or 1e-9
            self.tree.insert(
                "", "end", iid=str(r["id"]),
                values=(r["started_at"].replace("T", " ")[:16],
                        f"{r['duration_sec']:.0f}s",
                        f"{storage.wpm(r):.0f}",
                        f"{r['filler_count'] / mins:.1f}",
                        r["source"], r["engine"], r["label"] or ""),
                tags=() if r["verbatim"] else ("stripped",))

        self._summary_text()
        
        if not self.rows:
            
            self.set_status("No sessions yet - record or add a file to start.")

    def _summary_text(self):
        
        parts = []
        
        for s in storage.summary(self.conn):
            parts.append(f"{s['engine']}  {s['sessions']}x  {s['minutes']:.0f}min  "
                         f"{s['wpm']:.0f}wpm  {s['fillers_per_min']:.1f}f/min")
        t = storage.trend(self.conn)
        
        if t:
            parts.append(f"trend(5v5)  {t['wpm_delta']:+.0f}wpm  "
                         f"{t['fillers_delta']:+.1f}f/min")
        self.summary.config(text="    |    ".join(parts) if parts else "")

    def _selected_row(self):
        
        sel = self.tree.selection()
        return storage.get_session(self.conn, int(sel[0])) if sel else None

    def show_detail(self, _evt=None):
        
        r = self._selected_row()
        if not r:
            return

        mins = r["duration_sec"] / 60 or 1e-9
        title = r["label"] or r["started_at"].replace("T", " ")[:16]
        self.detail_head.config(text=title)

        lines = [
            f"pace      {storage.wpm(r):6.0f} wpm",
            f"fillers   {r['filler_count']:6d}   ({r['filler_count'] / mins:.1f}/min)",
            f"markers   {r['marker_count']:6d}   ({r['marker_count'] / mins:.1f}/min)",
            f"words     {r['word_count']:6d}",
            f"length    {r['duration_sec']:6.0f}s",
            f"engine    {r['engine']}" + ("" if r["verbatim"] else "  (strips fillers)"),
        ]
        detail = json.loads(r["detail_json"])
        if detail:
            lines.append("")
            
            for k, v in sorted(detail.items(), key=lambda x: -x[1])[:8]:
                lines.append(f"  {k:<14} {v}")
        self.detail_stats.config(text="\n".join(lines))

        #Unlock only long enough to swap the text, then lock again
        self.transcript.config(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.insert("1.0", r["transcript"] or "(empty)")
        self.transcript.config(state="disabled")

        ok = bool(r["audio_path"]) and os.path.exists(r["audio_path"])
        self.play_btn.config(state="normal" if ok else "disabled")

    def set_status(self, text):
        
        self.status.config(text=text)

    def _lock(self, busy):
        
        self.busy = busy
        state = "disabled" if busy else "normal"
        
        for b in (self.file_btn, self.folder_btn, self.rename_btn, self.del_btn):
            b.config(state=state)

    def _process(self, audio, source, source_path, label=None):

        wav_path = engine.session_path()
        engine.save(audio, wav_path)

        self.jobs.put(("status", "Transcribing (first run downloads the model)..."))
        text, verbatim, eng = engine.transcribe(wav_path)
        total, fillers, markers, detail = engine.count_fillers(text)

        storage.log_session(
            storage.connect(),      # own connection: sqlite is per-thread
            duration_sec=len(audio) / engine.SAMPLERATE,
            source=source, source_path=source_path, audio_path=wav_path,
            engine=eng, verbatim=verbatim, transcript=text,
            word_count=total, filler_count=fillers, marker_count=markers,
            detail=detail, label=label)

    def _pump(self):
        """Drain worker messages. All widget updates happen here, on the UI thread."""
        try:
            
            while True:
                kind, payload = self.jobs.get_nowait()
                if kind == "status":
                    self.set_status(payload)
                
                elif kind == "level":
                    self._draw_meter(payload)
                
                elif kind == "refresh":
                    self.refresh()
                
                elif kind == "warn":
                    #A skipped file in a batch isn't a failed job
                    messagebox.showwarning("Some files were skipped", payload)
                
                elif kind == "done":
                    self._end_job(payload)
                
                elif kind == "error":
                    self._end_job("Failed")
                    messagebox.showerror("Something went wrong", payload)
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _end_job(self, msg):
        
        self._lock(False)
        self.stop_flag.clear()
        self.rec_btn.config(text="Record", style="TButton")
        self.meter.pack_forget()
        self.set_status(msg)
        self.refresh()


    #Recording in app
    def toggle_record(self):
        if self.busy and not self.stop_flag.is_set():
            
            #Second press means stop. The worker watches this event.
            self.stop_flag.set()
            self.set_status("Finishing...")
            return
        
        if self.busy:
            return

        self._lock(True)
        self.stop_flag.clear()
        self.rec_btn.config(text="Stop", style="Rec.TButton")
        self.meter.pack(side="left", padx=(14, 0))
        self.set_status("Recording...")
        threading.Thread(target=self._record_worker, daemon=True).start()

    def _record_worker(self):
        
        #Capture from the mic without blocking the UI.

        try:
            import sounddevice as sd

            q = queue.Queue()

            def callback(indata, frames, time_info, status):
                q.put(indata[:, 0].copy())

            chunks = []
            silence_run = 0
            started = False

            with sd.InputStream(device=engine.DEVICE, channels=1,
                                samplerate=engine.SAMPLERATE,
                                blocksize=int(engine.SAMPLERATE * engine.BLOCK),
                                dtype="float32", callback=callback):
                while not self.stop_flag.is_set():
                    if len(chunks) * engine.BLOCK >= engine.MAX_DURATION:
                        break
                    try:
                        
                        block = q.get(timeout=0.5)
                    
                    except queue.Empty:
                        continue
                    chunks.append(block)

                    level = 20 * np.log10(np.sqrt(np.mean(block ** 2)) + 1e-10)
                    self.jobs.put(("level", level))

                    if level > engine.SILENCE_DB:
                        started = True
                        silence_run = 0
                    
                    else:
                        silence_run += 1

                    secs = len(chunks) * engine.BLOCK
                    
                    if len(chunks) % 5 == 0:
                        self.jobs.put(("status", f"Recording  {secs:.0f}s"))

                    #Same auto-stop rule as the CLI
                    if started and silence_run * engine.BLOCK >= engine.SILENCE_STOP:
                        break

            if not chunks:
                self.jobs.put(("done", "Nothing recorded"))
                return

            audio = np.concatenate(chunks)
            trim = int(silence_run * engine.BLOCK * engine.SAMPLERATE)
            
            if 0 < trim < len(audio):
                audio = audio[:-trim]        # trailing silence dilutes wpm

            if audio.size == 0:
                self.jobs.put(("done", "Nothing recorded"))
                return

            self._process(audio, "mic", None)
            self.jobs.put(("done", f"Recorded {len(audio) / engine.SAMPLERATE:.0f}s"))
        
        except Exception as e:
            self.jobs.put(("error", str(e)))

    def _draw_meter(self, db):

        self.meter.delete("all")
        frac = max(0.0, min(1.0, (db + 60) / 60))
        color = REC if db > engine.SILENCE_DB else MUTED
        self.meter.create_rectangle(0, 0, 120 * frac, 10, fill=color, width=0)

    #Insert files using ffmpeg
    def add_file(self):
        if self.busy:
            return
        
        path = filedialog.askopenfilename(
            title="Choose an audio or video file",
            filetypes=[("Audio/Video", "*.wav *.mp3 *.m4a *.mp4 *.flac *.ogg *.mov *.webm"),
                       ("All files", "*.*")])
        if not path:
            return
        self._lock(True)
        
        self.set_status(f"Decoding {os.path.basename(path)}...")
        threading.Thread(target=self._file_worker, args=([path],), daemon=True).start()

    #Inserting folders
    def add_folder(self):
        
        if self.busy:
            return
        folder = filedialog.askdirectory(title="Choose a folder of recordings")
        
        if not folder:
            return

        import audio_io
        paths = audio_io.find_audio(folder)
        
        if not paths:
            messagebox.showinfo("Nothing to add",
                                "No audio or video files found in that folder.")
            return
        
        if not messagebox.askyesno(
                "Add folder",
                f"Found {len(paths)} files. Transcribing runs about as long as the "
                f"audio itself, so this may take a while.\n\nContinue?"):
            return

        self._lock(True)
        self.stop_flag.clear()
        self.rec_btn.config(text="Stop", style="Rec.TButton")
        threading.Thread(target=self._file_worker, args=(paths,), daemon=True).start()

    def _file_worker(self, paths):
        
        try:
            import audio_io
            done, failed = 0, []
            
            for i, path in enumerate(paths, 1):
                
                if self.stop_flag.is_set():
                    break
                name = os.path.basename(path)
                self.jobs.put(("status", f"[{i}/{len(paths)}] {name}"))
                
                try:
                    audio = audio_io.load_audio(path, samplerate=engine.SAMPLERATE)
                    #Folder imports get named after the file, so a batch of
                    #twenty is still identifiable in the list afterwards.
                    label = os.path.splitext(name)[0] if len(paths) > 1 else None
                    self._process(audio, "file", os.path.abspath(path), label=label)
                    done += 1
                    if len(paths) > 1:
                        self.jobs.put(("refresh", None))
                
                except Exception as e:
                    failed.append(f"{name}: {e}")

            msg = f"Added {done} of {len(paths)}"
            
            if failed:
                msg += f", {len(failed)} skipped"
                self.jobs.put(("warn", "\n".join(failed[:8])))
            self.jobs.put(("done", msg))
        
        except Exception as e:
            self.jobs.put(("error", str(e)))
    
    #Rename files
    def rename_selected(self, _evt=None):
        r = self._selected_row()
        if not r or self.busy:
            return
        name = simpledialog.askstring(
            "Rename session", "Name:", initialvalue=r["label"] or "", parent=self)
        
        if name is None:
            return
        storage.set_label(self.conn, r["id"], name.strip())
        self.refresh()
        self.tree.selection_set(str(r["id"]))
        self.show_detail()

    def delete_selected(self):
        
        r = self._selected_row()
        if not r or self.busy:
            return
        title = r["label"] or r["started_at"][:16]
        
        if messagebox.askyesno("Delete session",
                               f"Delete '{title}'?\n\nThe audio file stays on disk."):
            storage.delete_session(self.conn, r["id"])
            self.refresh()

    def play_audio(self):
        r = self._selected_row()
        
        if not r or not r["audio_path"]:
            return
        
        try:
            import sounddevice as sd
            import scipy.io.wavfile as wav
            sr, data = wav.read(r["audio_path"])
            sd.play(data.astype(np.float32) / 32768.0, samplerate=sr)
        
        except Exception as e:
            messagebox.showerror("Playback failed", str(e))


if __name__ == "__main__":
    _check_engine()
    SpeechApp().mainloop()