# LLM Assistant

A local KDE Plasma and PyQt6 desktop assistant backed by an OpenAI-compatible
`llama-server` endpoint.

## Features

- Streaming local-LLM chat UI with a system tray icon
- KDE Plasma 6 widget through a D-Bus backend
- Optional voice input through `~/.local/bin/voice-control-run`
- Fixed volume and brightness controls
- Tool calling with confirmation gates, sensitive-path protection, and audit logs

## Layout

- `scripts/llm-assistant` — starts the model server when needed, then launches the PyQt UI
- `scripts/llm-assistant-qt.py` — floating desktop UI
- `scripts/llm-assistant-backend.py` — D-Bus and Unix-socket backend for the Plasma widget
- `scripts/llama-serve` — local model launcher and model-alias configuration
- `scripts/llm_core/` — client, agent loop, tools, configuration, and security policy
- `plasmoids/llm-assistant/` — Plasma 6 widget
- `scripts/tests/` — unit tests

Model files, llama.cpp sources, and compiled binaries are intentionally excluded.

## Requirements

The current setup targets Arch Linux with KDE Plasma 6. Runtime dependencies include:

- Python 3.10+
- PyQt6
- dbus-python and PyGObject for the Plasma backend
- fish, curl, lsof, pactl, and brightnessctl
- a llama.cpp build exposing an OpenAI-compatible server

The bundled `llama-serve` script expects binaries below `~/ai/llama.cpp-build/bin`
and GGUF models below `~/ai/models`. Adjust it for other layouts.

## Usage

Start the floating assistant:

```bash
scripts/llm-assistant
```

Install the Plasma widget:

```bash
scripts/install-plasmoid.sh
```

Configuration is read from `~/.config/llm-assistant/config.json`. Environment
variables such as `LLM_URL`, `LLM_PORT`, and `LLM_MODEL` can override LLM settings.

## Security model

- Dangerous command patterns are blocked.
- Every arbitrary shell command and every file write requires explicit confirmation.
- Reads outside configured roots require confirmation.
- Sensitive paths and virtual/device trees such as `/proc`, `/sys`, and `/dev` are blocked.
- Audit logs redact written content and are stored with mode `0600`.

The assistant still executes commands with the current user's privileges. Review every
confirmation prompt and do not treat model output as trusted instructions.

## Tests

```bash
python -m pytest -q
```
