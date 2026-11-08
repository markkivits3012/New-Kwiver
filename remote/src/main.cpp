// Handheld remote for the golf cart: reads the BK-880 GPS, joins the Pi's
// hotspot, sends a signed heartbeat with its position every second, and a big
// STOP button. Wire format and session rules: ../PROTOCOL.md (keep in step
// with server/remote.py).
//
// USB serial commands (115200): PAIR <ssid> <password> <32-hex key>, UNPAIR, STATUS, SCAN.
// It also prints every Wi-Fi join and drop with the reason.
#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <TinyGPSPlus.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_wifi.h>
#include <Wire.h>
#include <mbedtls/md.h>

#include "board.h"

static const IPAddress PI_IP(10, 42, 0, 1);
static const uint16_t PI_PORT = 8091;
static const uint32_t SEND_MS = 1000;      // hello / heartbeat
static const uint32_t LINK_MS = 3000;      // no ack this long: link lost
static const uint32_t REHELLO_MS = 10000;  // no ack this long: start a new session
static const uint32_t JOIN_MS = 20000;     // not on the hotspot this long since the last try: try again
static const uint32_t FIX_STALE_MS = 3000;
static const uint32_t GPS_BAUDS[] = {38400, 9600, 115200};  // BK-880: M10 units ship at 38400, older at 9600
static const float ACC_PER_HDOP_M = 2.5;   // no GST in the default NMEA: estimate error from HDOP
static const int STOP_COPIES = 5;
static const uint32_t STOP_GAP_MS = 100;
static const uint32_t POWER_HOLD_MS = 2000;

// colours (RGB565)
static const uint16_t C_BG = 0x0861, C_FG = 0xFFFF, C_MUTED = 0x9CD3, C_GREEN = 0x3E8F,
                      C_AMBER = 0xF660, C_RED = 0xF20A, C_RED_DARK = 0x9800;
// STOP button: the lower half of the screen, clear of the rounded corners
static const int16_t SX = 14, SY = 150, SW = LCD_W - 28, SH = 112;

Arduino_DataBus *bus = new Arduino_ESP32SPI(LCD_DC, LCD_CS, LCD_SCK, LCD_MOSI, GFX_NOT_DEFINED);
Arduino_GFX *gfx = new Arduino_ST7789(bus, LCD_RST, 0, true, LCD_W, LCD_H, 0, LCD_ROW_OFFSET, 0, LCD_ROW_OFFSET);
HardwareSerial GPS(1);
TinyGPSPlus gps;
TinyGPSCustom ggaQ(gps, "GPGGA", 6), gnggaQ(gps, "GNGGA", 6);  // fix quality
WiFiUDP udp;
Preferences prefs;

String ssid, pass;
uint32_t joinAt = 0;  // when the last attempt to join the hotspot started
uint8_t key[16];
bool paired = false;

char sid[17] = "";       // session from the Pi's welcome; empty = say hello
char nonce[9] = "";
uint32_t seq = 0, lastSend = 0, lastAck = 0, sessionStart = 0;
String trolley = "";     // from the last ack
int stopLeft = 0;
uint32_t nextStop = 0, stopPressed = 0;
uint32_t baudIdx = 0, baudSince = 0, lastPassed = 0;
bool touching = false;
uint32_t pwrDown = 0;

// ---- signing --------------------------------------------------------------------
void mac(const char *body, size_t n, char out[33]) {
  uint8_t h[32];
  mbedtls_md_context_t c;
  mbedtls_md_init(&c);
  mbedtls_md_setup(&c, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&c, key, sizeof key);
  mbedtls_md_hmac_update(&c, (const uint8_t *)body, n);
  mbedtls_md_hmac_finish(&c, h);
  mbedtls_md_free(&c);
  for (int i = 0; i < 16; i++) sprintf(out + 2 * i, "%02x", h[i]);
}

void sendSigned(const char *body) {
  char m[33];
  mac(body, strlen(body), m);
  udp.beginPacket(PI_IP, PI_PORT);
  udp.write((const uint8_t *)m, 32);
  udp.write(' ');
  udp.write((const uint8_t *)body, strlen(body));
  udp.endPacket();
}

// ---- GPS ------------------------------------------------------------------------
bool fixFresh() { return gps.location.isValid() && gps.location.age() < FIX_STALE_MS; }

int fixQuality() {
  const char *q = gnggaQ.isValid() ? gnggaQ.value() : ggaQ.isValid() ? ggaQ.value() : "0";
  return atoi(q);
}

// Find the module's baud rate: move on if no good NMEA arrives for 3 s.
void readGps() {
  while (GPS.available()) gps.encode(GPS.read());
  if (gps.passedChecksum() != lastPassed) {
    lastPassed = gps.passedChecksum();
    baudSince = millis();
  } else if (millis() - baudSince > 3000) {
    baudIdx = (baudIdx + 1) % (sizeof GPS_BAUDS / sizeof GPS_BAUDS[0]);
    GPS.updateBaudRate(GPS_BAUDS[baudIdx]);
    baudSince = millis();
  }
}

int batteryPct() {
  float v = analogReadMilliVolts(BAT_ADC) * 3 / 1000.0;
  return constrain((int)((v - 3.3) / (4.15 - 3.3) * 100), 0, 100);
}

// ---- link -----------------------------------------------------------------------
bool linked() { return sid[0] && lastAck && millis() - lastAck < LINK_MS; }

void sendHello() {
  snprintf(nonce, sizeof nonce, "%08lx", (unsigned long)esp_random());
  char b[64];
  snprintf(b, sizeof b, "{\"k\":\"hello\",\"n\":\"%s\"}", nonce);
  sendSigned(b);
}

void sendHeartbeat() {
  char f[128] = "null", b[256];
  if (fixFresh()) {
    int q = fixQuality();
    char qs[5] = "null";
    if (q > 0) snprintf(qs, sizeof qs, "%d", q);
    float acc = gps.hdop.isValid() ? gps.hdop.hdop() * ACC_PER_HDOP_M : 0;
    snprintf(f, sizeof f, "{\"lat\":%.7f,\"lon\":%.7f,\"acc\":%.1f,\"q\":%s,\"sats\":%lu}",
             gps.location.lat(), gps.location.lng(), acc, qs, (unsigned long)gps.satellites.value());
  }
  snprintf(b, sizeof b, "{\"k\":\"hb\",\"sid\":\"%s\",\"seq\":%lu,\"fix\":%s,\"bat\":%d}",
           sid, (unsigned long)++seq, f, batteryPct());
  sendSigned(b);
}

void sendStop() {
  char b[64];
  snprintf(b, sizeof b, "{\"k\":\"stop\",\"sid\":\"%s\",\"seq\":%lu}", sid, (unsigned long)++seq);
  sendSigned(b);
}

void readReplies() {
  int n;
  while ((n = udp.parsePacket()) > 0) {
    char buf[480];
    n = udp.read(buf, sizeof buf - 1);
    if (n < 34 || buf[32] != ' ') continue;
    buf[n] = 0;
    char m[33];
    mac(buf + 33, n - 33, m);
    if (memcmp(m, buf, 32) != 0) continue;  // not from our Pi
    JsonDocument d;
    if (deserializeJson(d, buf + 33)) continue;
    const char *k = d["k"] | "";
    if (!strcmp(k, "welcome") && !sid[0] && !strcmp(d["n"] | "", nonce)) {
      strlcpy(sid, d["sid"] | "", sizeof sid);
      seq = 0;
      lastAck = sessionStart = millis();
    } else if (!strcmp(k, "ack")) {
      lastAck = millis();
      trolley = d["trolley"] | "";
    } else if (!strcmp(k, "rehello")) {
      sid[0] = 0;
    }
  }
}

void network() {
  if (!paired) return;
  if (WiFi.status() != WL_CONNECTED) {
    // The core's auto-reconnect gives up for good if the hotspot wasn't there at
    // the first try (remote switched on before the cart), so start a fresh attempt
    // ourselves, but only once the last one has had time to finish: joining plus
    // DHCP can take several seconds, and restarting midway means it never gets there.
    if (millis() - joinAt >= JOIN_MS) {
      WiFi.disconnect();
      delay(100);  // let the disconnect land first, or begin() can be ignored
      WiFi.begin(ssid.c_str(), pass.c_str());
      joinAt = millis();
    }
    return;
  }
  readReplies();
  if (stopLeft && millis() >= nextStop) {
    sendStop();
    stopLeft--;
    nextStop = millis() + STOP_GAP_MS;
  }
  if (millis() - lastSend < SEND_MS) return;
  lastSend = millis();
  if (sid[0] && millis() - lastAck > REHELLO_MS) sid[0] = 0;  // Pi gone a while: start over
  if (sid[0]) sendHeartbeat();
  else sendHello();
}

// ---- pairing over USB serial -------------------------------------------------------
bool parseKey(const String &hex, uint8_t out[16]) {
  if (hex.length() != 32) return false;
  for (int i = 0; i < 16; i++) {
    char *end;
    char two[3] = {hex[2 * i], hex[2 * i + 1], 0};
    out[i] = strtoul(two, &end, 16);
    if (*end) return false;
  }
  return true;
}

void loadPairing() {
  prefs.begin("remote", true);
  ssid = prefs.getString("ssid", "");
  pass = prefs.getString("pass", "");
  paired = ssid.length() && parseKey(prefs.getString("key", ""), key);
  prefs.end();
  WiFi.disconnect();
  sid[0] = 0;
  if (paired) WiFi.begin(ssid.c_str(), pass.c_str());
  joinAt = millis();
}

// ---- Wi-Fi diagnostics over USB serial ---------------------------------------------
// Prints every join, address and drop with the chip's reason code, so a remote that
// won't stay on the hotspot says why (the Pi logs nothing for refused attempts).
const char *dropReason(uint8_t r) {
  switch (r) {
    case 2: return "auth expired";
    case 4: case 200: return "lost the hotspot's signal";
    case 8: return "we left (retrying)";
    case 15: case 204: return "handshake timed out: wrong password or weak signal";
    case 201: return "hotspot not found";
    case 202: return "refused: wrong password or security mismatch";
    case 203: return "association refused";
    default: return "";
  }
}

void wifiEvent(WiFiEvent_t e, WiFiEventInfo_t info) {
  // the core retries a failed join several times a second: print a reason once,
  // then only every 20th repeat, so the serial monitor stays usable
  static uint8_t lastReason = 0;
  static uint32_t repeats = 0;
  if (e == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
    lastReason = 0;
    Serial.println("wifi: joined the hotspot");
  } else if (e == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    Serial.printf("wifi: got address %s, signal %d dBm\n",
                  IPAddress(info.got_ip.ip_info.ip.addr).toString().c_str(), (int)WiFi.RSSI());
  } else if (e == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    uint8_t r = info.wifi_sta_disconnected.reason;
    if (r == lastReason && ++repeats % 20) return;
    if (r != lastReason) repeats = 1;
    lastReason = r;
    Serial.printf("wifi: not connected, reason %u %s%s\n", r, dropReason(r),
                  repeats > 1 ? " (still)" : "");
  }
}

void scan() {
  Serial.println("scanning...");
  // the chip can't scan while it's busy joining: stop joining, scan, then rejoin
  WiFi.setAutoReconnect(false);
  int n = -1;
  for (int i = 0; i < 3 && n < 0; i++) {
    WiFi.disconnect();
    delay(300);
    n = WiFi.scanNetworks();
  }
  WiFi.setAutoReconnect(true);
  joinAt = millis() - JOIN_MS;  // network() starts a fresh join right away
  if (n < 0) {
    Serial.println("ERR scan failed: try again");
    return;
  }
  for (int i = 0; i < n; i++)
    Serial.printf("%s channel %d signal %d dBm security %d\n", WiFi.SSID(i).c_str(), (int)WiFi.channel(i),
                  (int)WiFi.RSSI(i), (int)WiFi.encryptionType(i));
  Serial.printf("%d networks\n", n);
  WiFi.scanDelete();
}

void serialCommand() {
  static String line;
  while (Serial.available()) {
    char c = Serial.read();
    if (c != '\n' && c != '\r') {
      if (line.length() < 200) line += c;
      continue;
    }
    line.trim();
    if (line.startsWith("PAIR ")) {
      int a = line.indexOf(' ', 5), b = line.indexOf(' ', a + 1);
      String s = line.substring(5, a), p = line.substring(a + 1, b), k = line.substring(b + 1);
      uint8_t tmp[16];
      if (a < 0 || b < 0 || !parseKey(k, tmp)) {
        Serial.println("ERR expected: PAIR <ssid> <password> <32 hex key>");
      } else {
        prefs.begin("remote", false);
        prefs.putString("ssid", s);
        prefs.putString("pass", p);
        prefs.putString("key", k);
        prefs.end();
        // Restart rather than loadPairing(): a join already in progress keeps the
        // old password and the new one only took effect after a reset.
        Serial.printf("OK paired with %s, restarting\n", s.c_str());
        Serial.flush();
        delay(200);
        ESP.restart();
      }
    } else if (line == "SCAN") {
      scan();
    } else if (line == "UNPAIR") {
      prefs.begin("remote", false);
      prefs.clear();
      prefs.end();
      loadPairing();
      Serial.println("OK unpaired");
    } else if (line == "STATUS") {
      // wifi is the core's wl_status_t: 3 connected, 1 hotspot not seen, 4 join refused
      // (wrong password), 6 disconnected / still trying, 0 idle
      Serial.printf("paired=%d ssid=%s wifi=%d ip=%s rssi=%d linked=%d trolley=%s gps_baud=%lu fix=%d sats=%lu bat=%d%%\n",
                    paired, ssid.c_str(), (int)WiFi.status(), WiFi.localIP().toString().c_str(), (int)WiFi.RSSI(), linked(), trolley.c_str(),
                    (unsigned long)GPS_BAUDS[baudIdx], fixFresh(), (unsigned long)gps.satellites.value(),
                    batteryPct());
    } else if (line.length()) {
      Serial.println("ERR commands: PAIR <ssid> <password> <key>, UNPAIR, STATUS, SCAN");
    }
    line = "";
  }
}

// ---- touch (CST816T) -----------------------------------------------------------------
void touchBegin() {
  pinMode(TP_RST, OUTPUT);
  digitalWrite(TP_RST, LOW);
  delay(10);
  digitalWrite(TP_RST, HIGH);
  delay(50);
  Wire.begin(TP_SDA, TP_SCL);
  Wire.beginTransmission(TP_ADDR);  // DisAutoSleep: else it naps and misses the first tap
  Wire.write(0xFE);
  Wire.write(1);
  Wire.endTransmission();
}

bool touchRead(int16_t &x, int16_t &y) {
  Wire.beginTransmission(TP_ADDR);
  Wire.write(0x02);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(TP_ADDR, 5) != 5) return false;
  uint8_t d[5];
  for (int i = 0; i < 5; i++) d[i] = Wire.read();
  if (!(d[0] & 0x0F)) return false;
  x = ((d[1] & 0x0F) << 8) | d[2];
  y = ((d[3] & 0x0F) << 8) | d[4];
  return true;
}

// ---- screen -------------------------------------------------------------------------
void drawStop(bool down) {
  gfx->fillRoundRect(SX, SY, SW, SH, 22, down ? C_RED_DARK : C_RED);
  gfx->setTextColor(C_FG);
  gfx->setTextSize(6);
  gfx->setCursor(SX + (SW - 4 * 36) / 2, SY + (SH - 48) / 2);
  gfx->print("STOP");
}

void line(int16_t y, uint16_t dot, const String &text, uint16_t color = C_FG) {
  gfx->fillRect(10, y, LCD_W - 20, 24, C_BG);
  if (dot != C_BG) gfx->fillCircle(26, y + 10, 7, dot);
  gfx->setTextSize(2);
  gfx->setTextColor(color, C_BG);
  gfx->setCursor(42, y + 3);
  gfx->print(text.substring(0, 16));
}

void drawStatus() {
  if (!paired) line(28, C_AMBER, "Not paired");
  else if (WiFi.status() != WL_CONNECTED) line(28, C_RED, "No hotspot");
  else if (linked()) line(28, C_GREEN, "Linked");
  else line(28, C_AMBER, "Finding cart");

  if (fixFresh()) {
    char s[24];
    float acc = gps.hdop.isValid() ? gps.hdop.hdop() * ACC_PER_HDOP_M : 0;
    snprintf(s, sizeof s, "%lu sats +-%.0fm", (unsigned long)gps.satellites.value(), acc);
    line(58, C_GREEN, s);
  } else {
    line(58, gps.passedChecksum() ? C_AMBER : C_RED, gps.passedChecksum() ? "No GPS fix" : "No GPS data");
  }

  String t;
  if (stopPressed && millis() - stopPressed < 15000)
    t = trolley == "stopped" ? "Trolley stopped" : trolley == "none" ? "No trolley" : "STOP sent";
  else if (linked() && trolley.length() && trolley != "none")
    t = "Trolley " + trolley;
  line(88, C_BG, t, C_MUTED);
  line(118, C_BG, "Battery " + String(batteryPct()) + "%", C_MUTED);
}

void touch() {
  int16_t x, y;
  bool now = touchRead(x, y);
  bool onStop = now && x >= SX && x < SX + SW && y >= SY && y < SY + SH;
  if (onStop && !touching) {  // act on touch-down: no waiting for release
    stopLeft = STOP_COPIES;
    nextStop = 0;
    stopPressed = millis();
    drawStop(true);
  } else if (!now && touching) {
    drawStop(false);
  }
  touching = now && (onStop || touching);
}

void powerKey() {
  if (digitalRead(PWR_KEY) == LOW) {
    if (!pwrDown) pwrDown = millis();
    if (millis() - pwrDown > POWER_HOLD_MS) {
      gfx->fillScreen(C_BG);
      digitalWrite(LCD_BL, LOW);
      digitalWrite(SYS_EN, LOW);  // cuts battery power; on USB it just stays dark
      while (digitalRead(PWR_KEY) == LOW) delay(10);
      ESP.restart();
    }
  } else {
    pwrDown = 0;
  }
}

// ---- main -----------------------------------------------------------------------------
void setup() {
  pinMode(SYS_EN, OUTPUT);
  digitalWrite(SYS_EN, HIGH);  // keep running on battery once the PWR key is let go
  pinMode(PWR_KEY, INPUT_PULLUP);
  Serial.begin(115200);
  GPS.begin(GPS_BAUDS[0], SERIAL_8N1, GPS_RX, GPS_TX);
  baudSince = millis();

  gfx->begin();
  gfx->fillScreen(C_BG);
  pinMode(LCD_BL, OUTPUT);
  digitalWrite(LCD_BL, HIGH);
  gfx->setTextSize(1);
  gfx->setTextColor(C_MUTED);
  gfx->setCursor(86, 10);
  gfx->print("CART REMOTE");
  drawStop(false);
  touchBegin();

  WiFi.onEvent(wifiEvent);
  WiFi.mode(WIFI_STA);
  // A new random hardware address each start: the Pi's Wi-Fi chip kept refusing the
  // remote's fixed address (reason 202, strong signal, right password) while it let
  // other devices in. Locally administered, unicast. The pairing key is what the
  // cart trusts, not the address.
  uint8_t mac[6];
  esp_fill_random(mac, sizeof mac);
  mac[0] = (mac[0] & 0xFE) | 0x02;
  esp_wifi_set_mac(WIFI_IF_STA, mac);
  Serial.printf("wifi: using address %02x:%02x:%02x:%02x:%02x:%02x\n", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  WiFi.setSleep(false);  // light sleep delays replies by up to a beacon interval
  WiFi.setAutoReconnect(true);
  udp.begin(PI_PORT);
  loadPairing();
}

void loop() {
  static uint32_t lastTouch = 0, lastDraw = 0;
  serialCommand();
  readGps();
  if (millis() - lastTouch >= 20) {
    lastTouch = millis();
    touch();
  }
  network();
  powerKey();
  if (millis() - lastDraw >= 500) {
    lastDraw = millis();
    drawStatus();
  }
  delay(2);
}
