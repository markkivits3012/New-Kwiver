// Waveshare ESP32-S3-Touch-LCD-1.69 pins (from Waveshare's wiki and schematic),
// and where the BK-880 GPS is wired.
#pragma once

// ST7789V2 LCD, 240x280, SPI (write only)
#define LCD_SCK 6
#define LCD_MOSI 7
#define LCD_CS 5
#define LCD_DC 4
#define LCD_RST 8
#define LCD_BL 15
#define LCD_W 240
#define LCD_H 280
#define LCD_ROW_OFFSET 20  // the panel is 240x280 inside the ST7789's 240x320 memory

// CST816T touch, I2C (shared with the IMU and RTC)
#define TP_SDA 11
#define TP_SCL 10
#define TP_RST 13
#define TP_INT 14
#define TP_ADDR 0x15

// Power: SYS_EN high keeps the board on from the battery; the PWR key reads low when pressed
#define SYS_EN 41
#define PWR_KEY 40
#define BAT_ADC 1           // battery through a 200k/100k divider: x3

// BK-880 on the extension header's UART0 pins (USB serial is separate)
//   BK-880 TX -> GPIO44 (RX), BK-880 RX -> GPIO43 (TX), VCC -> 3V3, GND -> GND
#define GPS_RX 44
#define GPS_TX 43
