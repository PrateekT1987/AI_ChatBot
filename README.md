# AI_ChatBot — Python Tutor Console

An interactive, REPL-style Python tutoring chat console built with React + Vite. Ask
anything about Python — it responds with explanations, runnable code examples, and a
follow-up question to keep the lesson moving.

## Features

- Beginner / Intermediate / Advanced difficulty levels with tailored prompts
- Streaming responses (tokens appear as they generate)
- Automatic model fallback: if a provider errors or rate-limits, the next model is tried
- API keys stay server-side (injected by a Vite dev-server proxy, never shipped to the browser)

## Providers

Models are tried in order and fall back automatically:

| Priority | Model | Provider |
| --- | --- | --- |
| 1 | `gemini-3.7-flash` | Google AI Studio |
| 2 | `Qwen/Qwen2.5-7B-Instruct` | SiliconFlow |
| 3 | `Qwen/Qwen3-8B` | SiliconFlow |
| 4 | `big-pickle` | OpenCode Zen |
| 5 | `mimo-v2.5-free` | OpenCode Zen |

## Getting started

```bash
npm install
npm run dev
```

### API keys

Copy `.env.example` style keys into `.env` (already git-ignored):

```env
VITE_OPENCODE_API_KEY=sk-...            # opencode.ai/auth
VITE_SILICONFLOW_API_KEY=sk-...         # cloud.siliconflow.cn
VITE_GEMINI_API_KEY=...                 # aistudio.google.com/apikey
```

The Vite dev server proxies `/api/*` to the providers, injecting each key server-side.

## Scripts

```bash
npm run dev      # start dev server
npm run build    # production build
npm run lint     # oxlint
npm run preview  # preview the production build
```