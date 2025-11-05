# VFSBot Architecture Overview

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT INTERFACE                      │
│  (/start, /quit, /setting, /help commands)                      │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                     VFSBOT.PY (Main Controller)                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ VFSBot Class                                             │   │
│  │  • __init__: Setup Telegram bot & handlers               │   │
│  │  • start_handler: Spawn login_helper() async task        │   │
│  │  • quit_handler: Stop browser & cleanup                  │   │
│  │  • setting_handler: Update config.ini dynamically        │   │
│  │  • help_handler: Display help                            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Core Automation Flow                                     │   │
│  │  login_helper() ──► login() ──► check_appointment()      │   │
│  │     (async)          (blocking)     (blocking)           │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
        ▼                         ▼
┌──────────────────┐      ┌──────────────────┐
│   CONFIG.INI     │      │   UTILS.PY       │
│                  │      │                  │
│ [VFS]            │      │ break_captcha()  │
│  url             │      │ AdminHandler     │
│  email           │      │ WebError         │
│  password        │      │ Offline (exc)    │
│  interval        │      │                  │
│                  │      │                  │
│ [TELEGRAM]       │      │                  │
│  auth_token      │      │                  │
│  channel_id      │      │                  │
│  admin_ids       │      │                  │
└──────────────────┘      └──────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
                    ▼                           ▼
            ┌─────────────────┐        ┌──────────────────┐
            │ OpenCV + OCR    │        │ Tesseract Engine │
            │ Image Processing│        │ (External Tool)  │
            │ & Enhancement   │        │ captcha.png ────►│
            └─────────────────┘        └──────────────────┘
```

## Component Interactions

```
┌─────────────────────────────────────────────────────────────────────┐
│                         BROWSER AUTOMATION LAYER                     │
│                                                                       │
│  Selenium + undetected-chromedriver                                 │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ Chrome Browser Instance (headful)                          │    │
│  │  • WebDriverWait with extended timeouts                   │    │
│  │  • XPath-based element location                           │    │
│  │  • Screenshot capture for captcha solving                 │    │
│  │  • Form submission & navigation                           │    │
│  └────────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │   VFS GLOBAL WEBSITE                 │
        │   visa.vfsglobal.com                │
        │  ┌───────────────────────────────┐  │
        │  │ 1. Login Page                 │  │
        │  │ 2. Captcha Challenge          │  │
        │  │ 3. Appointment Selection      │  │
        │  │ 4. Available Dates Display    │  │
        │  └───────────────────────────────┘  │
        └──────────────────────────────────────┘
```

## Data Flow: Appointment Check Cycle

```
START
  │
  ├─► READ config.ini
  │    └─► Extract: url, email, password, interval
  │
  ├─► INITIALIZE Chrome browser (undetected-chromedriver)
  │
  ├─► LOGIN LOOP (repeats on failure)
  │    ├─► Navigate to VFS URL
  │    ├─► Fill email + password
  │    ├─► Submit form
  │    ├─► DETECT Captcha
  │    │    ├─► Take screenshot → captcha.png
  │    │    ├─► PROCESS with break_captcha()
  │    │    │    ├─► OpenCV preprocessing (grayscale, threshold, etc.)
  │    │    │    └─► Tesseract OCR recognition
  │    │    └─► Fill captcha field
  │    ├─► Verify login success
  │    └─► Raise WebError/Offline on failure (retry)
  │
  ├─► APPOINTMENT CHECK LOOP (repeats every `interval` seconds)
  │    ├─► Navigate to appointment pages
  │    ├─► Parse XPath selectors for appointment dates
  │    ├─► Extract available dates
  │    ├─► COMPARE with record.txt (dedupe)
  │    ├─► NEW appointment found?
  │    │    ├─► APPEND to record.txt
  │    │    └─► SEND Telegram notification
  │    │         └─► context.bot.send_message(channel_id, message)
  │    └─► Sleep `interval` seconds
  │
  └─► QUIT
       ├─► Close Chrome browser
       └─► Cleanup resources

```

## Key Files & Responsibilities

| File | Purpose | Key Methods |
|------|---------|-------------|
| **VFSBot.py** | Main bot logic & orchestration | `login()`, `check_appointment()`, `login_helper()`, handlers |
| **utils.py** | Helper utilities & shared logic | `break_captcha()`, `AdminHandler`, exceptions |
| **config.ini** | Runtime configuration (mutable) | Sections: `[VFS]`, `[TELEGRAM]` |
| **record.txt** | Persistence for deduplication | Append-only log of sent messages |
| **requirements.txt** | Python dependencies | Pinned versions for reproducibility |

## Async Model

```
Telegram Update Event
         │
         ▼
┌─────────────────┐
│ Command Handler │  (/start received)
│ (event loop)    │
└────────┬────────┘
         │
         ├─ Spawns async task: login_helper()
         │
         ▼
    ┌────────────────────────────────────────┐
    │  login_helper() (runs in background)   │
    │  ┌──────────────────────────────────┐  │
    │  │ while not stopped:               │  │
    │  │  ├─► login() [blocks]            │  │
    │  │  ├─► loop:                       │  │
    │  │  │   ├─► check_appointment()     │  │
    │  │  │   ├─► sleep(interval)         │  │
    │  │  │   └─► handle exceptions      │  │
    │  └──────────────────────────────────┘  │
    └────────────────────────────────────────┘
         │
         └─ Telegram updates processed concurrently
            (user can /quit anytime)
```

## External Dependencies

```
┌────────────────────────────────────────────────────────┐
│           EXTERNAL SERVICES & TOOLS                    │
├────────────────────────────────────────────────────────┤
│ ✓ Telegram Bot API (python-telegram-bot v20+)         │
│   └─ Sends notifications, handles commands            │
│                                                        │
│ ✓ Selenium + undetected-chromedriver                  │
│   └─ Browser automation, anti-detection               │
│                                                        │
│ ✓ Chrome/Chromium Browser                             │
│   └─ Required for headful browser instance            │
│                                                        │
│ ✓ Tesseract OCR (installed separately)                │
│   └─ Captcha text recognition                         │
│                                                        │
│ ✓ OpenCV (opencv-python)                              │
│   └─ Image preprocessing for OCR                      │
│                                                        │
│ ✓ VFS Global Website                                  │
│   └─ Target site for appointment checking             │
└────────────────────────────────────────────────────────┘
```

## Configuration & Persistence

```
RUNTIME CONFIGURATION FLOW
──────────────────────────

1. At startup:
   config = ConfigParser()
   config.read('config.ini')  ◄─── Read from disk

2. During execution:
   /setting command allows updates
   └─► Modified section.key values
   └─► Write back to config.ini
   └─► ConfigParser reloads

3. Persistence:
   record.txt = append-only log
   ├─► Lines = last sent appointment strings
   ├─► Prevents duplicate notifications
   └─► Manual edits allowed (but keep format)
```

## Error Handling Strategy

```
LOGIN PHASE ERRORS:
├─ WebError (custom exception)
│  └─► Retry login immediately
│
├─ Offline (custom exception)
│  └─► Network timeout, wait interval before retry
│
└─ Other exceptions
   └─► Log, send alert to admin, graceful shutdown

APPOINTMENT CHECK ERRORS:
├─ Element not found (XPath selector)
│  └─► Log warning, skip this cycle
│
├─ Network timeout
│  └─► Retry with extended wait
│
└─ Parsing error
   └─► Log, continue to next check cycle
```

## Typical User Workflow

```
User → /start Command
       │
       ├─► Bot initializes Chrome browser
       ├─► Bot logs into VFS website (with captcha solving)
       ├─► Bot enters continuous polling loop
       │
       ├─► [Periodically checks for appointment availability]
       │
       ├─► [When appointment found:]
       │   └─► Sends Telegram notification to channel
       │
       └─► User sends /quit
           └─► Bot closes browser & stops polling
```

---

## Quick Modification Guide

| Goal | Where to Change |
|------|-----------------|
| Change appointment check interval | `config.ini` → `[VFS] interval` |
| Update VFS URL for new center | `config.ini` → `[VFS] url` |
| Adjust XPath selectors | `VFSBot.py` → `check_appointment()` method |
| Add new Telegram admin | `config.ini` → `[TELEGRAM] admin_ids` |
| Modify captcha OCR settings | `utils.py` → `break_captcha()` function |
| Change notification format | `VFSBot.py` → message templates in `login_helper()` |
