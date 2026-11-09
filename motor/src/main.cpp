// Motor controller for the cart's two hoverboard wheels: a NodeMCU (ESP8266) driving
// two ZS-X11H BLDC boards. For now it's driven from the serial monitor with the wheels
// off the ground; later the Pi sends the same commands over USB.
//
// It fails safe on its own, whatever the other end does:
// - a drive command lasts RUN_MS at most, then the wheels stop and brake: a lost link,
//   a crash on the Pi or a forgotten test can't leave them turning
// - speed ramps (no jerks), direction only changes once a wheel has stopped,
//   and speed is capped at MAX_PCT while testing
//
// Serial commands (115200, one per line):
//   F 20     both wheels forward at 20%      B 20    both backward
//   L 20     left wheel only (-20 = back)    R 20    right wheel only
//   S        stop and brake now              STATUS  what each wheel is doing
#include <Arduino.h>

static const int MAX_PCT = 30;           // raise once the cart is trusted
static const uint32_t RUN_MS = 3000;     // a drive command lasts this long unless repeated
static const uint32_t STEP_MS = 20;      // ramp: 1% every 20 ms, 0 to 30% in 0.6 s
static const uint32_t DIR_SETTLE_MS = 300;  // at a standstill this long before reversing

// The ZS-X11H wants PWM at 1-20 kHz on its speed input (jumper on its speed knob
// removed), brake on a high level. Which DR level is "forward" depends on how a wheel
// is mounted and wired: if one turns the wrong way, flip its `rev`.
struct Motor {
  const char *name;
  uint8_t pwm, dir, brake;
  bool rev;
  int target = 0, now = 0;  // percent, + forward
  uint32_t stillSince = 0;  // when `now` reached 0
};

// NodeMCU labels: D1 = GPIO5, D2 = GPIO4, D5 = GPIO14, D6 = GPIO12, D7 = GPIO13,
// D0 = GPIO16. D1 and D2 stay low through boot and reset, so the speed lines can't
// twitch a wheel; still fit a 10k pull-down on each speed line to be sure.
Motor motors[2] = {
  {"left", 5, 14, 13, true},    // speed D1, direction D5, brake D7 (rev: ran backward)
  {"right", 4, 12, 16, false},  // speed D2, direction D6, brake D0
};

uint32_t runUntil = 0;
bool running = false;

void apply(Motor &m) {
  bool fwd = m.now >= 0;
  digitalWrite(m.dir, fwd != m.rev ? HIGH : LOW);
  digitalWrite(m.brake, m.now == 0 && m.target == 0 ? HIGH : LOW);
  analogWrite(m.pwm, abs(m.now) * 10);  // range 0-1000
}

void stopNow(const char *why) {
  for (Motor &m : motors) {
    m.target = m.now = 0;
    m.stillSince = millis();
    apply(m);
  }
  if (running) Serial.printf("stopped: %s\n", why);
  running = false;
}

void ramp(Motor &m) {
  if (m.now == m.target) return;
  // reversing: come down to 0, wait, then go up the other way
  bool crossing = m.now != 0 && (m.now > 0) != (m.target > 0) && m.target != 0;
  int goal = crossing ? 0 : m.target;
  if (m.now == 0 && goal != 0 && millis() - m.stillSince < DIR_SETTLE_MS) return;
  m.now += goal > m.now ? 1 : (goal < m.now ? -1 : 0);
  if (m.now == 0) m.stillSince = millis();
  apply(m);
}

void drive(int left, int right) {
  left = constrain(left, -MAX_PCT, MAX_PCT);
  right = constrain(right, -MAX_PCT, MAX_PCT);
  motors[0].target = left;
  motors[1].target = right;
  for (Motor &m : motors) apply(m);  // releases the brake on a wheel that's going to move
  runUntil = millis() + RUN_MS;
  running = left || right;
  Serial.printf("OK left %d%% right %d%% for %lu s (max %d%%)\n", left, right,
                (unsigned long)(RUN_MS / 1000), MAX_PCT);
}

void status() {
  for (Motor &m : motors)
    Serial.printf("%s: target %d%% now %d%% %s\n", m.name, m.target, m.now,
                  m.now == 0 && m.target == 0 ? "braked" : "");
  if (running) Serial.printf("stops in %ld ms unless repeated\n", (long)(runUntil - millis()));
}

void command(String line) {
  line.trim();
  line.toUpperCase();
  if (!line.length()) return;
  char c = line[0];
  int pct = line.substring(1).toInt();
  if (c == 'S' && line != "STATUS") stopNow("asked");
  else if (line == "STATUS") status();
  else if (c == 'F') drive(pct, pct);
  else if (c == 'B') drive(-pct, -pct);
  else if (c == 'L') drive(pct, motors[1].target);
  else if (c == 'R') drive(motors[0].target, pct);
  else Serial.println("ERR commands: F <pct>, B <pct>, L <pct>, R <pct>, S, STATUS");
}

void setup() {
  for (Motor &m : motors) {
    pinMode(m.pwm, OUTPUT);
    pinMode(m.dir, OUTPUT);
    pinMode(m.brake, OUTPUT);
  }
  analogWriteRange(1000);
  analogWriteFreq(10000);  // inside the board's 1-20 kHz
  stopNow("start");
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nmotor controller: F/B/L/R <pct>, S, STATUS. Max %d%%, each command lasts %lu s\n",
                MAX_PCT, (unsigned long)(RUN_MS / 1000));
}

void loop() {
  static String buf;
  static uint32_t lastStep = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      command(buf);
      buf = "";
    } else if (buf.length() < 40) {
      buf += ch;
    }
  }
  if (running && (int32_t)(millis() - runUntil) >= 0) stopNow("time up (repeat the command to keep going)");
  if (millis() - lastStep >= STEP_MS) {
    lastStep = millis();
    for (Motor &m : motors) ramp(m);
  }
}
