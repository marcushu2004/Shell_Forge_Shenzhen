"""
Marcus CyberPet - 上位机控制中心 (增强版)
================================

包含功能：
1. 番茄钟完成/CPU&RAM>90% 触发蜂鸣器快速响3下
2. 模拟喂食逻辑：水传感器数值低，宠物心情下降
3. 缺水锁定显示 "I NEED WATER"
4. 定时自动分析系统/水位状态更新心情 (显示 Too Busy / WARNING WATER LEVEL 等)
"""

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import customtkinter as ctk
import psutil
import serial
from serial.tools import list_ports


# ============================================================
# 基础配置
# ============================================================

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

SERIAL_BAUDRATE = 115200

PAGE_ROTATION_SECONDS = 4.0
GUI_TICK_MS = 250
METRICS_REFRESH_SECONDS = 1.0
AUTO_MOOD_EVAL_SECONDS = 30.0  # 每 30 秒根据当前状态自动刷新分析心情

WEATHER_REFRESH_SECONDS = 600
SERIAL_PAGE_TTL_SECONDS = 3.0

VALID_MOODS = {"HAPPY", "BUSY", "ANGRY", "LAZY"}

PAGE_ORDER = (
    "CPU",
    "MEMORY",
    "PET",
    "WEATHER",
    "MAIL",
    "CLOCK",
    "POMODORO",
)

WATER_LOCK_THRESHOLD = 200  # 水传感器判定缺水卡住的硬阈值
WATER_LOW_THRESHOLD = 300   # 判定提示缺水警告的阈值


def ascii_field(value, limit=20, fallback="N/A"):
    text = str(value or "")
    text = (
        text.replace("|", "/")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )
    text = text.encode("ascii", "ignore").decode("ascii")
    text = "".join(ch for ch in text if 32 <= ord(ch) <= 126)
    text = " ".join(text.split()).strip()

    if not text:
        text = fallback

    return text[:limit]


def safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ============================================================
# 串口通信线程
# ============================================================

@dataclass(order=True)
class OutboundFrame:
    priority: int
    sequence: int
    expires_at: float = field(compare=False)
    payload: str = field(compare=False)


class SerialBridge(threading.Thread):
    def __init__(self, event_queue):
        super().__init__(daemon=True)
        self.event_queue = event_queue
        self.control_queue = queue.Queue()
        self.outbound_queue = queue.PriorityQueue()

        self.serial_port = None
        self.running = True

        self.sequence = 0
        self.sequence_lock = threading.Lock()
        self.write_ready_at = 0.0

    def request_connect(self, port, baudrate=SERIAL_BAUDRATE):
        self.control_queue.put(("connect", port, baudrate))

    def request_disconnect(self):
        self.control_queue.put(("disconnect",))

    def shutdown(self):
        self.control_queue.put(("shutdown",))

    def send(self, protocol_frame, priority=10, ttl=SERIAL_PAGE_TTL_SECONDS):
        payload = str(protocol_frame).replace("\r", "").replace("\n", "").strip()
        if not payload:
            return

        try:
            payload.encode("ascii", "strict")
        except UnicodeEncodeError:
            self.event_queue.put(("serial", "error", f"Non-ASCII frame: {payload!r}"))
            return

        with self.sequence_lock:
            self.sequence += 1
            sequence = self.sequence

        frame = OutboundFrame(
            priority=priority,
            sequence=sequence,
            expires_at=time.monotonic() + ttl,
            payload=payload,
        )
        self.outbound_queue.put(frame)

    def _close_serial(self):
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except serial.SerialException:
                pass
        self.serial_port = None

    def _handle_controls(self):
        while True:
            try:
                control = self.control_queue.get_nowait()
            except queue.Empty:
                return

            action = control[0]
            if action == "connect":
                _, port, baudrate = control
                self._close_serial()
                try:
                    self.serial_port = serial.Serial(
                        port=port, baudrate=baudrate, timeout=0.1, write_timeout=1
                    )
                    try:
                        self.serial_port.reset_input_buffer()
                        self.serial_port.reset_output_buffer()
                    except serial.SerialException:
                        pass
                    self.write_ready_at = time.monotonic() + 1.5
                    self.event_queue.put(("serial", "connected", port))
                except serial.SerialException as exc:
                    self.serial_port = None
                    self.event_queue.put(("serial", "error", str(exc)))

            elif action == "disconnect":
                self._close_serial()
                self.event_queue.put(("serial", "disconnected", ""))

            elif action == "shutdown":
                self.running = False
                self._close_serial()
                return

    def _read_serial_data(self):
        if not self.serial_port or not self.serial_port.is_open:
            return
        try:
            while self.serial_port.in_waiting > 0:
                line = self.serial_port.readline().decode("ascii", errors="ignore").strip()
                if line.startswith("DATA:WATER|"):
                    parts = line.split("|")
                    if len(parts) >= 2:
                        water_val = safe_int(parts[1], 0)
                        self.event_queue.put(("water_data", water_val))
        except serial.SerialException:
            pass

    def run(self):
        while self.running:
            self._handle_controls()
            if not self.running:
                break

            self._read_serial_data()

            try:
                frame = self.outbound_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            now = time.monotonic()
            if frame.expires_at < now or not self.serial_port or not self.serial_port.is_open:
                continue

            if now < self.write_ready_at:
                time.sleep(min(0.05, self.write_ready_at - now))
                self.outbound_queue.put(frame)
                continue

            try:
                self.serial_port.write((frame.payload + "\n").encode("ascii"))
                self.serial_port.flush()
            except serial.SerialException as exc:
                self._close_serial()
                self.event_queue.put(("serial", "error", f"Write failed: {exc}"))


# ============================================================
# 心知天气 Poller
# ============================================================

class SeniverseWeatherPoller(threading.Thread):
    def __init__(self, event_queue, generation, api_key, location, refresh_interval=WEATHER_REFRESH_SECONDS):
        super().__init__(daemon=True)
        self.event_queue = event_queue
        self.generation = generation
        self.api_key = api_key.strip()
        self.location = location.strip()
        self.refresh_interval = max(60, int(refresh_interval))
        self.stop_event = threading.Event()
        self.refresh_event = threading.Event()

    def stop(self):
        self.stop_event.set()
        self.refresh_event.set()

    def fetch_weather(self):
        params = {"key": self.api_key, "location": self.location, "language": "en", "unit": "c"}
        endpoint = "https://api.seniverse.com/v3/weather/now.json?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(endpoint, headers={"User-Agent": "Marcus-CyberPet/3.0", "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        results = response_data.get("results", [])
        if not results:
            raise ValueError("Seniverse response contains no results")

        result = results[0]
        city = result.get("location", {}).get("name", self.location)
        now = result.get("now", {})
        weather_text = ascii_field(now.get("text", "Unknown"), 20)
        temperature_text = ascii_field(f"{now.get('temperature', '--')}C", 10)
        return city, weather_text, temperature_text

    def run(self):
        while not self.stop_event.set():
            try:
                city, weather_text, temperature_text = self.fetch_weather()
                self.event_queue.put(("weather", "success", self.generation, city, weather_text, temperature_text))
            except Exception as exc:
                self.event_queue.put(("weather", "error", self.generation, str(exc)))

            self.refresh_event.clear()
            deadline = time.monotonic() + self.refresh_interval
            while not self.stop_event.is_set():
                remain = deadline - time.monotonic()
                if remain <= 0 or self.refresh_event.wait(timeout=min(1.0, remain)):
                    self.refresh_event.clear()
                    break


# ============================================================
# 番茄钟状态机
# ============================================================

class PomodoroTimer:
    def __init__(self, focus_minutes=25, break_minutes=5):
        self.focus_seconds = int(focus_minutes * 60)
        self.break_seconds = int(break_minutes * 60)
        self.phase = "FOCUS"
        self.running = False
        self.remaining_seconds = self.focus_seconds
        self.deadline = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.deadline = time.monotonic() + self.remaining_seconds

    def pause(self):
        if not self.running:
            return
        self.update()
        self.running = False
        self.deadline = None

    def reset(self):
        self.phase = "FOCUS"
        self.running = False
        self.remaining_seconds = self.focus_seconds
        self.deadline = None

    def update(self):
        if not self.running or self.deadline is None:
            return None

        now = time.monotonic()
        if now < self.deadline:
            self.remaining_seconds = max(0, int(self.deadline - now + 0.999))
            return None

        changed_phase = None
        while now >= self.deadline:
            if self.phase == "FOCUS":
                self.phase = "BREAK"
                self.deadline += self.break_seconds
            else:
                self.phase = "FOCUS"
                self.deadline += self.focus_seconds
            changed_phase = self.phase

        self.remaining_seconds = max(0, int(self.deadline - now + 0.999))
        return changed_phase

    def clock_text(self):
        minutes, seconds = divmod(max(0, self.remaining_seconds), 60)
        return f"{minutes:02d}:{seconds:02d}"


# ============================================================
# 主应用 GUI
# ============================================================

class CyberPetGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Marcus CyberPet - Control Center")
        self.geometry("980x740")
        self.minsize(920, 680)

        self.is_closing = False
        self.events = queue.Queue()

        self.bridge = SerialBridge(self.events)
        self.bridge.start()

        self.weather_worker = None
        self.weather_generation = 0
        self.ai_request_id = 0

        # 系统指标
        self.cpu_percent = 0.0
        self.memory_percent = 0.0
        self.unread_mail_count = 0

        # 蜂鸣防刷冷却时间
        self.last_high_load_beep = 0.0
        self.last_auto_mood_eval = 0.0

        # 水传感器状态与喂食机制
        self.water_value = 1024
        self.is_water_locked = False

        # AI & 宠物状态
        self.ai_mood = "HAPPY"
        self.ai_speech = "Hello Human!"

        # 天气状态
        self.weather_city = "Not configured"
        self.weather_text = "Sunny"
        self.temperature_text = "25C"

        # 番茄钟
        self.pomodoro = PomodoroTimer(focus_minutes=25, break_minutes=5)

        # 页面轮播变量
        self.rotation_anchor = time.monotonic()
        self.last_page_frame = ""
        self.last_preview_frame = ""
        self.last_metrics_refresh = 0.0

        # GUI 镜像预览
        self.preview_alert_until = 0.0
        self.preview_alert_active = False

        # UI 绑定变量
        self.connection_var = ctk.StringVar(value="Disconnected")
        self.weather_status_var = ctk.StringVar(value="Weather: not configured")
        self.pomodoro_var = ctk.StringVar(value="FOCUS 25:00")
        self.water_status_var = ctk.StringVar(value="Water: Normal (1024)")

        psutil.cpu_percent(interval=None)

        self.setup_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.after(100, self.drain_events)
        self.after(GUI_TICK_MS, self.tick)

    def setup_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ================= 左侧侧边栏 =================
        self.sidebar = ctk.CTkScrollableFrame(self, width=280, corner_radius=8)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)

        ctk.CTkLabel(self.sidebar, text="CyberPet Control", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=16, pady=(16, 14))

        # 串口配置
        ctk.CTkLabel(self.sidebar, text="串口设备", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16)
        self.port_combo = ctk.CTkComboBox(self.sidebar, values=self.get_serial_ports())
        self.port_combo.pack(fill="x", padx=16, pady=(6, 7))

        ctk.CTkButton(self.sidebar, text="刷新串口列表", command=self.refresh_serial_ports).pack(fill="x", padx=16, pady=(0, 7))
        self.connect_button = ctk.CTkButton(self.sidebar, text="连接设备", command=self.toggle_connection)
        self.connect_button.pack(fill="x", padx=16, pady=(0, 7))

        ctk.CTkLabel(self.sidebar, textvariable=self.connection_var, text_color="#60A5FA", wraplength=230, justify="left").pack(anchor="w", padx=16, pady=(0, 14))

        ctk.CTkFrame(self.sidebar, height=2).pack(fill="x", padx=12, pady=8)

        # 天气配置
        ctk.CTkLabel(self.sidebar, text="Seniverse 心知天气", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16)
        self.weather_key_entry = ctk.CTkEntry(self.sidebar, placeholder_text="Seniverse API Key", show="*")
        self.weather_key_entry.insert(0, os.getenv("SENIVERSE_API_KEY", ""))
        self.weather_key_entry.pack(fill="x", padx=16, pady=(6, 7))

        self.weather_location_entry = ctk.CTkEntry(self.sidebar, placeholder_text="城市 (beijing/shanghai)")
        self.weather_location_entry.insert(0, os.getenv("SENIVERSE_LOCATION", "beijing"))
        self.weather_location_entry.pack(fill="x", padx=16, pady=7)

        ctk.CTkButton(self.sidebar, text="启动 / 更新天气", command=self.start_weather_service).pack(fill="x", padx=16, pady=(0, 7))
        ctk.CTkLabel(self.sidebar, textvariable=self.weather_status_var, text_color="#A5B4FC", wraplength=230, justify="left").pack(anchor="w", padx=16, pady=(0, 14))

        ctk.CTkFrame(self.sidebar, height=2).pack(fill="x", padx=12, pady=8)

        # AI 情绪引擎
        ctk.CTkLabel(self.sidebar, text="AI 情绪引擎", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16)
        self.api_url_entry = ctk.CTkEntry(self.sidebar, placeholder_text="OpenAI Base URL")
        self.api_url_entry.insert(0, "https://api.openai.com/v1")
        self.api_url_entry.pack(fill="x", padx=16, pady=(6, 7))

        self.model_entry = ctk.CTkEntry(self.sidebar, placeholder_text="Model ID (e.g. gpt-3.5-turbo)")
        self.model_entry.pack(fill="x", padx=16, pady=7)

        self.api_key_entry = ctk.CTkEntry(self.sidebar, placeholder_text="AI API Key", show="*")
        self.api_key_entry.pack(fill="x", padx=16, pady=7)

        self.ai_button = ctk.CTkButton(
            self.sidebar,
            text="立即激发 AI 思考",
            fg_color=("#6D28D9", "#7C3AED"),
            hover_color=("#5B21B6", "#6D28D9"),
            command=self.run_ai_engine,
        )
        self.ai_button.pack(fill="x", padx=16, pady=(0, 18))

        # ================= 右侧主控制区 =================
        self.main_panel = ctk.CTkFrame(self, corner_radius=8)
        self.main_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)

        ctk.CTkLabel(self.main_panel, text="OLED 128x64 实时显示镜像", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=18, pady=(18, 8))

        # 屏幕预览框
        self.oled_preview = ctk.CTkFrame(self.main_panel, width=390, height=180, fg_color="#050505", border_width=2, border_color="#374151", corner_radius=6)
        self.oled_preview.pack(pady=(0, 16))
        self.oled_preview.pack_propagate(False)

        self.oled_title = ctk.CTkLabel(self.oled_preview, text="[ CYBER PET ]", font=("Consolas", 15, "bold"), text_color="#FDE047")
        self.oled_title.pack(anchor="w", padx=14, pady=(12, 0))

        self.oled_body1 = ctk.CTkLabel(self.oled_preview, text="( ^ _ ^ )", font=("Consolas", 22, "bold"), text_color="#67E8F9")
        self.oled_body1.pack(pady=(10, 0))

        self.oled_body2 = ctk.CTkLabel(self.oled_preview, text="Hello Human!", font=("Consolas", 13), text_color="#F8FAFC")
        self.oled_body2.pack(pady=(4, 0))

        # 仪表盘
        self.dashboard = ctk.CTkFrame(self.main_panel, corner_radius=6)
        self.dashboard.pack(fill="x", padx=18, pady=(0, 14))
        self.dashboard.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.cpu_label = ctk.CTkLabel(self.dashboard, text="CPU: 0%")
        self.cpu_label.grid(row=0, column=0, padx=10, pady=12)

        self.memory_label = ctk.CTkLabel(self.dashboard, text="RAM: 0%")
        self.memory_label.grid(row=0, column=1, padx=10, pady=12)

        self.weather_label = ctk.CTkLabel(self.dashboard, text="Weather: --")
        self.weather_label.grid(row=0, column=2, padx=10, pady=12)

        self.water_label = ctk.CTkLabel(self.dashboard, textvariable=self.water_status_var, text_color="#34D399")
        self.water_label.grid(row=0, column=3, padx=10, pady=12)

        # 番茄钟控制
        self.pomodoro_frame = ctk.CTkFrame(self.main_panel, corner_radius=6)
        self.pomodoro_frame.pack(fill="x", padx=18, pady=(0, 14))
        self.pomodoro_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.pomodoro_frame, text="Pomodoro Timer", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(self.pomodoro_frame, textvariable=self.pomodoro_var, font=("Consolas", 18, "bold")).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

        self.pomodoro_button = ctk.CTkButton(self.pomodoro_frame, text="开始", width=90, command=self.toggle_pomodoro)
        self.pomodoro_button.grid(row=0, column=1, rowspan=2, padx=6, pady=12)

        ctk.CTkButton(self.pomodoro_frame, text="重置", width=90, command=self.reset_pomodoro).grid(row=0, column=2, rowspan=2, padx=(0, 14), pady=12)

        # 日志输出
        ctk.CTkLabel(self.main_panel, text="系统运行日志", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=18, pady=(0, 4))
        self.log_box = ctk.CTkTextbox(self.main_panel, height=170, font=("Consolas", 11))
        self.log_box.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        self.log("CyberPet Control Center V3.0 initialized")

    def log(self, message):
        if self.is_closing:
            return
        timestamp = time.strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{timestamp}] {message}\n")
        self.log_box.see("end")

    def get_serial_ports(self):
        try:
            ports = [port.device for port in list_ports.comports()]
            return ports or ["COM4"]
        except Exception:
            return ["COM4"]

    def refresh_serial_ports(self):
        ports = self.get_serial_ports()
        self.port_combo.configure(values=ports)
        self.port_combo.set(ports[0])
        self.log("Serial port list refreshed")

    def toggle_connection(self):
        if self.connect_button.cget("text") == "连接设备":
            selected_port = self.port_combo.get().strip()
            if not selected_port:
                self.log("Serial error: no port selected")
                return
            self.connection_var.set(f"Connecting: {selected_port}...")
            self.bridge.request_connect(selected_port)
        else:
            self.bridge.request_disconnect()

    def start_weather_service(self):
        api_key = self.weather_key_entry.get().strip()
        location = self.weather_location_entry.get().strip()

        if not api_key or not location:
            self.log("Weather error: API key and Location are required")
            return

        if self.weather_worker is not None:
            self.weather_worker.stop()

        self.weather_generation += 1
        self.weather_city = location
        self.weather_status_var.set(f"Weather: updating {location}...")
        self.weather_worker = SeniverseWeatherPoller(
            event_queue=self.events,
            generation=self.weather_generation,
            api_key=api_key,
            location=location,
            refresh_interval=WEATHER_REFRESH_SECONDS,
        )
        self.weather_worker.start()
        self.log(f"Weather service requested: {location}")

    def toggle_pomodoro(self):
        if self.pomodoro.running:
            self.pomodoro.pause()
            self.pomodoro_button.configure(text="开始")
            self.log("Pomodoro paused")
        else:
            self.pomodoro.start()
            self.pomodoro_button.configure(text="暂停")
            self.log("Pomodoro started")

    def reset_pomodoro(self):
        self.pomodoro.reset()
        self.pomodoro_button.configure(text="开始")
        self.pomodoro_var.set(f"{self.pomodoro.phase} {self.pomodoro.clock_text()}")
        self.log("Pomodoro reset")

    def trigger_buzzer(self):
        """发送蜂鸣器快速响3下的指令"""
        self.bridge.send("CMD:BEEP", priority=0, ttl=2.0)

    # --------------------------------------------------------
    # 事件处理中心
    # --------------------------------------------------------

    def drain_events(self):
        if self.is_closing:
            return

        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break

            event_type = event[0]

            if event_type == "serial":
                _, state, detail = event
                if state == "connected":
                    self.connection_var.set(f"Connected: {detail}")
                    self.connect_button.configure(text="断开连接", fg_color=("#B91C1C", "#DC2626"), hover_color=("#991B1B", "#B91C1C"))
                    self.last_page_frame = ""
                    self.log(f"Serial port connected: {detail}")
                elif state == "disconnected":
                    self.connection_var.set("Disconnected")
                    self.connect_button.configure(text="连接设备", fg_color=("#1F6AA5", "#1F6AA5"), hover_color=("#144870", "#144870"))
                    self.log("Serial port disconnected")
                elif state == "error":
                    self.connection_var.set("Serial error")
                    self.connect_button.configure(text="连接设备", fg_color=("#1F6AA5", "#1F6AA5"), hover_color=("#144870", "#144870"))
                    self.log(f"Serial error: {detail}")

            elif event_type == "weather":
                _, status, generation, *payload = event
                if generation != self.weather_generation:
                    continue
                if status == "success":
                    city, weather_text, temperature_text = payload
                    self.weather_city = city
                    self.weather_text = weather_text
                    self.temperature_text = temperature_text
                    self.weather_status_var.set(f"Weather: {city} | {weather_text} | {temperature_text}")
                    self.log(f"Weather updated: {city} | {weather_text} | {temperature_text}")

            elif event_type == "ai_result":
                _, request_id, mood, speech = event
                if request_id != self.ai_request_id:
                    continue
                self.ai_button.configure(state="normal")
                self.ai_mood = mood
                self.ai_speech = speech
                self.log(f"AI response: mood={mood}, speech='{speech}'")

            # 功能2 & 3：解析水传感器与模拟喂食逻辑
            elif event_type == "water_data":
                val = event[1]
                self.water_value = val

                if val < WATER_LOCK_THRESHOLD:
                    # 数值小于200，严重缺水
                    self.water_status_var.set(f"Water: Thirsty! ({val})")
                    self.water_label.configure(text_color="#EF4444")

                    if not self.is_water_locked:
                        self.is_water_locked = True
                        self.ai_mood = "ANGRY"
                        self.ai_speech = "I NEED WATER"
                        self.log(f"CRITICAL: Water level too low ({val} < {WATER_LOCK_THRESHOLD}). Pet locked!")
                else:
                    # 水量恢复/正常
                    if self.is_water_locked:
                        self.is_water_locked = False
                        self.ai_mood = "HAPPY"
                        self.ai_speech = "Yummy! Hydrated!"
                        self.log(f"FED: Water level recovered ({val}). Pet unlocked and happy!")

                    self.water_status_var.set(f"Water: Normal ({val})")
                    self.water_label.configure(text_color="#34D399")

        self.after(100, self.drain_events)

    # --------------------------------------------------------
    # 规则与状态评估
    # --------------------------------------------------------

    def evaluate_auto_mood(self):
        """功能4：根据当前硬件指标与水传感器状态自动评价宠物心情与状态标语"""
        if self.water_value < WATER_LOCK_THRESHOLD:
            self.ai_mood = "ANGRY"
            self.ai_speech = "WARNING WATER LEVEL"
        elif self.cpu_percent > 80.0 or self.memory_percent > 80.0:
            self.ai_mood = "BUSY"
            self.ai_speech = "Too Busy"
        elif self.unread_mail_count > 5:
            self.ai_mood = "LAZY"
            self.ai_speech = "Too Many Mails"
        else:
            self.ai_mood = "HAPPY"
            self.ai_speech = "All Normal"

    def refresh_metrics(self):
        self.cpu_percent = psutil.cpu_percent(interval=None)
        self.memory_percent = psutil.virtual_memory().percent

        self.cpu_label.configure(text=f"CPU: {self.cpu_percent:.1f}%")
        self.memory_label.configure(text=f"RAM: {self.memory_percent:.1f}%")
        self.weather_label.configure(text=f"Weather: {self.weather_text} {self.temperature_text}")

        now = time.monotonic()

        # 功能1：CPU或RAM占用率过高(>90%) 蜂鸣器快速响三下，带10秒冷却
        if (self.cpu_percent > 90.0 or self.memory_percent > 90.0) and (now - self.last_high_load_beep > 10.0):
            self.last_high_load_beep = now
            self.trigger_buzzer()
            self.send_alert("SYS", "HIGH LOAD DETECTED!", 4000)
            self.log(f"ALERT: High load (CPU:{self.cpu_percent}%, RAM:{self.memory_percent}%). Buzzer triggered.")

        # 功能4：定时（每30秒）自动更新一次心情
        if now - self.last_auto_mood_eval >= AUTO_MOOD_EVAL_SECONDS:
            self.last_auto_mood_eval = now
            self.evaluate_auto_mood()

    def build_current_page(self, page_name):
        cpu_text = str(int(self.cpu_percent))
        memory_text = str(int(self.memory_percent))
        weather_text = ascii_field(self.weather_text, 20)
        temperature_text = ascii_field(self.temperature_text, 10)

        if page_name == "CPU":
            return f"PAGE:CPU|{cpu_text}", "[ CPU MONITOR ]", f"CPU: {cpu_text}%", f"RAM: {memory_text}%"
        if page_name == "MEMORY":
            return f"PAGE:MEMORY|{memory_text}", "[ RAM MONITOR ]", f"RAM: {memory_text}%", "System memory"
        if page_name == "PET":
            faces = {"HAPPY": "( ^ _ ^ )", "BUSY": "( > _ < )", "ANGRY": "( # _ # )", "LAZY": "( - _ - )"}

            # 针对缺水或者异常状态的文本调整
            display_speech = "I NEED WATER" if self.water_value < WATER_LOCK_THRESHOLD else self.ai_speech
            return f"PAGE:PET|{self.ai_mood}", "[ CYBER PET ]", faces.get(self.ai_mood, "( ^ _ ^ )"), display_speech
        if page_name == "WEATHER":
            return f"PAGE:WEATHER|{weather_text}|{temperature_text}", "[ WEATHER ]", weather_text, temperature_text
        if page_name == "MAIL":
            mail_text = str(self.unread_mail_count)
            return f"PAGE:MAIL|{mail_text}", "[ UNREAD MAIL ]", f"Count: {mail_text}", "Inbox status"
        if page_name == "CLOCK":
            now = time.localtime()
            return f"PAGE:CLOCK|{time.strftime('%H:%M', now)}|{time.strftime('%Y-%m-%d', now)}", "[ SYSTEM TIME ]", time.strftime('%H:%M', now), time.strftime('%Y-%m-%d', now)

        pomodoro_mode = self.pomodoro.phase
        pomodoro_clock = self.pomodoro.clock_text()
        return f"PAGE:POMODORO|{pomodoro_mode}|{pomodoro_clock}", f"[ POMODORO: {pomodoro_mode} ]", pomodoro_clock, "Running" if self.pomodoro.running else "Paused"

    def update_oled_preview(self, title, line1, line2):
        self.oled_title.configure(text=ascii_field(title, 26))
        self.oled_body1.configure(text=ascii_field(line1, 22))
        self.oled_body2.configure(text=ascii_field(line2, 26))

    def send_alert(self, source, text, duration_ms=3000):
        source = ascii_field(source, 8)
        text = ascii_field(text, 52)
        duration_ms = max(500, min(int(duration_ms), 60000))

        protocol_frame = f"ALERT:{source}|{text}|{duration_ms}|SINGLE"
        self.bridge.send(protocol_frame, priority=0, ttl=duration_ms / 1000.0 + 3.0)

        self.preview_alert_until = time.monotonic() + duration_ms / 1000.0
        self.preview_alert_active = True
        self.update_oled_preview(f"! {source} ALERT !", text[:20], "Notification")

    def tick(self):
        if self.is_closing:
            return

        try:
            now = time.monotonic()

            if now - self.last_metrics_refresh >= METRICS_REFRESH_SECONDS:
                self.last_metrics_refresh = now
                self.refresh_metrics()

            # 更新番茄钟
            changed_phase = self.pomodoro.update()
            self.pomodoro_var.set(f"{self.pomodoro.phase} {self.pomodoro.clock_text()}")

            # 功能1：番茄钟到时间/阶段切换时蜂鸣器响三下
            if changed_phase in ("BREAK", "FOCUS"):
                self.trigger_buzzer()
                alert_text = "Focus Time Over!" if changed_phase == "BREAK" else "Break Time Over!"
                self.send_alert("POMO", alert_text, 5000)
                self.log(f"Pomodoro switched to {changed_phase}. Buzzer triggered.")

            # 轮播逻辑 (如果处于缺水锁定，强制发送 PET 页面)
            if self.water_value < WATER_LOCK_THRESHOLD:
                page_name = "PET"
            else:
                elapsed = now - self.rotation_anchor
                page_index = int(elapsed // PAGE_ROTATION_SECONDS) % len(PAGE_ORDER)
                page_name = PAGE_ORDER[page_index]

            protocol_frame, preview_title, preview_line1, preview_line2 = self.build_current_page(page_name)

            if protocol_frame != self.last_page_frame:
                self.bridge.send(protocol_frame, priority=10, ttl=SERIAL_PAGE_TTL_SECONDS)
                self.last_page_frame = protocol_frame

            alert_active_now = now < self.preview_alert_until
            alert_just_ended = self.preview_alert_active and not alert_active_now

            if not alert_active_now and (protocol_frame != self.last_preview_frame or alert_just_ended):
                self.update_oled_preview(preview_title, preview_line1, preview_line2)
                self.last_preview_frame = protocol_frame

            self.preview_alert_active = alert_active_now

        except Exception as exc:
            self.log(f"Main tick error: {exc}")

        self.after(GUI_TICK_MS, self.tick)

    # --------------------------------------------------------
    # AI 引擎解析
    # --------------------------------------------------------

    @staticmethod
    def parse_ai_json(content):
        if not isinstance(content, str):
            content = json.dumps(content)
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Model response did not contain JSON")

        response_json = json.loads(content[start:end + 1])
        mood = str(response_json.get("mood", "HAPPY")).upper().strip()
        if mood not in VALID_MOODS:
            mood = "HAPPY"
        speech = ascii_field(response_json.get("speech", "Cyber Pet Active"), 18)
        return mood, speech

    def run_ai_engine(self):
        api_url = self.api_url_entry.get().strip()
        api_key = self.api_key_entry.get().strip()
        model_id = self.model_entry.get().strip()

        if not api_url or not api_key or not model_id:
            self.log("AI error: API URL, Key, and Model ID are required")
            return

        self.ai_request_id += 1
        request_id = self.ai_request_id
        self.ai_button.configure(state="disabled")
        self.log("AI is evaluating system & pet state...")

        cpu = self.cpu_percent
        memory = self.memory_percent
        water = self.water_value

        def ai_worker():
            prompt = (
                "You are a cyber pet inside a 128x64 OLED.\n"
                f"PC status: CPU={cpu:.1f}%, RAM={memory:.1f}%, WaterSensor={water}.\n\n"
                "Rules:\n"
                "1. Choose exactly one mood: HAPPY, BUSY, ANGRY, or LAZY.\n"
                "2. Write short English text reaction (e.g., 'Too Busy', 'WARNING WATER LEVEL', 'Feeling Great').\n"
                "3. speech must be ASCII only, maximum 18 chars.\n"
                "4. Return JSON format only:\n"
                '{"mood":"BUSY","speech":"Too Busy"}'
            )

            request_data = {
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
            }

            endpoint = f"{api_url.rstrip('/')}/chat/completions"

            try:
                request = urllib.request.Request(
                    endpoint,
                    data=json.dumps(request_data).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                )
                with urllib.request.urlopen(request, timeout=12) as response:
                    response_data = json.loads(response.read().decode("utf-8"))

                choices = response_data.get("choices", [])
                if not choices:
                    raise ValueError("AI response empty")

                content = choices[0].get("message", {}).get("content", "")
                mood, speech = self.parse_ai_json(content)
                self.events.put(("ai_result", request_id, mood, speech))

            except Exception as exc:
                self.events.put(("ai_error", request_id, str(exc)))

        threading.Thread(target=ai_worker, daemon=True).start()

    def on_close(self):
        self.is_closing = True
        if self.weather_worker is not None:
            self.weather_worker.stop()
        self.bridge.shutdown()
        self.bridge.join(timeout=0.5)
        self.destroy()


if __name__ == "__main__":
    app = CyberPetGUI()
    app.mainloop()