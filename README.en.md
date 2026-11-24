# Shell_Forge_Shenzhen · Marcus CyberPet

> Project built at **Evolve Tavern Hackathon · Shenzhen** by **Marcus Team**: a software + hardware desktop pet and desktop assistant system.
> The device side runs on a NodeMCU (ESP8266) with a 128×64 OLED; the host side is a Python desktop control center that talks to the board over a USB serial line protocol and integrates a weather API plus an LLM.

[简体中文](README.md) | English

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Hardware & Wiring](#hardware--wiring)
- [Dependencies](#dependencies)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Serial Protocol](#serial-protocol)
- [How It Works](#how-it-works)
- [Repository Layout](#repository-layout)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Docs](#docs)
- [License](#license)

---

## Overview

**Marcus CyberPet** merges a *system monitor* with an *expressive desk pet*:

- **The hardware does the expressing** — a 128×64 monochrome OLED shows expressions, metrics and clock pages; a buzzer handles alerts and reminders; a water-level sensor provides the "feeding" interaction.
- **The host does the computing** — the Python app samples CPU / memory, drives a Pomodoro state machine, polls Seniverse weather, asks an LLM for the pet's mood, then pushes results to the board as newline-delimited ASCII frames.
- **Edge-first, cloud-optional** — rendering and interaction are fully local. Weather and AI mood are enhancements only; with no network the system degrades to pure rule-based behaviour.

Design intent: a **persistent, low-noise secondary information surface** next to your monitor — it grabs your attention with a beep and inverted highlight only when it matters, and quietly "lives" in the corner of your desktop the rest of the time.

## Features

| Module | Capability | Where |
| --- | --- | --- |
| 🍅 Pomodoro | 25 min focus / 5 min break auto-rotation, triple beep + popup on phase change | `PomodoroTimer`, firmware `updateBuzzer` |
| 📊 System monitor | CPU / RAM refreshed every second, 7 pages (CPU / RAM / PET / WEATHER / MAIL / CLOCK / POMODORO) rotating every 4 s | `refresh_metrics`, `PAGE_ORDER` |
| 🚨 High-load alert | CPU or RAM > 90% → triple beep, OLED shows `HIGH LOAD DETECTED!`, 10 s cooldown | `refresh_metrics` |
| 💧 Simulated feeding | Water sensor reported in real time; sufficient water → mood becomes HAPPY, shortage → ANGRY | `water_data` event, `pollWaterSensor` |
| 🚫 Thirst lock | Below threshold (200) the pet page is force-locked with inverted `I NEED WATER!`, popups and page rotation suppressed | `renderCurrentScreen`, `setCurrentPage` |
| 🧠 AI mood engine | Sends CPU / RAM / water state to any OpenAI-compatible endpoint, parses strict JSON `mood` + `speech` | `run_ai_engine`, `parse_ai_json` |
| 🌦 Weather sync | Seniverse v3 real-time weather, 10-minute refresh, English fields | `SeniverseWeatherPoller` |
| 🖥 OLED mirror | The host renders a preview identical to the device, so demos work without hardware | `update_oled_preview` |

Four pet faces: `HAPPY ( ^ _ ^ )`, `BUSY ( > _ < )`, `ANGRY ( # _ # )`, `LAZY ( - _ - )`; plus `( O _ O )` when the tank is dry.

## Architecture

```
┌──────────────────────────── Host side (Python) ─────────────────────────┐
│                                                                        │
│  customtkinter GUI ──┬── psutil          → CPU / RAM metrics (1 s)      │
│                      ├── PomodoroTimer   → focus / break state machine  │
│                      ├── SerialBridge    → serial I/O thread (115200)   │
│                      ├── SeniversePoller → weather API (10 min)         │
│                      └── AI Worker       → OpenAI-compatible LLM        │
│                                                                        │
│            queue.Queue — single-direction cross-thread event bus        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ USB serial · newline-delimited ASCII
┌──────────────────────────────────┴─────────────────────────────────────┐
│                        Device side: NodeMCU (ESP8266)                  │
│                                                                        │
│  Serial parser → UiState / AlertState → dirty-flag rendering           │
│  SSD1306 128×64 over I2C (0x3C)  ·  active buzzer (active-LOW)         │
│  Analog water sensor on A0 → reports DATA:WATER|<0-1023> every 500 ms   │
└────────────────────────────────────────────────────────────────────────┘
```

Two data directions:

- **Downstream (host → device):** `PAGE:*` page frames, `CMD:BEEP`, `ALERT:*` popups.
- **Upstream (device → host):** `DATA:WATER|<raw>` water samples.

## Hardware & Wiring

| Part | Spec | Note |
| --- | --- | --- |
| MCU | NodeMCU (ESP8266) | Select `NodeMCU 1.0 (ESP-12E Module)` in Arduino IDE |
| Display | SSD1306 OLED 128×64, I2C | Address `0x3C` |
| Buzzer | Active buzzer module | **Active-LOW**: LOW = sound, HIGH = silent |
| Water sensor | Analog output (0–1023) | To `A0`, plus 3.3V / GND |
| Cable | Micro-USB data cable | Must support data, not charge-only |

Pin map (fixed by firmware constants):

| Signal | NodeMCU label | GPIO | Constant |
| --- | --- | --- | --- |
| OLED SDA | D2 | GPIO4 | `OLED_SDA` |
| OLED SCL | D1 | GPIO5 | `OLED_SCL` |
| Buzzer | D5 | GPIO14 | `BUZZER_PIN` |
| Water AO | A0 | ADC0 | `WATER_PIN` |

> Power the sensor from **3.3V**: the ESP8266 ADC tolerates 0–3.3V only; feeding it 5V can permanently damage the chip.

## Dependencies

**Host** (Python 3.9+):

```bash
pip install -r requirements.txt
# == customtkinter>=5.2.0, psutil>=5.9.0, pyserial>=3.5
```

**Firmware** (Arduino IDE with the `esp8266` board core installed):

- `Adafruit GFX Library`
- `Adafruit SSD1306`
- `Wire` (bundled)

## Quick Start

### 1. Flash the firmware

1. Install the ESP8266 board core and the two libraries above in Arduino IDE.
2. Create a sketch and paste the contents of `sketch_sep22c.ino` into it — **Arduino requires the `.ino` file name to match its parent folder**, otherwise compilation fails.
3. Select `NodeMCU 1.0 (ESP-12E Module)` and the correct port, then upload.
4. The OLED should show the splash screen: `[ CyberPet OS ]` / `Marcus Team V3.0` / `Serial Ready`.

### 2. Run the host app

```bash
pip install -r requirements.txt
python main_app.py
```

Then in the window:

1. Pick your port (e.g. `COM4`) in the **串口设备 / Serial device** dropdown and click **连接设备 / Connect**;
2. On success the log prints `Serial port connected: COM4` and the OLED starts rotating pages;
3. Click **开始 / Start** to run the Pomodoro timer, or **立即激发 AI 思考 / Ask AI** for a single LLM mood inference.

> Empty port list? Click **刷新串口列表 / Refresh**. Still empty usually means the CH340 / CP210x USB-serial driver is missing.

### 3. Manual protocol test (no hardware needed)

Open any serial terminal at 115200 baud and send single lines:

```
PAGE:CLOCK|21:30|2026-11-24
CMD:BEEP
ALERT:TEST|Hello CyberPet|3000|SINGLE
```

## Configuration

### Weather API (Seniverse)

Enter the API key and a city pinyin (`beijing`, `shanghai`, …) in the sidebar, then click **启动 / 更新天气**. Environment variables pre-fill the fields:

```powershell
$env:SENIVERSE_API_KEY="your-seniverse-key"
$env:SENIVERSE_LOCATION="shenzhen"
```

### AI mood engine (any OpenAI-compatible endpoint)

All three fields are entered in the GUI — no code changes needed:

| Field | Example |
| --- | --- |
| Base URL | `https://api.openai.com/v1` (or any compatible `/v1` endpoint) |
| Model ID | `gpt-4o-mini` / `deepseek-chat` / your own model |
| API Key | Provider key (masked in the UI) |

The model must return JSON: `{"mood": "BUSY", "speech": "Too Busy"}`. `mood` is clamped to `HAPPY / BUSY / ANGRY / LAZY`; `speech` is sanitised to ≤18 ASCII characters (OLED font constraint).

### Tunable thresholds

| Parameter | Location | Default | Meaning |
| --- | --- | --- | --- |
| `WATER_LOCK_THRESHOLD` | `main_app.py` **and** `.ino` (**must match**) | `200` | Below this: thirst lock |
| `WATER_LOW_THRESHOLD` | `main_app.py` | `300` | Low-water warning |
| High-load alert | `refresh_metrics` | `90%` | Beep + popup trigger |
| `PAGE_ROTATION_SECONDS` | `main_app.py` | `4.0 s` | Page dwell time |
| `AUTO_MOOD_EVAL_SECONDS` | `main_app.py` | `30 s` | Rule-engine refresh |

## Serial Protocol

**Physical layer:** USB CDC serial, `115200` 8N1, ASCII, `\n`-terminated lines, `\r` ignored; max 160 bytes per line, oversized lines are discarded entirely. Fields are separated by `|`; the leading keyword identifies the frame type.

### Downstream (host → device)

| Frame | Fields | Example |
| --- | --- | --- |
| `PAGE:CPU\|<percent>` | integer CPU load | `PAGE:CPU\|42` |
| `PAGE:MEMORY\|<percent>` | integer RAM load | `PAGE:MEMORY\|67` |
| `PAGE:PET\|<MOOD>` | mood enum | `PAGE:PET\|BUSY` |
| `PAGE:WEATHER\|<text>\|<temp>` | English weather + temperature | `PAGE:WEATHER\|Sunny\|25C` |
| `PAGE:MAIL\|<count>` | unread mail count | `PAGE:MAIL\|3` |
| `PAGE:CLOCK\|<HH:MM>\|<YYYY-MM-DD>` | time + date | `PAGE:CLOCK\|21:30\|2026-11-24` |
| `PAGE:POMODORO\|<phase>\|<MM:SS>` | `FOCUS` / `BREAK` | `PAGE:POMODORO\|FOCUS\|25:00` |
| `CMD:BEEP` | triple beep, 100 ms interval | `CMD:BEEP` |
| `ALERT:<source>\|<text>\|<ms>\|SINGLE` | popup, 500–60000 ms | `ALERT:SYS\|HIGH LOAD DETECTED!\|4000\|SINGLE` |

### Upstream (device → host)

| Frame | Meaning | Example |
| --- | --- | --- |
| `DATA:WATER\|<raw>` | raw ADC value every 500 ms (0–1023, lower = more water) | `DATA:WATER\|812` |

### Transport strategy

- **Priority queue:** `ALERT` / `CMD:BEEP` use `priority=0`, page frames use `priority=10` (lower value dequeues first).
- **Frame TTL:** page frames 3 s; alert frames = duration + 3 s. Expired frames are dropped on dequeue so stale data never reaches the OLED.
- **Connect cooldown:** writes are held for 1.5 s after opening the port to survive the NodeMCU reset window.
- **Deduplication:** identical consecutive page frames are not resent, saving serial bandwidth.

## How It Works

1. **GUI loop:** a `tick` every 250 ms, metrics refresh every second, page index derived from 4-second slices.
2. **Rule engine:** `evaluate_auto_mood` runs every 30 s with priority water shortage → CPU/RAM > 80% → mail > 5 → normal.
3. **Event bus:** the serial, weather and AI threads only enqueue events; the GUI thread drains them every 100 ms, so no widget is touched across threads.
4. **Device rendering:** the firmware renders on a `renderDirty` flag, redrawing only when data or page changes, keeping I2C traffic low.
5. **Thirst dominates:** below the threshold, **both** host and firmware force the pet page, suppressing alerts and rotation.

## Repository Layout

```
Shell_Forge_Shenzhen/
├── main_app.py          # Host control center: GUI, serial bridge, Pomodoro, weather, AI engine
├── sketch_sep22c.ino    # NodeMCU firmware: OLED rendering, protocol parser, buzzer, water sampling
├── requirements.txt     # Host Python dependencies
├── README.md            # 简体中文说明
├── README.en.md         # English README (this file)
├── docs/
│   └── CODE_REVIEW.md   # Code review and optimisation notes (with defect locations)
└── LICENSE              # MIT
```

## Known Limitations

Each item is located in [docs/CODE_REVIEW.md](docs/CODE_REVIEW.md); PRs welcome:

- `SeniverseWeatherPoller.run` uses `stop_event.set()` as its loop condition instead of `is_set()`, which breaks the 10-minute interval and can hammer the weather API.
- The `ai_error` event emitted on AI failure is never consumed by the GUI, so the "Ask AI" button stays disabled after one failure.
- Weather failures are silent — no UI feedback.
- There is no data source for the unread-mail count, so the `MAIL` page and `LAZY` mood are effectively unreachable.
- AI-generated speech is overwritten by the 30-second rule engine; mood lifetime is capped at ~30 s.
- Water samples are unfiltered and use no hysteresis, so a noisy sensor can flap the thirst lock.
- API keys live in memory only (weather supports env vars, AI key does not).
- The original README claimed "STM32/ESP32", while the firmware targets ESP8266 / NodeMCU.

## Roadmap

- [ ] Fix the weather-thread loop condition and handle `ai_error`; add retry with backoff
- [ ] Add a config file (`config.yaml`) for thresholds, keys and refresh intervals
- [ ] Moving-average + hysteresis window for water sampling
- [ ] Wire up a real mail/notification source, or drop the page
- [ ] Heartbeat frames from firmware plus a link-status indicator in the GUI
- [ ] `pytest` unit tests (protocol codec, Pomodoro state machine, JSON parsing)
- [ ] Configurable page order and face assets

## Docs

- [简体中文 README](README.md)
- [Code review and optimisation notes](docs/CODE_REVIEW.md) *(Chinese)*

## Team & Credits

- **Marcus Team** — Evolve Tavern Hackathon · Shenzhen
- Weather data: [Seniverse](https://www.seniverse.com/)
- OLED drivers: [Adafruit GFX](https://github.com/adafruit/Adafruit-GFX-Library) / [Adafruit SSD1306](https://github.com/adafruit/Adafruit_SSD1306)
- GUI toolkit: [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)

## License

Released under the [MIT License](LICENSE), Copyright (c) 2026 marcushu2004.
