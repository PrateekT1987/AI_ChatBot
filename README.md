# AI_ChatBot — Python Tutor Console

An interactive, REPL-style Python tutoring chat. Ask anything about Python — it
responds with explanations, runnable code examples, and a follow-up question to keep
the lesson moving.

Two frontends share the same providers and model fallback:

- **Desktop app** — pure-Python Tkinter GUI (`python_tutor_gui.py`), recommended.
- **Browser app** — the original React + Vite console.

## Features (desktop app)

- Beginner / Intermediate / Advanced difficulty levels with tailored prompts
- Streaming responses (tokens appear as they generate) from 5 models with automatic fallback
- **Run any code block** the tutor writes: a "run this code" button executes it with your
  local Python in an isolated temp dir (stdin closed, 10s kill, output truncated)
- **Sandbox** toggle in the header: a free-form scratchpad to write and run your own code,
  auto-saved to `scratchpad.py`
- **Auto-continue**: if an answer is cut off at the model's output limit, the app
  automatically asks the model to finish it (up to 2 extra rounds) instead of leaving a
  truncated answer

## Providers

Models are tried in order and fall back automatically:

| Priority | Model | Provider |
| --- | --- | --- |
| 1 | `gemini-3.7-flash` | Google AI Studio |
| 2 | `Qwen/Qwen2.5-7B-Instruct` | SiliconFlow |
| 3 | `Qwen/Qwen3-8B` | SiliconFlow |
| 4 | `big-pickle` | OpenCode Zen |
| 5 | `mimo-v2.5-free` | OpenCode Zen |

## Getting started — desktop app

Requirements: Python 3.10+, tkinter, `requests`.

```bash
sudo apt install python3-tk     # Ubuntu/Debian (others: python3-tk equivalent)
python3 -m pip install requests
./run_tutor.sh                  # checks deps, installs missing pieces, launches the GUI
```

Or directly:

```bash
python3 python_tutor_gui.py
```

### API keys

Copy keys into `.env` (already git-ignored):

```env
VITE_OPENCODE_API_KEY=sk-...            # opencode.ai/auth
VITE_SILICONFLOW_API_KEY=sk-...         # cloud.siliconflow.cn (needs account balance)
VITE_GEMINI_API_KEY=...                 # aistudio.google.com/apikey
```

### Security note

`run this code` and the Sandbox execute snippets with **your normal user privileges** in
an isolated temp directory. The app warns once before the first run. Only run code you trust.

## Getting started — browser app (React + Vite)

```bash
npm install
npm run dev
```

The Vite dev server proxies `/api/*` to the providers, injecting each key server-side
(keys never ship to the browser).

## Scripts

```bash
./run_tutor.sh    # desktop app launcher (checks deps first)
npm run dev      # browser dev server
npm run build    # production build
npm run lint     # oxlint
npm run preview  # preview the production build
```