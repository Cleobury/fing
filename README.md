<p align="center"><img src="docs/logo.png" width="112" alt="Fing logo: a gloved hand pointing up"></p>

# Fing

**A desktop assistant that uses your computer for you.** Tell it what you want done, out loud or from your
phone, and Fing reads the screen, moves the mouse, clicks, types and presses keys the way you would. It works
with whatever is on your screen, in any app, without needing the app to support it.

Fing is short for *fingers*: it extends what you can do with a computer by being the hands on the mouse and
keyboard. That makes it useful to anyone with their hands full, and especially to people for whom a mouse and
keyboard are slow, tiring, painful or impossible to use.

You can rename it: call it anything you like in Settings → General, and that becomes the name you say to wake
it ("Hey Fing" by default).

> **Windows only.** Fing runs on Windows 10 and 11. It relies on Windows' built-in OCR engine (WinRT), Windows
> accessibility (UI Automation), Win32 mouse, keyboard and window control, the Start menu's app list and
> Windows Credential Manager. It will not run on macOS or Linux, including under WSL.

## Who it's for

- **People with limited hand or arm use**: RSI, arthritis, tremor, paralysis, amputation, or a temporary
  injury. Fing can be driven entirely by voice, with no key presses at all.
- **People who tire quickly or are in pain**: long sequences of small, precise movements (menus, drag and
  drop, form fields) become one sentence.
- **People with low vision**, as a helper: it can find and press something by its name even when it's hard
  to locate on screen. It is **not a screen reader**, though: it doesn't speak, so it works best alongside
  one (see [Limitations](#safety-and-limitations)).
- **Anyone** who wants to say what they want rather than click through it, or whose hands are busy.

## What it can do

Fing understands requests in plain language and works out the clicks and keystrokes itself:

- **Use any app on the screen**: press buttons and links, pick from menus and tabs, fill in fields, tick
  boxes, press keys and shortcuts, scroll, and drag one thing onto another. It knows controls by their visible
  text and by the names Windows accessibility gives them, so icon-only buttons work too.
- **Do several things at once**, and **finish what you meant**: requests often imply more than you said, so
  after the last step it checks the screen and keeps going until the request is actually done.
- **Find its way**: if what it needs isn't visible, it waits for things to load, tries the most likely tab or
  menu, looks more closely at the screen and scrolls, before asking you.
- **Find things on the PC**: files, folders, apps and Windows settings, through the Start menu or PowerToys.
- **Manage windows**: open and switch between apps, minimise, maximise and snap.
- **Repeat routines** you save as plain-language [scripts](#scripts), the same way every time.
- **Ask when unsure**, with numbered options you can answer by voice, and **stop the moment you say so**.

Some things you might say: "click the second link", "scroll down to the comments", "tick remember me and sign
in", "move this file into the Archive folder", "turn on dark mode", "make the text bigger", "reply to this and
say I'll be there at six".

## Accessibility features

| Feature | What it does |
|---|---|
| **Wake word** | Say "Hey Fing" (or your own phrase) instead of holding a key. Nothing leaves the PC until you give a command. |
| **Answering by voice** | Every question lists numbered options. Say "two" or "the second one", or just say your answer. With **Listen for my answer automatically**, the mic opens by itself after each question. |
| **Phone remote** | A big hold-to-talk button on your phone, over your Wi-Fi: use your phone's microphone from bed, a wheelchair tray or across the room. |
| **One key, if you use keys** | Everything else can be done by holding **Right Ctrl** and speaking. **Esc** stops. |
| **Clear feedback** | The indicator above the taskbar says what it heard and what it's doing. A hand taps whatever Fing clicks, so you can see where it acted; the target is outlined too. Chimes mark the microphone opening and closing. |
| **Adjustable indicator** | Colours (including each state's dot), opacity and position, for high contrast or to keep it out of the way. The hand animations can be switched off. |
| **Fewer interruptions** | **YOLO mode** makes every decision itself, while still refusing anything hard to undo unless you allow it. |
| **Try before trusting** | **Dry run** shows what it would do without doing it. |

## How it works

**Whisper** (`large-v3-turbo`, on your NVIDIA GPU) turns your speech into text, locally, while **Windows OCR**
and **UI Automation** read the screen (starting the moment you begin talking, so it's ready when you finish).
Each request then climbs three stages, only going further when the one before can't solve it:

```
 1. Screen + TypeSafe   fast, every command          →  can't find it / doesn't understand
 2. AI assist           optional, only when needed   →  still unsure, or needs your decision
 3. Ask you             numbered options, by voice, key or phone
```

### 1. Reading the screen and deciding

Every command starts here, with no generative AI involved. The **[TypeSafe API](https://docs.typesafe.ai)**
(its `jev-latest` classifier) makes each decision from what's on screen: which action, which element, which
app, what text to type. Code lists the candidates and the classifier picks one, with a probability, so it never
invents coordinates or text, and only acts when it's confident enough (thresholds in Settings → TypeSafe).

Along with the OCR text it reads the active window's **named controls** from Windows accessibility: icon-only
buttons, tabs and unlabelled text boxes. With several monitors it reads **all of them**, so you can say "on my
left screen". If what it needs isn't apparent it looks harder before giving up: it waits for loading (up to
5 s), tries the most likely tab or menu (up to 3 clicks), re-reads the screen at 2×, each quarter at 3× and as a
high-contrast negative (light text on dark themes), and scrolls.

After the last step it judges whether the whole request is **done**, and if not, chooses the next action
itself, getting up to **2 actions of its own** before the AI planner is called.

### 2. AI assist (optional)

Only if an **AI planner** is set up (any model on OpenRouter, or a local model through Ollama), and only when
stage 1 is stuck.

- **Rewriting the steps:** the AI gets your request, what went wrong, what's on screen (text with positions,
  plus a screenshot if you allow it), and the **run journal**: every action so far, what it changed and whether
  it worked. It returns simple, literal steps, and each one goes back through stage 1, which checks it against
  the real screen.
- **Looking at the screenshot:** a **vision model** finds icons, images and colours OCR can't read ("the gear
  icon", "the red button"), and what it finds becomes something stage 1 can pick.
- **Guided exploring:** the AI suggests places to look (a sidebar, a "More" menu, going back, a closer look at
  one corner), and the screen is re-read after each. Capped at 8 actions and a minute per step; it never clicks
  things like Delete, Buy, Send or Sign out.

### 3. Asking you

When neither stage can settle something, it asks: when it's torn between things on screen, can't tell what to
type, needs your decision (anything that deletes data, spends money, sends a message or changes security
settings), is stuck after 3 rounds, or has done 20 actions beyond what you said. Every question ends with
**Cancel**, and **None of these: have the AI rethink** (or saying what you meant) sends it back to stage 2.

## Requirements

- **Windows 10 or 11**
- **Python 3.14** from [python.org](https://www.python.org/downloads/windows/), available as `py -3.14`
- **An NVIDIA GPU** with a recent driver (Whisper falls back to the CPU without one, much more slowly)
- **A microphone** (or a phone, with the phone remote)
- **A [TypeSafe API key](https://console.typesafe.ai/keys)**
- Optional: an [OpenRouter key](https://openrouter.ai/keys), or [Ollama](https://ollama.com) running locally,
  for the AI planner
- Optional: [PowerToys](https://learn.microsoft.com/windows/powertoys/) (Command Palette or PowerToys Run) for
  PC search
- An OCR language installed in Windows (English is, by default)

## Setup

In PowerShell, from the project folder:

```powershell
.\setup.ps1            # add -Startup to also launch it when you sign in (or tick Start with Windows later)
```

This creates `.venv`, installs `requirements.txt` and adds a **Fing** shortcut to the Start menu and the
project folder (replacing any old **Jev Harness** ones). If PowerShell refuses to run the script, allow local
scripts for your account first: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Then:

1. Start **Fing** from the Start menu. The first start downloads the Whisper model (~1.6 GB). A hand waves
   hello in the middle of the screen when it's ready.
2. Right-click the tray icon (the hand) → **Settings** → **TypeSafe**, paste your key and press **Test key**.
3. Optionally give it a name and turn on **Start with Windows** (General), the wake word (Hands-free), the phone
   remote (Phone) and the AI planner.
4. Press **Save**.

It runs without a console window. To see its log while debugging, run `.venv\Scripts\python -m jevharness`.

**Upgrading from Jev Harness:** settings, API keys and logs carry over (they stay in `%APPDATA%\JevHarness`),
and a wake phrase you saved, like "hey jev", keeps working until you change it.

## Using it

| Do | What happens |
|---|---|
| Hold **Right Ctrl**, speak, release | Carries out the request. The indicator above the taskbar shows progress. |
| Say **"Hey Fing"** (with the wake word on) | Same as holding Right Ctrl: it listens until you stop talking. Say the request in the same breath, or pause after the name. |
| Hold the button on your **phone** | Same as holding Right Ctrl, using the phone's microphone |
| Answer a question | Say the number ("two", "the second one") or your answer, press the number, or tap it on the phone |
| **Esc** (or Right Ctrl) while it's working | Stops |
| Click **✓** on the indicator while it's working | Tells it the task is done, so it stops |
| Tray icon → **Dry run** | Shows what it would do, without doing it |
| Tray icon → **YOLO mode** | Decides everything itself instead of asking |
| Tray icon → **Listen for wake word** | Turns the wake word on or off |

### The indicator and the hand

A small dot above the taskbar while idle. It grows into a pill showing progress while you speak and while it
works, and into a box when it asks you something. While it works, sparks circle the dot; the microphone
opening and closing ripples rings out of it and back in, with a chime.

The **hand** shows what Fing is doing on your behalf: it taps each spot Fing clicks, with a ripple; points
at each numbered option when it asks which one; waves hello in the middle of the screen when it's ready; and
throws confetti when a request is done. When something goes wrong the pill shakes. Settings → Indicator turns
the hand off and has buttons to try each animation: Wave and Tap play big in the middle of the main screen,
Confetti comes out of the dot.

The indicator stays on top of other windows, but hides while idle when a fullscreen app or video is in front.
To move it, choose tray icon → **Move indicator**, drag it anywhere and double-click to drop it there.

### Hands-free

Settings → **Hands-free**:

- **Listen for a wake word.** Any phrase works straight away, with no training. It's rated as you type: one
  word, or everyday words only, go off easily from normal talk, TV and calls, so it suggests phrases with the
  assistant's name in. **Test** shows what it heard and how often the phrase would have gone off, and
  **Sensitivity** trades false triggers against missed ones. Renaming the assistant (General) renames the
  phrase with it.
- **Listen for my answer automatically.** When it asks a question, the mic opens by itself after the beep and
  closes when you stop talking. If you gave the command from your phone, the phone listens instead.

Speech near the mic is checked on the PC with the Whisper model that's already loaded; nothing leaves the PC
until you give a command. While it's carrying out a command it ignores the wake word, so nothing it plays can
set it off.

### Phone remote

A web page with one big hold-to-talk button, over your Wi-Fi.

1. Settings → **Phone**: tick **Let my phone control this PC on this network**, then **Save**.
2. Scan the QR code with the phone's camera. It opens the page and pairs it with the PIN, so you only do this
   once.
3. The first time, the phone warns that the connection isn't private: the PC makes its own certificate,
   because browsers only allow the microphone over HTTPS. Choose **Advanced → Proceed** (Chrome) or **Show
   Details → visit this website** (Safari). If Windows asks, allow Python on **private** networks.

The page shows what Fing is doing, lets you tap an option when it asks a question, and a tap while it's working
stops it. Add it to your home screen for one-tap access. Anyone on your network with the PIN can control the
PC: **New PIN** signs every phone out, and after 10 wrong PINs the remote refuses everyone for 5 minutes.

### YOLO mode

Turn it on from the tray menu or Settings → General, and it never waits for you: it takes the most likely
option (after checking it fits the request), the AI is told to decide rather than ask, it tries a different
approach when stuck (stopping after two), and it stops after 30 actions beyond what you said. The idle dot
turns purple while it's on; Esc and ✓ still stop it.

By default it **still refuses irreversible actions**: before each action it judges whether it would be hard to
undo (deleting, buying, sending a message, signing out, changing security settings) and stops rather than
doing it. Untick "…but still refuse irreversible actions" to allow them.

### Scripts

Save tasks you do often, or tests you run on an app, as a **script** in Settings → **Scripts**, written in
plain words, one task per line or as a numbered list:

```
1. Open Notepad and type "Hello from Fing"
2. Make sure the title shows it's unsaved
3. Press ctrl+s, wait a couple of seconds, then check a Save As dialog appears
4. Press escape
```

The AI planner breaks it into simple steps (preview them with **Break into steps**); "check / verify / make
sure …" lines become checks and "wait …" a pause. The breakdown is saved and reused until you edit the script,
so every run follows the same steps; without an AI planner, each line is a step. Start one by saying "run the
notepad test", from tray icon → **Run script**, or with **Run now**. **Run unattended** (on by default) decides
everything like YOLO mode; **Stop at the first failed step or check** is on by default too. A report of every
step and check is saved to `%APPDATA%\JevHarness\logs\scripts`.

## Settings

Right-click the tray icon → **Settings**. Pages are in the sidebar; the window follows Windows' light or dark
mode.

| Page | What's there |
|---|---|
| **General** | The assistant's name (and so its wake phrase), microphone, **Start with Windows**, dry run, YOLO mode (and whether it still refuses irreversible actions), and whether to read button names and all your screens |
| **TypeSafe** | The TypeSafe API key and classifier model, with **Test key**, and the confidence it needs before acting on its own |
| **AI planner** | Provider (off, OpenRouter, Ollama), model (**Load list**; a fast model such as `google/gemini-3.8-flash` keeps replies quick, while `openrouter/auto` lets OpenRouter pick and may choose a slower one that deliberates), key or server URL, when to use it (only when the classifier is unsure, or for every command), screenshots, keeping an Ollama model loaded, and the **vision model**, which must point accurately (**Test connections** checks it) |
| **Hands-free** | The wake word: on or off, the phrase, **Test** and sensitivity; listening for answers automatically; chimes |
| **Phone** | The phone remote: on or off, its address, QR code and PIN, and port (default 8765) |
| **PC search** | Search with PowerToys instead of the Start menu, and its shortcut (**Record** or **Detect**) |
| **Scripts** | Write, name, delete and run scripts, and preview their steps |
| **Indicator** | Colours, opacity, each state's dot colour, position, and the hand animations |

## Privacy

- **Your voice never leaves your PC.** Whisper runs locally. The phone remote sends recordings straight to the
  PC over your network (HTTPS). With the wake word on, speech near the mic is transcribed on the PC and thrown
  away unless it's a command.
- **Sent to TypeSafe for each decision:** your transcribed command, the active window's title and the text read
  from the screen (every monitor, unless **Read all my screens** is off). No screenshots.
- **Sent to the AI planner, only when it's used:** the same text with positions, plus a screenshot if allowed.
  With OpenRouter this goes to OpenRouter and the model's provider; with Ollama it stays on your PC.
- API keys are kept in Windows Credential Manager, never in files. Settings and logs are in
  `%APPDATA%\JevHarness`.

## Safety and limitations

- **It controls your real mouse and keyboard.** Use **Dry run** to try things out, and **Esc** (or the
  phone's stop) at any time. The AI planner is told to ask before anything that deletes data, spends money,
  sends a message or changes security settings, but AI models don't always follow instructions, so keep an eye
  on what it's doing.
- **It doesn't speak.** Progress and questions appear on the indicator and the phone page, with chimes. If you
  rely on a screen reader, keep it running alongside; questions can always be answered by number.
- Without a vision model it only sees text and named controls: an unnamed icon can't be clicked by name.
- It can't see or control windows running **as administrator**, unless it's also run as administrator.
- The microphone stays open while it runs, so recording starts instantly; Windows shows its mic-in-use
  indicator.
- Right Ctrl together with another key (e.g. Right Ctrl+C) is left alone as a normal shortcut.

## Troubleshooting

Logs, including every decision and its probabilities, are in `%APPDATA%\JevHarness\logs` (tray icon → **Open
logs folder**): `app.log` for the app, `commands-<date>.jsonl` for each command.

| Problem | Try |
|---|---|
| "Add TypeSafe API key" on the indicator | Settings → TypeSafe, then **Test key** |
| "CUDA unavailable: transcribing on CPU" | Update the NVIDIA driver; the CUDA libraries come from `requirements.txt` |
| "Windows OCR is unavailable" | Add a language with OCR support: Settings → Time & language → Language & region |
| Nothing happens on Right Ctrl | Check the tray icon is there; the focused app may be running as administrator |
| The wake word goes off by itself | Use two words including a name, or move **Sensitivity** towards "Fewer false triggers". **Test** shows what it's hearing. |
| The wake word doesn't respond | Press **Test** and say it; move **Sensitivity** towards "Catches more", or pick a phrase Whisper spells more reliably |
| The phone page can't reach the PC | Both must be on the same network. Allow Python on private networks in Windows Defender Firewall, and check the address in Settings → Phone. |
| The AI planner is slow ("Thinking it through" for half a minute) | Pick a fast model in Settings → AI planner, e.g. `google/gemini-3.8-flash`, rather than `openrouter/auto`. `app.log` records how long each AI call took. |
| PC search types into the wrong place | Settings → PC search → **Detect** or **Record** |

## Uninstalling

Quit it from the tray icon, then delete the project folder, the **Fing** shortcuts (Start menu, and Startup if
you used it) and `%APPDATA%\JevHarness`. The API keys are under **JevHarness** in Credential Manager.

## How it fits together

| Module | Role |
|---|---|
| `app.py` | Push-to-talk and the three stages: the step loop (resolve, explore, act, check done), handing over to the AI, questions, YOLO mode |
| `decide.py` | The TypeSafe classifier's questions: splitting a request into steps, the action for a step, done / loading / ambiguity / option-fit / irreversible checks |
| `llm.py` | The AI over an OpenAI-compatible chat API (OpenRouter, Ollama): planning, exploring, locating things on the screenshot |
| `perception.py`, `accessibility.py` | Screenshot + Windows OCR, and named controls from UI Automation → elements with screen positions |
| `desktop.py`, `settle.py` | Win32 windows, fullscreen detection, and waiting for the screen to be ready after an action |
| `executor.py` | Mouse, keyboard, launching and switching apps, PC search |
| `stt.py`, `audio.py` | Whisper on CUDA; microphone capture |
| `listen.py`, `wakephrase.py` | Hands-free: speech detection, spotting and rating the wake phrase, recording an answer |
| `remote.py`, `web/index.html` | Phone remote: HTTPS server on the local network and the hold-to-talk page |
| `brand.py` | The name, colours and logo (the hand's outlines are shared by the icon and the animations); `python -m jevharness.brand` rebuilds `icon.ico` |
| `overlay.py`, `fx.py` | The indicator (dot, pill, ✓, sparks, shake) and the highlight; the hand animations over the screen |
| `tray.py`, `settings_dialog.py` | The tray icon and Settings window |
| `apps.py`, `search.py`, `autostart.py` | Start-menu apps, PC search, Start with Windows |
| `scripts.py`, `journal.py` | Saved scripts and their reports; the run journal for the AI's context |
| `settings.py` | Settings file and API keys |

The Python package keeps its original name, `jevharness`, so existing shortcuts and settings keep working.
Whisper must load before anything initialises COM (Windows OCR, PortAudio, the tray icon), or CTranslate2
crashes; that's why `.audio` and `.perception` are imported late in `app.py`.

The hands-free logic and the screen-ready check have tests that run anywhere: `pip install pytest`, then
`python -m pytest`.
