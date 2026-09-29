# Jev Harness

Voice control for your PC. Hold **Right Ctrl**, say what you want ("open Steam and search for the Witcher"),
let go, and it does it.

> **Windows only.** Jev Harness runs on Windows 10 and 11 and nothing else. It is built on Windows-specific
> parts throughout: Windows' built-in OCR engine (WinRT), Win32 mouse, keyboard and window control, the Start
> menu's app list, Windows Credential Manager for API keys, and optionally PowerToys. It will not run on macOS or
> Linux, including under WSL.

## How it works

- **Whisper** (`large-v3-turbo`, on your NVIDIA GPU) turns your speech into text, locally.
- **Windows OCR** reads the screen the moment you press the key, so it's ready when you let go.
- **[Jev](https://docs.typesafe.ai)** (TypeSafe's System One model) makes each decision: which action, which
  on-screen element, which app, whether the request is done yet. Code lists the candidates and Jev picks one,
  with a probability, so it never has to invent coordinates or text. When it isn't confident, it asks you.
- An optional **AI planner** (any model on OpenRouter, or a local model through Ollama) rewrites a request into
  simple steps when Jev is confused, or when the request needs steps you didn't say.

## Requirements

- **Windows 10 or 11** (see above)
- **Python 3.14** from [python.org](https://www.python.org/downloads/windows/), available as `py -3.14`
- **An NVIDIA GPU** with a recent driver. Whisper falls back to the CPU without one, which is much slower.
- **A microphone**
- **A [TypeSafe API key](https://console.typesafe.ai/keys)**
- Optional: an [OpenRouter key](https://openrouter.ai/keys), or [Ollama](https://ollama.com) running locally,
  for the AI planner
- Optional: [PowerToys](https://learn.microsoft.com/windows/powertoys/) (Command Palette or PowerToys Run) for
  PC search
- An OCR language installed in Windows (English is, by default)

## Setup

In PowerShell, from the project folder:

```powershell
.\setup.ps1            # add -Startup to also launch it when you sign in
```

This creates `.venv`, installs `requirements.txt` and adds a **Jev Harness** shortcut to the Start menu (and
the project folder). If PowerShell refuses to run the script, allow local scripts for your account first:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Then:

1. Start **Jev Harness** from the Start menu. The first start downloads the Whisper model (~1.6 GB).
2. Right-click the tray icon → **Settings** → **Jev** tab, and paste your TypeSafe key.
3. Optionally set up the **AI planner** and **PC search** tabs.
4. Press **Test connections**, then **Save**.

It runs without a console window. To see its log output while debugging, run it with a console instead:
`.venv\Scripts\python -m jevharness`.

## Using it

| Do | What happens |
|---|---|
| Hold **Right Ctrl**, speak, release | Carries out the request. The indicator above the taskbar shows progress. |
| Answer a question | When it asks (e.g. "Click which one? 1) … 2) …"), hold Right Ctrl and say the answer: "the first one", a name, or the text to type |
| **Esc** (or Right Ctrl) while it's working | Stops |
| Click **✓** on the indicator while it's working | Tells it the task is done, so it stops (shown as **Done ✓**) |
| Tray icon → **Dry run** | Highlights what it would do, without doing it |

Things you can say:

- **Open or switch to apps:** "open Discord", "open a new Brave window". If the app is already open it switches
  to it, unless you ask for a new window.
- **Click, type, press keys, scroll:** "click Library", "type hello into the search box", "press Ctrl+S",
  "scroll down"
- **Several steps at once:** "open Notepad, then type hello and press Enter"
- **Implied steps:** "open YouTube in Brave", "find the Witcher 3 in my Steam library". It keeps going until
  Jev judges the request done.
- **Search the PC:** "find my budget spreadsheet", "open display settings". This uses the Start menu, or
  PowerToys if enabled; Jev then picks the matching result.

Along the way it waits for slow apps and pages to load, looks elsewhere (e.g. a Store tab) when what it needs
isn't on screen, and asks you when it's unsure or stuck.

### The indicator

A small dot above the taskbar while idle. It shows a pill with progress while you speak and while it works. It
stays on top of other windows, but hides while idle when a fullscreen app, game or video is in front on the main
screen.

## Settings

Right-click the tray icon → **Settings**.

| Tab | What's there |
|---|---|
| **Jev** | TypeSafe API key and model, the confidence thresholds for acting, dry run |
| **AI planner** | Provider (off, OpenRouter, Ollama), model (**Load list** shows what's available), key or server URL, when to use it (only when Jev is confused, or for every command), whether to send a screenshot (needs a vision model), and for Ollama whether to keep the model loaded in memory |
| **PC search** | Search with PowerToys instead of the Start menu, and its shortcut (default `left alt+space`; **Record** captures a new one, **Detect** reads it from PowerToys) |
| **Indicator** | Background and text colour, opacity, and the dot colour for each state. Changes preview live. |

## Privacy

- **Your voice never leaves your PC.** Whisper runs locally.
- **Sent to TypeSafe for each decision:** your transcribed command, the active window's title and the text
  OCR read on screen. No screenshots are sent.
- **Sent to the AI planner, only when it's used:** the same text, plus a screenshot if **Send a screenshot** is
  on. With OpenRouter this goes to OpenRouter and the model's provider; with Ollama it stays on your PC.
- API keys are kept in Windows Credential Manager, never in files. Settings and logs are in
  `%APPDATA%\JevHarness`.

## Safety and limitations

- **It controls your real mouse and keyboard.** Use **Dry run** to try things out, and **Esc** to stop at any
  time. The AI planner is told to ask before anything that deletes data, spends money, sends a message or
  changes security settings, but check what it's doing.
- It only sees **text**: icon-only buttons can't be clicked by name ("open <app>" still works for apps).
- It can't see or control windows running **as administrator**, unless it's also run as administrator.
- The microphone stays open while the app runs so recording starts instantly, so Windows shows its
  microphone-in-use indicator.
- Pressing Right Ctrl together with another key (e.g. Right Ctrl+C) is left alone as a normal shortcut.

## Troubleshooting

Logs, including every decision Jev made and its probabilities, are in `%APPDATA%\JevHarness\logs`
(tray icon → **Open logs folder**): `app.log` for the app, `commands-<date>.jsonl` for each command.

| Problem | Try |
|---|---|
| "CUDA unavailable: transcribing on CPU" | Update the NVIDIA driver. The CUDA libraries themselves come from `requirements.txt`. |
| "Windows OCR is unavailable" | Add a language with OCR support: Settings → Time & language → Language & region |
| Nothing happens on Right Ctrl | Check the tray icon is there; the focused app may be running as administrator |
| "Add TypeSafe API key" on the indicator | Add it in Settings → Jev, then **Test connections** |
| PC search types into the wrong place | In Settings → PC search, press **Detect** or **Record** to match your PowerToys shortcut |

## Uninstalling

Quit it from the tray icon, delete the project folder, the **Jev Harness** shortcuts (Start menu, and Startup
if you used `-Startup`) and `%APPDATA%\JevHarness`. The API keys are under **JevHarness** in Credential Manager.

## How it fits together

| Module | Role |
|---|---|
| `app.py` | Push-to-talk, the step loop (resolve, act, check done, re-plan), clarifying questions |
| `decide.py` | Jev questions: splitting a request into steps, the action for a step, done / loading / ambiguity checks |
| `llm.py` | AI planner over an OpenAI-compatible chat API (OpenRouter, Ollama), plus Ollama keep-alive |
| `perception.py` | Screenshot + Windows OCR → text elements with screen positions |
| `desktop.py` | Win32: foreground and open app windows, switching to a window, fullscreen detection, waiting for the screen to settle |
| `executor.py` | Mouse, keyboard, launching and switching apps, PC search |
| `stt.py`, `audio.py` | Whisper on CUDA; microphone capture with a short pre-roll |
| `apps.py` | Installed Start-menu apps, for "open <app>" |
| `search.py` | Searching the PC: PowerToys via its shortcut, or the Start menu |
| `overlay.py` | The status indicator (with its ✓ button) and the highlight around the element being acted on |
| `tray.py`, `settings_dialog.py` | The tray icon and Settings window |
| `settings.py` | Settings file and API keys |

Whisper must load before anything in the process initialises COM (Windows OCR, PortAudio, the tray icon), or
CTranslate2 crashes. That's why `.audio` and `.perception` are imported late in `app.py`.
