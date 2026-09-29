# Jev Harness

Voice control for your PC. Hold **Right Ctrl**, say what you want ("open Steam and search for the Witcher"),
let go, and it does it.

> **Windows only.** Jev Harness runs on Windows 10 and 11 and nothing else. It is built on Windows-specific
> parts throughout: Windows' built-in OCR engine (WinRT), Win32 mouse, keyboard and window control, the Start
> menu's app list, Windows Credential Manager for API keys, and optionally PowerToys. It will not run on macOS or
> Linux, including under WSL.

## How it works

**Whisper** (`large-v3-turbo`, on your NVIDIA GPU) turns your speech into text, locally, while **Windows OCR**
reads the screen (it starts the moment you press the key, so it's ready when you let go). Then each request climbs
three stages, only going further when the stage before can't solve it:

```
 1. OCR + Jev          fast, every command          →  can't find it / doesn't understand
 2. AI assist          optional, only when needed   →  still unsure, or needs your decision
 3. Ask you            numbered options, by key or voice
```

### Stage 1: can OCR and Jev solve it?

Every command starts here, with no generative AI involved. **[Jev](https://docs.typesafe.ai)** (TypeSafe's
System One model) makes each decision from what OCR read on screen: which action, which on-screen element, which
app, what text to type. Code lists the candidates and Jev picks one, with a probability, so it never has to invent
coordinates or text, and only acts when it's confident enough (thresholds in Settings → Jev).

If what it needs isn't apparent, Jev and OCR look harder before giving up:

- **waits** for a slow app or page to load (up to 5 s)
- **tries the most likely tab or menu** (e.g. a Store tab), up to 3 clicks
- **reads the screen again at 2× zoom** to catch small text
- **scrolls** through the window, and back if that didn't help

After the last step, Jev checks the screen to judge whether the whole request is **done**. If it isn't (e.g.
"open YouTube in Brave" after only opening Brave), Jev chooses the next action itself and keeps going.

### Stage 2: can the AI assist?

Only if an **AI planner** is set up (any model on OpenRouter, or a local model through Ollama), and only when
stage 1 is stuck: a step failed, Jev didn't understand what you said, or the request isn't done and Jev can't see
what to do next.

- **Rewriting the steps:** the AI gets your request, what went wrong, what's on screen (the text with positions,
  plus a screenshot if **Send a screenshot** is on), and the **run journal**: every action so far with what it
  changed on screen and whether Jev judged it worked, plus failed steps, dead ends, earlier plans and your
  answers. So in a long run it builds on what worked and doesn't repeat what failed. It returns simple, literal steps
  (`click "LIBRARY"` → `type "Witcher 3" into "Search"`), and each one goes back through Jev, which checks it
  against the real screen.
- **Looking at the screenshot:** a **vision model** points out icons, images and colours OCR can't read ("open
  the settings gear", "click the red button"). What it finds becomes something Jev can pick.
- **Guided exploring:** the AI suggests places to try (a sidebar, a "More" menu, going back, or a closer look at
  one corner) and the screen is re-read after each.

Exploring is capped at 8 actions and a minute per step. It never clicks things like Delete, Buy, Send or Sign
out, and Esc stops it. (Settings → AI planner can also set the AI to rewrite *every* command up front.)

### Stage 3: can you tell it more?

When neither stage can settle something, it asks you. Every question lists **numbered options**, ending with
**Cancel**. Press the number, or hold Right Ctrl and say it ("two", "the second one"); where it makes sense you
can also just say your answer. It asks when:

- **Jev is torn between specific things on screen** ("Click which one? 1) … 2) …"). This is asked straight
  away, since you can answer faster than the AI could guess.
- **it can't tell what text to type or search for** (it offers its best guesses from your words)
- **the AI needs to know what you want**, or you're asking for something that deletes data, spends money,
  sends a message or changes security settings
- **it's stuck** after trying for 3 rounds ("I'm stuck on …. What should I do next?"), or didn't understand you
- it has done **20 actions** beyond what you said ("Keep going?")

If none of the options fit, choose **None of these: have the AI rethink**, or just say what you meant. That goes
back to stage 2 with your answer, and the AI works out new steps.

**YOLO mode** replaces this stage: it decides everything itself (see [YOLO mode](#yolo-mode)).

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
| Answer a question | Questions always list numbered options (the last is Cancel). Press the number, or hold Right Ctrl and say it ("two", "the second one"). Where it makes sense, e.g. "What should I type?", you can also just say your answer. |
| **Esc** (or Right Ctrl) while it's working | Stops |
| Click **✓** on the indicator while it's working | Tells it the task is done, so it stops (shown as **Done ✓**) |
| Tray icon → **Dry run** | Highlights what it would do, without doing it |
| Tray icon → **YOLO mode** | Decides everything itself instead of asking (see below) |

Things you can say:

- **Open or switch to apps:** "open Discord", "open a new Brave window". If the app is already open it switches
  to it, unless you ask for a new window.
- **Click, type, press keys, scroll:** "click Library", "type hello into the search box", "press Ctrl+S",
  "scroll down"
- **Drag:** "drag the budget file into the Archive folder", "move Fix the login bug to Done", "drag the volume
  slider to the right"
- **Several steps at once:** "open Notepad, then type hello and press Enter"
- **Implied steps:** "open YouTube in Brave", "find the Witcher 3 in my Steam library". It keeps going until
  Jev judges the request done.
- **Search the PC:** "find my budget spreadsheet", "open display settings". This uses the Start menu, or
  PowerToys if enabled; Jev then picks the matching result.

- **Window commands:** "minimise this window", "maximise it", "snap it to the left"

### YOLO mode

Turn it on from the tray menu or Settings → Jev, and stage 3 never waits for you:

- **Questions:** it takes the most likely option, after Jev checks that it actually fits the request. If it
  doesn't, the AI rethinks instead (stage 2).
- **The AI** is told to decide rather than ask.
- **When stuck,** it tries a different approach, and stops after two attempts.
- **Instead of "Keep going?"** it stops after 30 actions beyond what you said.

The idle dot turns purple while it's on; Esc and ✓ still stop it.

By default it **still refuses irreversible actions**: before each action Jev judges whether it would be hard to
undo (deleting, buying, sending a message, signing out, changing security settings), and if so it stops rather
than doing it. This errs on the cautious side, e.g. it won't even draft a message to someone. Untick
"…but still refuse irreversible actions" to allow them; then nothing asks you before deleting, buying or sending.

### The indicator

A small dot above the taskbar while idle. It shows a pill with progress while you speak and while it works, and
grows into a box (wrapping onto more lines as needed) when it asks you something. It
stays on top of other windows, but hides while idle when a fullscreen app, game or video is in front on its
screen.

To move it, choose tray icon → **Move indicator**, drag it anywhere (any monitor) and double-click to drop it
there; **Reset indicator position** puts it back. Settings → **Indicator** also has preset positions. The dot
stays put and the text grows out of it, towards the middle of the screen: on the right-hand side the pill is
mirrored, with the text to the left of the dot.

## Settings

Right-click the tray icon → **Settings**.

| Tab | What's there |
|---|---|
| **Jev** | TypeSafe API key and model, the confidence thresholds for acting, dry run, YOLO mode (and whether it still refuses irreversible actions) |
| **AI planner** | Provider (off, OpenRouter, Ollama), model (**Load list** shows what's available), key or server URL, when to use it (only when Jev is confused, or for every command), whether to send a screenshot (needs a vision model), and for Ollama whether to keep the model loaded in memory. Choosing a different Ollama model and pressing **Test** or **Save** unloads the previous one and loads the new one. The **vision model** (the planner's by default) finds icons and images on the screenshot; it has to point accurately, which **Test connections** checks (e.g. `qwen3.8` can; `gemma4` describes screens well but can't). |
| **PC search** | Search with PowerToys instead of the Start menu, and its shortcut (default `left alt+space`; **Record** captures a new one, **Detect** reads it from PowerToys) |
| **Indicator** | Background and text colour, opacity, the dot colour for each state, and its position (presets, **Drag…** to place it anywhere, or **Reset position**). Changes preview live. |

## Privacy

- **Your voice never leaves your PC.** Whisper runs locally.
- **Sent to TypeSafe for each decision:** your transcribed command, the active window's title and the text
  OCR read on screen. No screenshots are sent.
- **Sent to the AI planner, only when it's used:** the same text with each item's position on screen, plus a
  screenshot if **Send a screenshot** is on. With OpenRouter this goes to OpenRouter and the model's provider; with Ollama it stays on your PC.
- API keys are kept in Windows Credential Manager, never in files. Settings and logs are in
  `%APPDATA%\JevHarness`.

## Safety and limitations

- **It controls your real mouse and keyboard.** Use **Dry run** to try things out, and **Esc** to stop at any
  time. The AI planner is told to ask before anything that deletes data, spends money, sends a message or
  changes security settings, but AI models don't always follow such instructions, so check what it's doing.
- Without a vision model it only sees **text**: icon-only buttons can't be clicked by name ("open <app>" still
  works for apps). Vision models vary a lot at pointing accurately; **Test connections** checks yours.
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
| `app.py` | Push-to-talk and the three stages: the step loop (resolve, explore, act, check done), handing over to the AI, questions, YOLO mode |
| `decide.py` | Jev questions: splitting a request into steps, the action for a step (including drags), done / loading / ambiguity / option-fit / irreversible checks |
| `llm.py` | The AI over an OpenAI-compatible chat API (OpenRouter, Ollama): planning steps, exploring, locating things on the screenshot; Ollama keep-alive |
| `perception.py` | Screenshot + Windows OCR → text elements with screen positions |
| `desktop.py` | Win32: foreground and open app windows, switching to a window, fullscreen detection, waiting for the screen to settle |
| `executor.py` | Mouse (clicks, drags, scrolling), keyboard, launching and switching apps, PC search |
| `stt.py`, `audio.py` | Whisper on CUDA; microphone capture with a short pre-roll |
| `apps.py` | Installed Start-menu apps, for "open <app>" |
| `search.py` | Searching the PC: PowerToys via its shortcut, or the Start menu |
| `overlay.py` | The status indicator (with its ✓ button) and the highlight around the element being acted on |
| `tray.py`, `settings_dialog.py` | The tray icon and Settings window |
| `journal.py` | The run journal: each action, what it changed and whether it worked, for the AI's context |
| `settings.py` | Settings file and API keys |

Whisper must load before anything in the process initialises COM (Windows OCR, PortAudio, the tray icon), or
CTranslate2 crashes. That's why `.audio` and `.perception` are imported late in `app.py`.
