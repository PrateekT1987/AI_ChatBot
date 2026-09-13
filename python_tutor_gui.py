#!/usr/bin/env python3
"""Python, interactively — a desktop Python tutor backed by multiple LLM providers.

Requirements:
    sudo apt install python3-tk     # system tkinter (Ubuntu/Debian)
    pip install requests            # already present on most systems

Runs with:  python3 python_tutor_gui.py

Reads API keys from .env (same file Vite used). Model fallback:
gemini-3.7-flash -> Qwen2.5-7B -> Qwen3-8B -> big-pickle -> mimo-v2.5-free.

Every code block the tutor writes gets a "run this code" button: it runs the
snippet with the same interpreter (sys.executable) in an isolated temp dir,
with stdin closed, killed after RUN_TIMEOUT seconds. It runs with your normal
user privileges - only run snippets you trust.

The "Sandbox" button in the header toggles a free-form scratchpad where you can
write and run your own code. It auto-saves to scratchpad.py next to this file.
"""

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

import requests
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import scrolledtext

# ── Theme (mirrors the original React console) ─────────────────────────
BG = "#12181F"
PANEL = "#161E27"
PANEL_EDGE = "#232D39"
TEXT = "#E7E5DC"
MUTED = "#7C8896"
BLUE = "#4F8FC0"
AMBER = "#E3A542"
CODE_BG = "#0D1218"
ERROR = "#C4715A"
CODE_FG = "#D6E4EF"
OK_GREEN = "#7FB069"

# ── Sandbox: snippet execution ─────────────────────────────────────────
RUN_TIMEOUT = 10          # seconds before a snippet is killed
SCRATCH_TIMEOUT = 120     # longer budget for the Sandbox (network/API scripts)
RUN_MAX_OUTPUT = 20000    # characters of stdout/stderr shown

LEVELS = ["Beginner", "Intermediate", "Advanced"]

# ── Providers + models ─────────────────────────────────────────────────
PROVIDER_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "siliconflow": "https://api.siliconflow.com/v1",
    "zen": "https://opencode.ai/zen/v1",
}

MODELS = [
    ("gemini-3.7-flash", "gemini"),
    ("Qwen/Qwen2.5-7B-Instruct", "siliconflow"),
    ("Qwen/Qwen3-8B", "siliconflow"),
    ("big-pickle", "zen"),
    ("mimo-v2.5-free", "zen"),
]

STARTERS = {
    "Beginner": [
        "What's the difference between a list and a tuple?",
        "Explain for-loops with a simple example",
        "Why do I need to indent my code?",
    ],
    "Intermediate": [
        "How do list comprehensions actually work?",
        "Debug my BeautifulSoup scraper's rate limiting",
        "When should I use a class vs. a plain function?",
    ],
    "Advanced": [
        "Walk me through Python's GIL and threading",
        "How do generators and yield actually work under the hood?",
        "Best practices for structuring a data pipeline package",
    ],
}


def load_env(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


ENV = load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def provider_headers(provider):
    if provider == "gemini":
        return {"Authorization": f"Bearer {ENV.get('VITE_GEMINI_API_KEY', '')}"}
    if provider == "siliconflow":
        return {"Authorization": f"Bearer {ENV.get('VITE_SILICONFLOW_API_KEY', '')}"}
    # OpenCode Zen free tier requires OpenCode client headers.
    return {
        "Authorization": f"Bearer {ENV.get('VITE_OPENCODE_API_KEY', '')}",
        "x-opencode-client": "cli",
        "x-opencode-session": f"ses_{int(time.time() * 1000)}",
        "User-Agent": "opencode/1.15.5",
    }


def build_system_prompt(level):
    base = (
        "You are a patient, precise Python tutor running inside an interactive REPL-style teaching console. "
        "Teach by explaining the 'why', not just the 'how'. Always include a short, runnable code example when a concept "
        "benefits from one, formatted in a fenced code block with the python language tag. Ask a small follow-up question "
        "or suggest a next step at the end of your answer to keep the lesson moving. Keep answers focused \u2014 "
        "prefer one clear example over several. When the learner shares code with an error, diagnose it directly before "
        "explaining the underlying concept."
    )
    notes = {
        "Beginner": (
            "The learner is new to Python. Avoid jargon unless you define it immediately. Use everyday analogies. "
            "Keep code examples under 8 lines."
        ),
        "Intermediate": (
            "The learner is comfortable with core syntax (loops, functions, basic data structures) and has built small "
            "real projects, including a BeautifulSoup-based web scraper. Skip basic definitions. Favor practical, "
            "slightly-real-world examples (automation, working with APIs/data) over toy examples. It's fine to introduce "
            "idiomatic patterns (comprehensions, context managers, decorators) when relevant."
        ),
        "Advanced": (
            "The learner wants depth: internals, performance trade-offs, and idiomatic/production-grade patterns. "
            "Don't oversimplify. Reference relevant standard-library or ecosystem tools by name."
        ),
    }
    return f"{base}\n\n{notes[level]}"


def parse_content(text):
    """Split a message into (kind, value) parts where kind is 'text' or 'code'."""
    parts = []
    pattern = re.compile(r"```(\w*)\n?([\s\S]*?)```")
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            parts.append(("text", text[last:m.start()]))
        parts.append(("code", m.group(2).rstrip("\n")))
        last = m.end()
    if last < len(text):
        parts.append(("text", text[last:]))
    return parts


def execute_python(code, timeout=RUN_TIMEOUT):
    """Run a snippet with the same interpreter in an isolated temp dir.

    Returns (ok, stdout, stderr). Never raises for user-code errors; only
    returns a message for timeouts. stdin is closed so input() gets EOF.
    """
    with tempfile.TemporaryDirectory(prefix="pytutor_") as workdir:
        path = os.path.join(workdir, "snippet.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-u", path],
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, "", f"Timed out after {timeout}s."
        except OSError as exc:
            return False, "", f"Could not start the interpreter: {exc}"
        return proc.returncode == 0, proc.stdout, proc.stderr


def stream_chat(model_id, provider, messages, flag=None):
    """Yield text deltas for a streamed chat completion.

    If `flag` is a dict, sets flag["truncated"]=True when the model reports
    finish_reason "length" (answer hit the token cap).
    """
    url = f"{PROVIDER_URLS[provider]}/chat/completions"
    payload = {
        "model": model_id,
        "max_tokens": 2000,
        "stream": True,
        "messages": messages,
    }
    with requests.post(
        url, json=payload, headers=provider_headers(provider), stream=True, timeout=30
    ) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            try:
                choice = chunk["choices"][0]
            except (KeyError, IndexError, TypeError):
                continue
            if flag is not None and choice.get("finish_reason") == "length":
                flag["truncated"] = True
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                yield content


def looks_truncated(text, truncated=False):
    """Guess whether an answer was cut off: provider said 'length', or
    we ended inside a fenced code block (odd number of ``` fences)."""
    if truncated:
        return True
    return text.count("```") % 2 == 1


# ── GUI ────────────────────────────────────────────────────────────────
class Scratchpad(tk.Toplevel):
    """Free-form code playground: type anything, run it, see the output."""

    FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratchpad.py")

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.q = queue.Queue()
        self._cleared = False
        self.title("Sandbox \u2014 free-form scratchpad")
        self.configure(bg=BG)
        self.geometry("660x600")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=2)
        self.rowconfigure(3, weight=1)

        # header
        head = tk.Frame(self, bg=PANEL, padx=12, pady=8)
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text="Sandbox", bg=PANEL, fg=TEXT,
                 font=self.app.body).pack(side="left")
        tk.Label(head, text="free-form scratchpad \u2014 runs with your user privileges",
                 bg=PANEL, fg=MUTED, font=self.app.small).pack(side="left", padx=(8, 0))
        self.status_lbl = tk.Label(head, text="ready", bg=PANEL, fg=MUTED,
                                   font=self.app.small)
        self.status_lbl.pack(side="right")

        # editor
        editor_frame = tk.Frame(self, bg=CODE_BG)
        editor_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(8, 4))
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)
        self.editor = scrolledtext.ScrolledText(
            editor_frame, wrap="none", undo=True,
            bg=CODE_BG, fg=TEXT, insertbackground=TEXT,
            selectbackground=BLUE, selectforeground=BG,
            relief="flat", borderwidth=0, font=self.app.mono, padx=10, pady=8)
        self.editor.grid(column=0, row=0, sticky="nsew")
        self.editor.tag_configure("placeholder", foreground=MUTED,
                                  font=self.app.small)

        # action row
        actions = tk.Frame(self, bg=BG)
        actions.grid(row=2, column=0, sticky="ew", padx=10, pady=2)
        self.run_btn = tk.Button(
            actions, text="Run (Ctrl+Enter)", command=self.run,
            font=self.app.mono, bg=BLUE, fg=BG, borderwidth=0,
            activebackground="#6ba7d4", activeforeground=BG,
            padx=12, pady=4, cursor="hand2")
        self.run_btn.pack(side="left")
        self.clear_btn = tk.Button(
            actions, text="Clear", command=self.clear,
            font=self.app.mono, bg=PANEL, fg=MUTED, borderwidth=0,
            activebackground=PANEL_EDGE, activeforeground=TEXT,
            padx=12, pady=4, cursor="hand2")
        self.clear_btn.pack(side="left", padx=(8, 0))
        self.save_btn = tk.Button(
            actions, text="Save", command=self.save,
            font=self.app.mono, bg=PANEL, fg=MUTED, borderwidth=0,
            activebackground=PANEL_EDGE, activeforeground=TEXT,
            padx=12, pady=4, cursor="hand2")
        self.save_btn.pack(side="left", padx=(8, 0))
        self.reload_btn = tk.Button(
            actions, text="Reload", command=self.reload,
            font=self.app.mono, bg=PANEL, fg=MUTED, borderwidth=0,
            activebackground=PANEL_EDGE, activeforeground=TEXT,
            padx=12, pady=4, cursor="hand2")
        self.reload_btn.pack(side="left", padx=(8, 0))
        self.close_btn = tk.Button(
            actions, text="Close", command=self.close,
            font=self.app.mono, bg=PANEL, fg=MUTED, borderwidth=0,
            activebackground=PANEL_EDGE, activeforeground=TEXT,
            padx=12, pady=4, cursor="hand2")
        self.close_btn.pack(side="left", padx=(8, 0))

        # output
        out_frame = tk.Frame(self, bg=BG)
        out_frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=4)
        out_frame.columnconfigure(0, weight=1)
        out_frame.rowconfigure(0, weight=1)
        self.output = scrolledtext.ScrolledText(
            out_frame, wrap="char", bg=BG, fg=CODE_FG, font=self.app.mono,
            relief="flat", borderwidth=0, padx=10, pady=8)
        self.output.grid(column=0, row=0, sticky="nsew")
        self.output.configure(state="disabled")
        self.output.tag_configure("stdout", foreground=CODE_FG)
        self.output.tag_configure("stderr", foreground=ERROR)
        self.output.tag_configure("ok", foreground=OK_GREEN)
        self.output.tag_configure("err", foreground=ERROR)

        self.editor.bind("<Control-Return>", lambda e: self.run())
        self._load()
        self.after(50, self._poll)

    def _load(self):
        try:
            with open(self.FILE, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return
        if content.strip():
            self.editor.insert("1.0", content)

    def save(self):
        text = self.editor.get("1.0", "end-1c")
        # Never destroy a saved script because the editor happens to be empty.
        # Only an explicit Clear() writes an empty file.
        if not text.strip() and not self._cleared:
            if os.path.exists(self.FILE) and os.path.getsize(self.FILE) > 0:
                self.set_status("kept existing scratchpad (editor is empty)", MUTED)
                return
        self._cleared = False
        try:
            with open(self.FILE, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
        self.set_status("saved to scratchpad.py")

    def reload(self):
        """Replace the editor with the on-disk scratchpad file."""
        self.editor.delete("1.0", "end")
        try:
            with open(self.FILE, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return
        if content.strip():
            self.editor.insert("1.0", content)
        self.set_status("reloaded from disk")

    def close(self):
        # Never auto-write on close: closing must not overwrite the on-disk
        # scratchpad with whatever happens to be in the editor. Use Save.
        self.destroy()

    def clear(self):
        self.editor.delete("1.0", "end")
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self._cleared = True
        self.save()  # explicit wipe of the on-disk file too
        self._cleared = False
        self.set_status("cleared")

    def set_status(self, msg, color=MUTED):
        self.status_lbl.configure(text=msg, fg=color)

    def run(self):
        code = self.editor.get("1.0", "end-1c").strip()
        if not code:
            self.set_status("type some code first", ERROR)
            return
        self.run_btn.configure(state="disabled")
        self.set_status("running\u2026", BLUE)

        def worker():
            t0 = time.time()
            result = execute_python(code, SCRATCH_TIMEOUT)
            elapsed = time.time() - t0
            self.q.put((*result, elapsed))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            ok, stdout, stderr, elapsed = self.q.get_nowait()
        except queue.Empty:
            pass
        except ValueError:
            pass
        else:
            self.run_btn.configure(state="normal")
            self.set_status(f"exit {'ok' if ok else 'error'} in {elapsed:.2f}s",
                            OK_GREEN if ok else ERROR)
            self._append_output(ok, stdout, stderr)
        if self.winfo_exists():
            self.after(50, self._poll)

    def _append_output(self, ok, stdout, stderr):
        if len(stdout) > RUN_MAX_OUTPUT:
            stdout = stdout[:RUN_MAX_OUTPUT] + "\n\u2026 (output truncated)"
        if len(stderr) > RUN_MAX_OUTPUT:
            stderr = stderr[:RUN_MAX_OUTPUT] + "\n\u2026 (output truncated)"
        self.output.configure(state="normal")
        divider = "\u2501 exit " + ("ok" if ok else "error") + " \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        self.output.insert("end", divider, ("ok" if ok else "err",))
        if stdout.strip():
            self.output.insert("end", stdout + ("\n" if not stdout.endswith("\n") else ""), ("stdout",))
        if stderr.strip():
            self.output.insert("end", stderr + ("\n" if not stderr.endswith("\n") else ""), ("stderr",))
        if not stdout.strip() and not stderr.strip():
            self.output.insert("end", "(no output)\n", ("stdout",))
        self.output.see("end")
        self.output.configure(state="disabled")


class TutorApp:
    def __init__(self, root):
        self.root = root
        self.history = []
        self.level = "Intermediate"
        self.loading = False
        self.block_start = None
        self.has_sent = False
        self._run_ack = False
        self.scratch = None
        self.q = queue.Queue()

        self.mono = ("DejaVu Sans Mono", 11)
        self.body = ("Georgia", 12)
        self.small = ("TkDefaultFont", 10)

        self._style(root)
        self._build_widgets()
        self.update_level_buttons()
        self.show_starters()
        self.root.after(50, self._poll_queue)

    def _style(self, root):
        root.title("Python, interactively")
        root.configure(bg=BG)
        root.geometry("760x640")
        root.minsize(560, 420)

    def _build_widgets(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        # header
        header = tk.Frame(self.root, bg=PANEL, pady=10, padx=16)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="Python, interactively", bg=PANEL, fg=TEXT,
                 font=self.body, anchor="w").pack(side="left")
        tk.Label(header, text="Python 3.x console \u2014 a tutor sits behind the prompt",
                 bg=PANEL, fg=MUTED, font=self.small, anchor="w").pack(side="left", padx=(10, 0))
        self.level_buttons = {}
        bar = tk.Frame(header, bg=PANEL)
        bar.pack(side="right")
        self.scratch_btn = tk.Button(
            bar, text="Sandbox", command=self._toggle_scratch,
            font=self.small, borderwidth=0, bd=0, pady=2, padx=10,
            bg=PANEL, fg=MUTED, activebackground=PANEL_EDGE,
            activeforeground=TEXT, cursor="hand2")
        self.scratch_btn.pack(side="right", padx=(8, 0))
        for lvl in LEVELS:
            btn = tk.Button(bar, text=lvl, command=lambda l=lvl: self.set_level(l),
                            font=self.small, borderwidth=0, bd=0, pady=2, padx=8,
                            activebackground=PANEL_EDGE, activeforeground=TEXT,
                            cursor="hand2")
            btn.pack(side="left", padx=1)
            self.level_buttons[lvl] = btn

        # starters (shown before first message)
        self.starters_frame = tk.Frame(self.root, bg=BG, padx=22, pady=14)
        self.starters_frame.grid(row=1, column=0, sticky="ew")
        tk.Label(self.starters_frame, text=(
            "Ask anything \u2014 a concept, an error message, or code you want reviewed. "
            "Pick a level above to set how much is explained. A few ways to start:"),
            bg=BG, fg=MUTED, font=self.small, wraplength=700, justify="left").pack(anchor="w")
        self.starter_buttons = []
        self._build_starter_buttons()

        # transcript
        self.text = scrolledtext.ScrolledText(
            self.root, wrap="char", bg=BG, fg=TEXT, font=self.body,
            insertbackground=TEXT, selectbackground=BLUE, selectforeground=BG,
            relief="flat", borderwidth=0, padx=22, pady=14, undo=False)
        self.text.grid(row=2, column=0, sticky="nsew")
        self.text.configure(state="disabled")

        self._config_tags()

        # input row
        bottom = tk.Frame(self.root, bg=PANEL, padx=14, pady=12)
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(1, weight=1)
        tk.Label(bottom, text=">>>", fg=AMBER, bg=PANEL,
                 font=self.mono).grid(row=0, column=0, padx=(0, 8))
        self.input_var = tk.StringVar()
        entry = tk.Entry(bottom, textvariable=self.input_var, bg=CODE_BG, fg=TEXT,
                         insertbackground=TEXT, relief="flat", font=self.mono, borderwidth=0)
        entry.grid(row=0, column=1, sticky="ew", ipady=6, ipadx=8)
        entry.bind("<Return>", lambda e: self.send())
        self.run_btn = tk.Button(bottom, text="Run", command=self.send,
                                 font=self.mono, bg=BLUE, fg=BG, borderwidth=0,
                                 activebackground="#6ba7d4", activeforeground=BG,
                                 padx=14, pady=6, cursor="hand2")
        self.run_btn.grid(row=0, column=2, padx=(10, 0))
        self.status = tk.Label(self.root, bg=BG, fg=MUTED, font=self.small, anchor="se")
        self.status.grid(row=4, column=0, sticky="ew", padx=22, pady=(4, 6))
        self.set_status("ready")

    def _config_tags(self):
        t = self.text
        t.tag_configure("user_prompt", foreground=AMBER, font=self.mono)
        t.tag_configure("ai_prompt", foreground=BLUE, font=self.mono)
        t.tag_configure("user_text", foreground=TEXT)
        t.tag_configure("ai_text", foreground="#D9D6CB")
        t.tag_configure("code", foreground=CODE_FG, background=CODE_BG,
                        font=self.mono, lmargin1=12, lmargin2=12, rmargin=12,
                        spacing1=4, spacing3=4)
        t.tag_configure("error", foreground=ERROR)
        t.tag_configure("thinking", foreground=MUTED, font=self.small, lmargin1=26)

    def _build_starter_buttons(self):
        for b in self.starter_buttons:
            b.destroy()
        self.starter_buttons = []
        for s in STARTERS[self.level]:
            btn = tk.Button(
                self.starters_frame,
                text=s,
                command=lambda txt=s: self.send(txt),
                bg=PANEL, fg="#D9D6CB", activebackground=PANEL_EDGE,
                activeforeground=TEXT, relief="flat", borderwidth=0,
                font=self.body, anchor="w", justify="left", pady=6, padx=10,
                cursor="hand2")
            btn.pack(fill="x", pady=3)
            self.starter_buttons.append(btn)

    def update_level_buttons(self):
        for lvl, btn in self.level_buttons.items():
            if lvl == self.level:
                btn.configure(bg=BLUE, fg=BG)
            else:
                btn.configure(bg=PANEL, fg=MUTED)

    def set_level(self, level):
        if self.loading:
            return
        self.level = level
        self.update_level_buttons()
        if not self.has_sent:
            self._build_starter_buttons()

    def show_starters(self):
        self.starters_frame.grid()

    def hide_starters(self):
        self.starters_frame.grid_remove()

    def _toggle_scratch(self):
        if self.scratch is not None and self.scratch.winfo_exists():
            if self.scratch.winfo_viewable():
                self.scratch.withdraw()
                self.scratch_btn.configure(bg=PANEL, fg=MUTED)
            else:
                self.scratch.deiconify()
                self.scratch.lift()
                self.scratch.reload()   # always reflect the on-disk scratchpad
                self.scratch_btn.configure(bg=BLUE, fg=BG)
        else:
            self.scratch = Scratchpad(self.root, app=self)
            self.scratch.protocol("WM_DELETE_WINDOW", self._close_scratch)
            self.scratch_btn.configure(bg=BLUE, fg=BG)

    def _close_scratch(self):
        if self.scratch is not None and self.scratch.winfo_exists():
            self.scratch.close()
        self.scratch = None
        self.scratch_btn.configure(bg=PANEL, fg=MUTED)

    def set_status(self, msg):
        self.status.configure(text=msg)

    def _insert(self, text, tags=()):
        self.text.configure(state="normal")
        self.text.insert("end", text, tags)
        self.text.see("end")
        self.text.configure(state="disabled")

    def _insert_parsed(self, content, role):
        """Insert a message respecting fenced code blocks."""
        is_user = role == "user"
        self._insert(">>> " if is_user else "... ", ("user_prompt" if is_user else "ai_prompt",))
        for kind, value in parse_content(content):
            if not value.strip():
                continue
            if kind == "code":
                code = value.strip("\n")
                self._insert("\n" + code + "\n", ("code",))
                if not is_user:
                    self._insert_runner(code)
            else:
                self._insert(
                    value.strip() + "\n",
                    ("user_text" if is_user else "ai_text",),
                )
        self._insert("\n")

    def _insert_runner(self, code):
        """Embed a 'run this code' button + output area right under a code block."""
        frame = tk.Frame(self.text, bg=CODE_BG)
        button = tk.Button(
            frame,
            text="\u25b6 run this code",
            command=None,
            bg=CODE_BG, fg=BLUE, activebackground=CODE_BG,
            activeforeground="#6ba7d4", relief="flat", borderwidth=0,
            highlightthickness=0, font=("DejaVu Sans Mono", 9),
            cursor="hand2", padx=2, pady=0,
        )
        out = tk.Label(
            frame, text="", bg=CODE_BG, fg=CODE_FG,
            font=("DejaVu Sans Mono", 9), justify="left", anchor="w",
            wraplength=560,
        )
        button.configure(command=lambda: self.run_snippet(code, out, button))
        button.pack(anchor="w")
        out.pack(anchor="w")
        self.text.window_create("end", window=frame, padx=10, pady=2)

    def run_snippet(self, code, out, button):
        if not self._run_ack:
            if not messagebox.askyesno(
                "Run this snippet?",
                "Snippets run locally with your normal user privileges.\n"
                "Only run code you trust. Continue?",
                parent=self.root,
            ):
                return
            self._run_ack = True
        button.configure(state="disabled")
        out.configure(text="running\u2026", fg=MUTED)
        self.set_status("running snippet\u2026")

        def worker():
            result = execute_python(code)
            self.q.put(("run_out", out, button, *result))

        threading.Thread(target=worker, daemon=True).start()

    def _show_result(self, out, button, result):
        ok, stdout, stderr = result
        if len(stdout) > RUN_MAX_OUTPUT:
            stdout = stdout[:RUN_MAX_OUTPUT] + "\n\u2026 (output truncated)"
        if len(stderr) > RUN_MAX_OUTPUT:
            stderr = stderr[:RUN_MAX_OUTPUT] + "\n\u2026 (output truncated)"
        chunks = [c for c in (stdout.rstrip(), stderr.rstrip()) if c]
        if not chunks:
            chunks.append("(ran with no output)")
        out.configure(text="\n".join(chunks), fg=(CODE_FG if ok else ERROR))
        button.configure(state="normal")
        self.set_status("ready")

    def append_user_message(self, text):
        self._insert_parsed(text, "user")

    def send(self, text=None):
        if self.loading:
            return
        text = (text if text is not None else self.input_var.get()).strip()
        if not text:
            return
        self.history.append({"role": "user", "content": text})
        self.has_sent = True
        self.hide_starters()
        self.append_user_message(text)
        self.input_var.set("")
        self.loading = True
        self.run_btn.configure(state="disabled")
        self.set_status("thinking\u2026")

        self.text.configure(state="normal")
        self.block_start = self.text.index("end-1c")  # absolute index; stays put
        self.text.insert("end", "", ("ai_prompt",))
        self.text.configure(state="disabled")

        messages = [{"role": "system", "content": build_system_prompt(self.level)}]
        messages.extend(self.history)
        threading.Thread(target=self._worker, args=(messages,), daemon=True).start()

    def _worker(self, messages):
        ordered = [MODELS[0]] + [m for i, m in enumerate(MODELS) if i != 0]
        last_error = ""
        text = ""
        model_id = provider = None
        last_truncated = False

        # First pass: get an answer from the first working model.
        for model_id, provider in ordered:
            text = ""
            flag = {"truncated": False}
            try:
                for tok in stream_chat(model_id, provider, messages, flag):
                    text += tok
                    self.q.put(("token", tok))
                last_truncated = flag["truncated"]
                if text.strip():
                    break
                last_error = "empty response"
            except Exception as exc:  # noqa: BLE001 - report to UI, keep fallback chain
                last_error = str(exc).strip().replace("\n", " ")[:120]
                if len(text.strip()) >= 40:
                    self.q.put(("done", text, True))
                    return
            time.sleep(0.5)

        if not text.strip():
            self.q.put(("error", last_error or "empty response"))
            return

        # Continuation passes: if the answer is cut off, ask the same model to
        # finish it instead of dumping a truncated blob on the learner.
        rounds = 0
        while looks_truncated(text, last_truncated) and rounds < 2:
            rounds += 1
            cont_messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user",
                 "content": "Continue exactly where you left off. Do not repeat any "
                            "text you already wrote."},
            ]
            flag = {"truncated": False}
            try:
                for tok in stream_chat(model_id, provider, cont_messages, flag):
                    text += tok
                    self.q.put(("token", tok))
                last_truncated = flag["truncated"]
            except Exception as exc:  # noqa: BLE001 - give up on continuing
                last_truncated = True
                break

        self.q.put(("done", text, looks_truncated(text, last_truncated)))

    def _poll_queue(self):
        try:
            while True:
                kind, *payload = self.q.get_nowait()
                if kind == "token":
                    self._insert(payload[0], ("ai_text",))
                elif kind == "done":
                    self._finish_assistant(payload[0], error=None, truncated=payload[1])
                elif kind == "error":
                    self._finish_assistant(None, error=payload[0])
                elif kind == "run_out":
                    out, button, ok, stdout, stderr = payload
                    self._show_result(out, button, (ok, stdout, stderr))
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _finish_assistant(self, text, error, truncated=False):
        self.text.configure(state="normal")
        self.text.delete(self.block_start, "end-1c")
        self.text.configure(state="disabled")
        if error:
            self._insert(f"!!! the tutor models are busy ({error}). try again in a moment.\n",
                         ("error",))
            self.set_status("error")
        else:
            self._insert_parsed(text, "assistant")
            if truncated:
                self._insert(
                    "\n[answer cut off at the output limit - say 'continue' to get the rest]\n",
                    ("error",),
                )
                self.set_status("answer truncated")
            else:
                self.set_status("ready")
            self.history.append({"role": "assistant", "content": text})
        self.loading = False
        self.run_btn.configure(state="normal")


def main():
    root = tk.Tk()
    TutorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()