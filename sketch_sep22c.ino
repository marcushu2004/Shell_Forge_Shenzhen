#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

constexpr uint8_t SCREEN_WIDTH = 128;
constexpr uint8_t SCREEN_HEIGHT = 64;
constexpr uint8_t OLED_SDA = 4;  // NodeMCU D2
constexpr uint8_t OLED_SCL = 5;  // NodeMCU D1
constexpr uint8_t OLED_ADDR = 0x3C;

// 硬件引脚定义
constexpr uint8_t BUZZER_PIN = 14; // NodeMCU D5 (GPIO14, 低电平触发)
constexpr uint8_t WATER_PIN = A0;   // 模拟量水传感器输入引脚

constexpr size_t RX_LINE_SIZE = 160;
constexpr size_t SMALL_TEXT_SIZE = 20;
constexpr size_t MEDIUM_TEXT_SIZE = 28;
constexpr size_t ALERT_TITLE_SIZE = 24;
constexpr size_t ALERT_BODY_SIZE = 72;

// 水位判定阈值 (低于该值认为严重缺水，卡在宠物界面)
constexpr int WATER_LOCK_THRESHOLD = 200;

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

enum PageId {
  PAGE_PET,
  PAGE_CPU,
  PAGE_MEMORY,
  PAGE_WEATHER,
  PAGE_MAIL,
  PAGE_CLOCK,
  PAGE_POMODORO
};

struct UiState {
  char cpu[SMALL_TEXT_SIZE];
  char memory[SMALL_TEXT_SIZE];
  char weather[MEDIUM_TEXT_SIZE];
  char temperature[SMALL_TEXT_SIZE];
  char mood[SMALL_TEXT_SIZE];
  char mail[SMALL_TEXT_SIZE];
  char timeText[SMALL_TEXT_SIZE];
  char dateText[MEDIUM_TEXT_SIZE];
  char pomodoroMode[SMALL_TEXT_SIZE];
  char pomodoroClock[SMALL_TEXT_SIZE];
};

struct AlertState {
  bool active;
  uint32_t expiresAt;
  char title[ALERT_TITLE_SIZE];
  char body[ALERT_BODY_SIZE];
};

UiState ui = {
  "0", "0", "Sunny", "25C", "HAPPY", "0", "--:--", "---- -- --", "FOCUS", "25:00"
};

AlertState alert = {false, 0, "", ""};

PageId currentPage = PAGE_PET;
bool renderDirty = true;

char rxLine[RX_LINE_SIZE];
size_t rxLength = 0;
bool discardUntilNewline = false;

// 水传感器相关变量
int currentWaterValue = 1024;
uint32_t lastWaterReportTime = 0;
constexpr uint32_t WATER_REPORT_INTERVAL = 500; // 每 500ms 采样汇报一次水位

// 蜂鸣器无阻塞蜂鸣控制变量 (低电平触发)
struct BuzzerState {
  bool active;
  uint8_t toggleCount;
  uint32_t lastToggleTime;
  uint16_t intervalMs;
} buzzer = {false, 0, 0, 100};

void copyText(char* target, size_t targetSize, const char* source) {
  if (targetSize == 0) return;
  strncpy(target, source ? source : "", targetSize - 1);
  target[targetSize - 1] = '\0';
}

void setCurrentPage(PageId page) {
  // 如果处于严重缺水状态，锁定在 PET 页面
  if (currentWaterValue < WATER_LOCK_THRESHOLD) {
    currentPage = PAGE_PET;
  } else {
    currentPage = page;
  }
  renderDirty = true;
}

bool alertExpired() {
  return alert.active && static_cast<int32_t>(millis() - alert.expiresAt) >= 0;
}

// 触发蜂鸣器快速响 3 下
void triggerBuzzerTripleBeep() {
  buzzer.active = true;
  buzzer.toggleCount = 0;
  buzzer.lastToggleTime = millis();
  digitalWrite(BUZZER_PIN, LOW); // 低电平触发：拉低发声
}

void updateBuzzer() {
  if (!buzzer.active) return;

  if (millis() - buzzer.lastToggleTime >= buzzer.intervalMs) {
    buzzer.lastToggleTime = millis();
    buzzer.toggleCount++;

    if (buzzer.toggleCount >= 6) { // 响3下 = 3次LOW + 3次HIGH = 6次切换
      buzzer.active = false;
      digitalWrite(BUZZER_PIN, HIGH); // 静音 (拉高)
    } else {
      digitalWrite(BUZZER_PIN, (buzzer.toggleCount % 2 == 0) ? LOW : HIGH);
    }
  }
}

void pollWaterSensor() {
  uint32_t now = millis();
  if (now - lastWaterReportTime >= WATER_REPORT_INTERVAL) {
    lastWaterReportTime = now;
    currentWaterValue = analogRead(WATER_PIN);
    
    // 定时给上位机上报水位
    Serial.print("DATA:WATER|");
    Serial.println(currentWaterValue);

    // 水位过低强制重置为 PET 页并更新显示
    if (currentWaterValue < WATER_LOCK_THRESHOLD && currentPage != PAGE_PET) {
      currentPage = PAGE_PET;
      renderDirty = true;
    }
  }
}

void drawHeader(const char* title) {
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(title);
}

void drawWrappedText(const char* text, uint8_t startY) {
  const char* cursor = text;
  const size_t maxCharsPerLine = 21;

  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  for (uint8_t row = 0; row < 4 && *cursor; row++) {
    char line[maxCharsPerLine + 1];
    size_t used = 0;
    while (*cursor && used < maxCharsPerLine) {
      line[used++] = *cursor++;
    }
    line[used] = '\0';
    display.setCursor(0, startY + row * 11);
    display.println(line);
  }
}

void renderPetPage() {
  drawHeader("[ CYBER PET ]");
  display.setTextSize(2);
  display.setCursor(0, 16);

  // 根据 Mood 或 水位 显示表情
  if (currentWaterValue < WATER_LOCK_THRESHOLD) {
    display.println(F("( O _ O )")); // 缺水呆滞表情
  } else if (strcmp(ui.mood, "BUSY") == 0) {
    display.println(F("( > _ < )"));
  } else if (strcmp(ui.mood, "ANGRY") == 0) {
    display.println(F("( # _ # )"));
  } else if (strcmp(ui.mood, "LAZY") == 0) {
    display.println(F("( - _ - )"));
  } else {
    display.println(F("( ^ _ ^ )"));
  }

  display.setTextSize(1);
  display.setCursor(0, 48);

  // 功能3：当水传感器数值低于 200 时，强行卡在表情页并显示 "I NEED WATER"
  if (currentWaterValue < WATER_LOCK_THRESHOLD) {
    display.setTextColor(SSD1306_BLACK, SSD1306_WHITE); // 反色高亮提示
    display.println(F("  I NEED WATER!  "));
    display.setTextColor(SSD1306_WHITE);
  } else {
    display.print(F("MOOD: "));
    display.println(ui.mood);
  }
}

void renderCpuPage() {
  drawHeader("[ CPU MONITOR ]");
  display.setTextSize(2);
  display.setCursor(0, 20);
  display.print(ui.cpu);
  display.println(F(" %"));
}

void renderMemoryPage() {
  drawHeader("[ RAM MONITOR ]");
  display.setTextSize(2);
  display.setCursor(0, 20);
  display.print(ui.memory);
  display.println(F(" %"));
}

void renderWeatherPage() {
  drawHeader("[ WEATHER ]");
  display.setTextSize(1);
  display.setCursor(0, 16);
  display.println(ui.weather);
  display.setTextSize(2);
  display.setCursor(0, 32);
  display.println(ui.temperature);
}

void renderMailPage() {
  drawHeader("[ UNREAD MAIL ]");
  display.setTextSize(2);
  display.setCursor(0, 20);
  display.println(ui.mail);

  if (strcmp(ui.mail, "0") != 0) {
    display.drawRect(82, 36, 38, 23, SSD1306_WHITE);
    display.drawLine(82, 36, 101, 48, SSD1306_WHITE);
    display.drawLine(120, 36, 101, 48, SSD1306_WHITE);
  }
}

void renderClockPage() {
  drawHeader("[ SYSTEM TIME ]");
  display.setTextSize(2);
  display.setCursor(0, 16);
  display.println(ui.timeText);
  display.setTextSize(1);
  display.setCursor(0, 48);
  display.println(ui.dateText);
}

void renderPomodoroPage() {
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.print(F("[ POMODORO: "));
  display.print(ui.pomodoroMode);
  display.println(F(" ]"));

  display.setTextSize(2);
  display.setCursor(15, 24);
  display.println(ui.pomodoroClock);
}

void renderAlertPage() {
  display.fillRect(0, 0, SCREEN_WIDTH, 12, SSD1306_WHITE);
  display.setTextSize(1);
  display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
  display.setCursor(2, 2);
  display.println(alert.title);
  drawWrappedText(alert.body, 20);
}

void renderCurrentScreen() {
  display.clearDisplay();
  
  // 缺水状态下强制锁定渲染 PET 页面，屏蔽弹窗和切页
  if (currentWaterValue < WATER_LOCK_THRESHOLD) {
    renderPetPage();
  } else if (alert.active) {
    renderAlertPage();
  } else {
    switch (currentPage) {
      case PAGE_CPU: renderCpuPage(); break;
      case PAGE_MEMORY: renderMemoryPage(); break;
      case PAGE_WEATHER: renderWeatherPage(); break;
      case PAGE_MAIL: renderMailPage(); break;
      case PAGE_CLOCK: renderClockPage(); break;
      case PAGE_POMODORO: renderPomodoroPage(); break;
      case PAGE_PET:
      default: renderPetPage(); break;
    }
  }
  display.display();
}

uint8_t splitFields(char* text, char** fields, uint8_t maxFields) {
  if (maxFields == 0) return 0;
  uint8_t count = 0;
  fields[count++] = text;
  for (char* cursor = text; *cursor && count < maxFields; cursor++) {
    if (*cursor == '|') {
      *cursor = '\0';
      fields[count++] = cursor + 1;
    }
  }
  return count;
}

void applyAlert(char** fields, uint8_t count) {
  if (count < 2) return;
  const char* source = fields[0] + 6;  // Skip "ALERT:"
  const char* body = fields[1];

  uint32_t durationMs = 3000;
  if (count >= 3) durationMs = strtoul(fields[2], nullptr, 10);
  if (durationMs < 500) durationMs = 500;
  if (durationMs > 60000) durationMs = 60000;

  snprintf(alert.title, sizeof(alert.title), "! %s ALERT !", source);
  copyText(alert.body, sizeof(alert.body), body);

  alert.expiresAt = millis() + durationMs;
  alert.active = true;
  renderDirty = true;
}

void parseCommand(char* line) {
  char* fields[5];
  uint8_t count = splitFields(line, fields, 5);

  if (count == 0 || fields[0][0] == '\0') return;

  if (strcmp(fields[0], "PAGE:CPU") == 0 && count >= 2) {
    copyText(ui.cpu, sizeof(ui.cpu), fields[1]);
    setCurrentPage(PAGE_CPU);
  } else if (strcmp(fields[0], "PAGE:MEMORY") == 0 && count >= 2) {
    copyText(ui.memory, sizeof(ui.memory), fields[1]);
    setCurrentPage(PAGE_MEMORY);
  } else if (strcmp(fields[0], "PAGE:WEATHER") == 0 && count >= 3) {
    copyText(ui.weather, sizeof(ui.weather), fields[1]);
    copyText(ui.temperature, sizeof(ui.temperature), fields[2]);
    setCurrentPage(PAGE_WEATHER);
  } else if (strcmp(fields[0], "PAGE:PET") == 0 && count >= 2) {
    copyText(ui.mood, sizeof(ui.mood), fields[1]);
    setCurrentPage(PAGE_PET);
  } else if (strcmp(fields[0], "PAGE:MAIL") == 0 && count >= 2) {
    copyText(ui.mail, sizeof(ui.mail), fields[1]);
    setCurrentPage(PAGE_MAIL);
  } else if (strcmp(fields[0], "PAGE:CLOCK") == 0 && count >= 3) {
    copyText(ui.timeText, sizeof(ui.timeText), fields[1]);
    copyText(ui.dateText, sizeof(ui.dateText), fields[2]);
    setCurrentPage(PAGE_CLOCK);
  } else if (strcmp(fields[0], "PAGE:POMODORO") == 0 && count >= 3) {
    copyText(ui.pomodoroMode, sizeof(ui.pomodoroMode), fields[1]);
    copyText(ui.pomodoroClock, sizeof(ui.pomodoroClock), fields[2]);
    setCurrentPage(PAGE_POMODORO);
  } else if (strcmp(fields[0], "CMD:BEEP") == 0) {
    triggerBuzzerTripleBeep();
  } else if (strncmp(fields[0], "ALERT:", 6) == 0) {
    applyAlert(fields, count);
  }
}

void pollSerial() {
  while (Serial.available() > 0) {
    char incoming = static_cast<char>(Serial.read());

    if (incoming == '\r') continue;

    if (incoming == '\n') {
      if (!discardUntilNewline && rxLength > 0) {
        rxLine[rxLength] = '\0';
        parseCommand(rxLine);
      }
      rxLength = 0;
      discardUntilNewline = false;
      continue;
    }

    if (discardUntilNewline) continue;

    if (rxLength < RX_LINE_SIZE - 1) {
      rxLine[rxLength++] = incoming;
    } else {
      rxLength = 0;
      discardUntilNewline = true;
    }
  }
}

void setup() {
  Serial.begin(115200);

  // 初始化蜂鸣器引脚（高电平静音，低电平响）
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);

  Wire.begin(OLED_SDA, OLED_SCL);

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    for (;;) { delay(10); }
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(F("[ CyberPet OS ]"));
  display.setCursor(0, 16);
  display.println(F("Marcus Team V3.0"));
  display.setCursor(0, 32);
  display.println(F("Serial Ready"));
  display.display();
}

void loop() {
  pollSerial();
  pollWaterSensor();
  updateBuzzer();

  if (alertExpired()) {
    alert.active = false;
    renderDirty = true;
  }

  if (renderDirty) {
    renderCurrentScreen();
    renderDirty = false;
  }
}