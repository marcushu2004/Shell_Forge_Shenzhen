# Shell_Forge_Shenzhen · Marcus CyberPet

> 进化酒馆黑客松深圳站（Marcus Team）作品：软硬件一体化的嵌入式智能桌宠与桌面助手系统。
> 硬件端为 NodeMCU（ESP8266）+ 128×64 OLED；上位机为 Python 桌面控制中心，通过 USB 串口与硬件联动，并接入天气 API 与大语言模型。

[English](README.en.md) | 简体中文

---

## 目录

- [项目简介](#项目简介)
- [功能特性](#功能特性)
- [系统架构](#系统架构)
- [硬件与接线](#硬件与接线)
- [软件依赖](#软件依赖)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [串口通信协议](#串口通信协议)
- [运行机制](#运行机制)
- [目录结构](#目录结构)
- [已知限制](#已知限制)
- [路线图](#路线图)
- [文档](#文档)
- [许可协议](#许可协议)

---

## 项目简介

**Marcus CyberPet** 把「系统监控面板」和「情绪化桌宠」合二为一：

- **硬件承担表达**：一块 128×64 的单色 OLED 负责显示表情、指标与时钟，蜂鸣器负责告警与提醒，水位传感器负责"喂食"互动。
- **上位机承担计算**：Python 桌面端采集 CPU / 内存指标、运行番茄钟状态机、拉取心知天气数据、调用 LLM 生成宠物情绪，再把结果按行文本协议推给硬件。
- **边缘与云端协同**：显示与交互在本地闭环（不依赖网络）；天气与 AI 情绪仅作为增强能力，断网时自动降级为本地规则驱动。

设计目标：在显示器之外提供一块**常驻的、低打扰的第二信息面**——重要时用蜂鸣与反色提示抢夺注意力，其余时间安静地待在桌面角落里"活着"。

## 功能特性

| 模块 | 能力 | 实现位置 |
| --- | --- | --- |
| 🍅 番茄钟 | 25 分钟专注 / 5 分钟休息自动轮转，阶段切换时蜂鸣器响 3 下并弹窗提示 | `PomodoroTimer` + 固件 `updateBuzzer` |
| 📊 系统监控 | 每秒刷新 CPU / 内存，七页（CPU / RAM / PET / WEATHER / MAIL / CLOCK / POMODORO）4 秒轮播 | `refresh_metrics` + `PAGE_ORDER` |
| 🚨 高负载告警 | CPU 或内存 > 90% 时蜂鸣器连响 3 下，OLED 弹出 `HIGH LOAD DETECTED!`，带 10 秒冷却 | `refresh_metrics` |
| 💧 模拟喂食 | 水位传感器实时上报，水量充足即"被喂食"→ 心情转 HAPPY；缺水则心情转 ANGRY | `water_data` 事件 + 固件 `pollWaterSensor` |
| 🚫 缺水锁定 | 水位低于阈值（200）时强制锁定在宠物页并反色高亮 `I NEED WATER!`，屏蔽弹窗与切页 | `renderCurrentScreen` + `setCurrentPage` |
| 🧠 AI 情绪引擎 | 将 CPU / 内存 / 水位状态送入 OpenAI 兼容接口，返回严格 JSON 的 `mood` + `speech`，驱动表情与台词 | `run_ai_engine` / `parse_ai_json` |
| 🌦 天气同步 | 心知天气（Seniverse）v3 实时天气，10 分钟刷新，支持城市与英文字段 | `SeniverseWeatherPoller` |
| 🖥 OLED 镜像 | 上位机实时渲染与硬件同构的预览画面，无硬件也能演示 | `update_oled_preview` |

宠物表情共 4 种：`HAPPY ( ^ _ ^ )` / `BUSY ( > _ < )` / `ANGRY ( # _ # )` / `LAZY ( - _ - )`，缺水时额外显示 `( O _ O )`。

## 系统架构

```
┌──────────────────────────── 上位机 (Python) ────────────────────────────┐
│                                                                        │
│  customtkinter GUI ──┬── psutil          → CPU / RAM 指标 (1s)          │
│                      ├── PomodoroTimer   → 专注/休息状态机              │
│                      ├── SerialBridge    → 串口读写线程 (115200 8N1)    │
│                      ├── SeniversePoller → 心知天气 API  (10min)        │
│                      └── AI Worker       → OpenAI 兼容 LLM (手动触发)   │
│                                                                        │
│              事件队列 queue.Queue（跨线程单向汇聚，GUI 线程消费）        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ USB 串口 · 行文本协议 "\n" 分隔
┌──────────────────────────────────┴─────────────────────────────────────┐
│                        硬件端 NodeMCU (ESP8266)                        │
│                                                                        │
│  Serial 解析 → UiState / AlertState → 脏标记渲染                        │
│  SSD1306 128×64 (I2C 0x3C)   ·   有源蜂鸣器 (低电平触发)               │
│  模拟水位传感器 A0 → 每 500ms 上报 DATA:WATER|<0-1023>                  │
└────────────────────────────────────────────────────────────────────────┘
```

数据流有两个方向：

- **下行（上位机 → 硬件）**：`PAGE:*` 页面帧、`CMD:BEEP` 蜂鸣指令、`ALERT:*` 弹窗帧。
- **上行（硬件 → 上位机）**：`DATA:WATER|<raw>` 水位采样。

## 硬件与接线

| 器件 | 型号 / 参数 | 说明 |
| --- | --- | --- |
| 主控 | NodeMCU（ESP8266） | Arduino IDE 选择 `NodeMCU 1.0 (ESP-12E Module)` |
| 显示屏 | SSD1306 OLED 128×64，I2C | 地址 `0x3C` |
| 蜂鸣器 | 有源蜂鸣器模块 | **低电平触发**，高电平静音 |
| 水位传感器 | 模拟量输出（0–1023） | 接 `A0`，另需 3.3V / GND |
| 连接 | Micro-USB 数据线 | 需支持数据传输，非纯充电线 |

接线表（引脚按固件常量固定）：

| 信号 | NodeMCU 丝印 | GPIO | 固件常量 |
| --- | --- | --- | --- |
| OLED SDA | D2 | GPIO4 | `OLED_SDA` |
| OLED SCL | D1 | GPIO5 | `OLED_SCL` |
| 蜂鸣器 | D5 | GPIO14 | `BUZZER_PIN` |
| 水位传感器 AO | A0 | ADC0 | `WATER_PIN` |

> 传感器供电请使用 **3.3V**：ESP8266 的 ADC 只能承受 0–3.3V，直接接 5V 输出可能损坏芯片。

## 软件依赖

**上位机**（Python 3.9+，已在 Windows 11 + Python 3.14 环境验证可运行）：

```bash
pip install -r requirements.txt
# 等价于: customtkinter>=5.2.0, psutil>=5.9.0, pyserial>=3.5
```

**固件**（Arduino IDE + 开发板管理器安装 `esp8266` 核心）：

- `Adafruit GFX Library`
- `Adafruit SSD1306`
- `Wire`（内置）

## 快速开始

### 1. 烧录固件

1. Arduino IDE 中安装 ESP8266 开发板核心与上表两个库。
2. 新建工程，将 `sketch_sep22c.ino` 内容粘贴进去（**注意：Arduino 要求 .ino 文件名与所在文件夹同名**，否则无法编译）。
3. 选择开发板 `NodeMCU 1.0 (ESP-12E Module)` 与对应串口，上传。
4. 上传完成后 OLED 应显示启动页 `[ CyberPet OS ]` / `Marcus Team V3.0` / `Serial Ready`。

### 2. 启动上位机

```bash
pip install -r requirements.txt
python main_app.py
```

窗口出现后：

1. 在侧边栏「串口设备」下拉框选择设备端口（如 `COM4`），点击「连接设备」；
2. 连接成功后日志出现 `Serial port connected: COM4`，OLED 开始随页面轮播刷新；
3. 点击「开始」运行番茄钟，点击「立即激发 AI 思考」触发一次 LLM 情绪推理。

> 若下拉框为空，点击「刷新串口列表」；仍为空通常是缺少 CH340 / CP210x USB 转串口驱动。

### 3. 串口自检（不接硬件可选）

用任意串口终端连 115200 波特率，向设备发送单行文本即可手测协议：

```
PAGE:CLOCK|21:30|2026-11-24
CMD:BEEP
ALERT:TEST|Hello CyberPet|3000|SINGLE
```

## 配置说明

### 天气 API（心知天气）

在侧边栏填写 API Key 与城市拼音（`beijing` / `shanghai`），点击「启动 / 更新天气」。也支持环境变量预填：

```powershell
$env:SENIVERSE_API_KEY="your-seniverse-key"
$env:SENIVERSE_LOCATION="shenzhen"
```

### AI 情绪引擎（任意 OpenAI 兼容接口）

三个输入框在 GUI 内填写，不需要改代码：

| 字段 | 示例 |
| --- | --- |
| Base URL | `https://api.openai.com/v1`（或任意兼容服务的 `/v1` 地址） |
| Model ID | `gpt-4o-mini` / `deepseek-chat` / 自建模型名 |
| API Key | 服务商密钥（输入框为密码掩码） |

模型必须返回 JSON：`{"mood": "BUSY", "speech": "Too Busy"}`。`mood` 会被强制收敛到 `HAPPY / BUSY / ANGRY / LAZY`，`speech` 会被裁剪为 ≤18 字符的纯 ASCII（OLED 字库限制）。

### 阈值调整

| 参数 | 位置 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `WATER_LOCK_THRESHOLD` | `main_app.py` + `.ino`（**两端必须一致**） | `200` | 低于此值判定缺水并锁页 |
| `WATER_LOW_THRESHOLD` | `main_app.py` | `300` | 缺水预警阈值 |
| 高负载告警阈值 | `refresh_metrics` | `90%` | 触发蜂鸣与弹窗 |
| 页面轮播周期 | `PAGE_ROTATION_SECONDS` | `4.0s` | 单页停留时长 |
| 心情自动评估 | `AUTO_MOOD_EVAL_SECONDS` | `30s` | 规则引擎刷新周期 |

## 串口通信协议

**物理层**：USB CDC 串口，`115200` 8N1，ASCII 编码，`\n` 结束一行，`\r` 被忽略；单行最大 160 字节，超长整行丢弃。字段之间用 `|` 分隔，前导关键字区分帧类型。

### 下行指令（上位机 → 硬件）

| 帧格式 | 字段含义 | 示例 |
| --- | --- | --- |
| `PAGE:CPU\|<百分比>` | CPU 占用整数 | `PAGE:CPU\|42` |
| `PAGE:MEMORY\|<百分比>` | 内存占用整数 | `PAGE:MEMORY\|67` |
| `PAGE:PET\|<MOOD>` | 心情枚举 | `PAGE:PET\|BUSY` |
| `PAGE:WEATHER\|<天气>\|<温度>` | 英文天气与温度 | `PAGE:WEATHER\|Sunny\|25C` |
| `PAGE:MAIL\|<数量>` | 未读邮件数 | `PAGE:MAIL\|3` |
| `PAGE:CLOCK\|<HH:MM>\|<YYYY-MM-DD>` | 时间与日期 | `PAGE:CLOCK\|21:30\|2026-11-24` |
| `PAGE:POMODORO\|<阶段>\|<MM:SS>` | `FOCUS` / `BREAK` | `PAGE:POMODORO\|FOCUS\|25:00` |
| `CMD:BEEP` | 蜂鸣器快响 3 下（100ms 间隔） | `CMD:BEEP` |
| `ALERT:<来源>\|<正文>\|<毫秒>\|SINGLE` | 弹窗，时长 500–60000ms | `ALERT:SYS\|HIGH LOAD DETECTED!\|4000\|SINGLE` |

### 上行数据（硬件 → 上位机）

| 帧格式 | 含义 | 示例 |
| --- | --- | --- |
| `DATA:WATER\|<raw>` | 每 500ms 上报一次 ADC 原始值（0–1023，越小代表水量越充足） | `DATA:WATER\|812` |

### 传输策略

- **优先级队列**：`ALERT` / `CMD:BEEP` 使用 `priority=0`，页面帧使用 `priority=10`（数值越小越先出队）。
- **帧有效期（TTL）**：页面帧 3 秒，弹窗帧 = 时长 + 3 秒；过期帧在出队时丢弃，避免固件显示陈旧数据。
- **连接冷却**：打开串口后延迟 1.5 秒才允许写入，规避 NodeMCU 复位期间的数据丢失。
- **去重**：页面帧内容与上一帧相同则不重发，减少串口带宽占用。

## 运行机制

1. **GUI 主循环**：250ms 一次 `tick`，1 秒刷新一次指标，按 4 秒切片计算当前应显示页面。
2. **规则引擎**：每 30 秒执行一次 `evaluate_auto_mood`，优先级为 缺水 → CPU/RAM > 80% → 邮件 > 5 → 正常。
3. **事件汇聚**：串口线程、天气线程、AI 线程只向 `queue.Queue` 投递事件，GUI 线程 100ms 批量消费，避免跨线程操作控件。
4. **硬件渲染**：固件用 `renderDirty` 脏标记渲染，只有数据或页码变化才重绘，降低 I2C 刷新开销。
5. **缺水优先级最高**：水位低于阈值时，上位机与固件**双重**强制锁定宠物页，屏蔽告警弹窗与轮播。

## 目录结构

```
Shell_Forge_Shenzhen/
├── main_app.py          # 上位机控制中心：GUI、串口桥、番茄钟、天气、AI 引擎
├── sketch_sep22c.ino    # NodeMCU 固件：OLED 渲染、协议解析、蜂鸣与水位采样
├── requirements.txt     # 上位机 Python 依赖
├── README.md            # 中文说明（本文件）
├── README.en.md         # English README
├── docs/
│   └── CODE_REVIEW.md   # 代码评审与优化建议（含已知缺陷定位）
└── LICENSE              # MIT
```

## 已知限制

以下问题已在 [docs/CODE_REVIEW.md](docs/CODE_REVIEW.md) 中逐条定位，欢迎 PR 修复：

- `SeniverseWeatherPoller.run` 循环条件误写为 `stop_event.set()`（应为 `is_set()`），会导致天气刷新脱离 10 分钟间隔、高频请求 API。
- AI 请求失败时投递的 `ai_error` 事件未被 GUI 消费，失败后「立即激发 AI 思考」按钮会一直处于禁用态。
- 天气请求失败分支没有界面提示，仅静默失败。
- 未读邮件数没有数据源，`MAIL` 页面与 `LAZY` 心情目前恒为 0 / 不可达。
- AI 生成的台词会被 30 秒一次的规则引擎覆盖，情绪保持时间上限约 30 秒。
- 水位采样未做滤波与迟滞判定，传感器抖动可能导致缺水锁定状态反复跳变。
- API Key 仅在内存中，重启需重新填写（天气 Key 支持环境变量，AI Key 暂不支持）。
- 原 README 中「STM32/ESP32」表述与实际固件平台（ESP8266 / NodeMCU）不符。

## 路线图

- [ ] 修复天气线程循环条件与 `ai_error` 事件处理，补齐失败重试与退避
- [ ] 引入配置文件（`config.yaml`）统一管理阈值、密钥与刷新周期
- [ ] 为水位采样增加滑动平均 + 迟滞窗口，消除锁定状态抖动
- [ ] 接入真实邮件/通知数据源，或移除该页面避免误导
- [ ] 固件侧增加心跳帧与上位机连接状态指示
- [ ] 增加 `pytest` 单元测试（协议编解码、番茄钟状态机、JSON 解析）
- [ ] 支持自定义页面顺序与表情素材

## 文档

- [English README](README.en.md)
- [代码评审与优化建议](docs/CODE_REVIEW.md)

## 团队与致谢

- **Marcus Team** — 进化酒馆黑客松 · 深圳站
- 天气数据：[心知天气 Seniverse](https://www.seniverse.com/)
- OLED 驱动：[Adafruit GFX](https://github.com/adafruit/Adafruit-GFX-Library) / [Adafruit SSD1306](https://github.com/adafruit/Adafruit_SSD1306)
- GUI 框架：[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)

## 许可协议

本项目基于 [MIT License](LICENSE) 开源，Copyright (c) 2026 marcushu2004。
