#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_MLX90614.h>

#include <esp_err.h>
#include <esp_wifi.h>
#include <sdkconfig.h>
#include <soc/soc_caps.h>

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#include <lwip/ip_addr.h>
#include <ping/ping_sock.h>
#include <math.h>
#include <string.h>

// ============================================================================
// 모듈별 설정은 빌드 플래그로 바꿉니다.
// room_1: -DPIUDA_SENSOR_ID=\"room_1\" -DPIUDA_HAS_IR_SENSOR=1
// room_2: -DPIUDA_SENSOR_ID=\"room_2\" -DPIUDA_HAS_IR_SENSOR=0
// ============================================================================
#ifndef PIUDA_SENSOR_ID
#define PIUDA_SENSOR_ID "room_1"
#endif
#ifndef PIUDA_HAS_IR_SENSOR
#define PIUDA_HAS_IR_SENSOR 1
#endif

#define SENSOR_ID         PIUDA_SENSOR_ID
#define HAS_IR_SENSOR     (PIUDA_HAS_IR_SENSOR != 0)
#define PIUDA_WIFI_SSID   "PIUDA-CNU"
#define PIUDA_WIFI_PASSWORD "piuda3017"
#define PIUDA_SERVER_HOST "192.168.4.1"
#define PIUDA_SERVER_PORT 8080
#define PIUDA_SENSOR_KEY  "piuda-demo-3017"

// ────────────── 핀 설정 ──────────────
#define PIR_PIN   14
#define LED_PIN    2
#define SDA_PIN   21
#define SCL_PIN   22

// ────────────── 일반 설정값 ──────────────
#define WIFI_RETRY_TIMEOUT_MS       20000UL
#define PIR_STABILIZE_MS             2000UL
#define PIR_SAMPLE_INTERVAL_MS        100UL
#define PIR_DEBOUNCE_MS               200UL
#define SENSOR_REPORT_INTERVAL_MS    1000UL  // 실시간 시연용 서버 갱신 주기
#define HTTP_TIMEOUT_MS              2000UL
#define HTTP_FAIL_LIMIT                   5

// ────────────── CSI 설정값 ──────────────
#define CSI_REPORT_INTERVAL_MS       1000UL
#define CSI_PING_INTERVAL_MS          500UL  // 일반 공유기 보호: 초당 약 2회 ping
#define CSI_PING_RESTART_MS         120000UL // 장시간 세션 정지 예방
#define CSI_HEALTH_CHECK_MS          30000UL
#define CSI_MIN_PACKETS_PER_CHECK         10
#define CSI_MAX_BYTES                    384 // 기존 ESP32의 최대 CSI 길이
#define CSI_QUEUE_DEPTH                   16
#define UPLOAD_QUEUE_DEPTH                  4

#if !SOC_WIFI_CSI_SUPPORT
#error "선택한 ESP32 칩은 Wi-Fi CSI를 지원하지 않습니다."
#endif

#if !CONFIG_ESP_WIFI_CSI_ENABLED
#error "현재 ESP32 Arduino Core 빌드에서 Wi-Fi CSI가 비활성화되어 있습니다. Arduino-ESP32 3.3.8 이상을 사용하세요."
#endif

#if CONFIG_SOC_WIFI_HE_SUPPORT
#error "이 스케치는 기존 ESP32 계열용 CSI 설정 구조체를 사용합니다. ESP32-C5/C6용 설정을 별도로 추가해야 합니다."
#endif

// ────────────── 객체와 상태 ──────────────
Adafruit_MLX90614 mlx;

String serverIP;
String serverUrl;

bool irReady = false;
bool csiRunning = false;
bool pirReady = false;
int httpFailCount = 0;
int pirStableState = LOW;
int pirCandidateState = LOW;

unsigned long wifiLostTime = 0;
unsigned long pirCandidateSince = 0;
unsigned long lastPirSampleAt = 0;
unsigned long lastSensorReportAt = 0;
unsigned long csiPingStartedAt = 0;
unsigned long lastCsiHealthCheckAt = 0;
uint32_t lastCsiHealthPacketCount = 0;

uint8_t routerBssid[6] = {0};
bool routerBssidValid = false;

QueueHandle_t csiQueue = nullptr;
QueueHandle_t uploadQueue = nullptr;
TaskHandle_t csiTaskHandle = nullptr;
TaskHandle_t uploadTaskHandle = nullptr;
esp_ping_handle_t csiPingHandle = nullptr;

volatile uint32_t csiPacketCount = 0;
volatile uint32_t csiDroppedCount = 0;

struct CsiFrame {
  uint32_t sequence;
  int8_t rssi;
  uint16_t len;
  bool firstWordInvalid;
  int8_t data[CSI_MAX_BYTES];
};

struct CsiSummary {
  bool valid;
  uint32_t packetCount;
  float packetRate;
  int8_t rssi;
  uint16_t length;
  float meanAmplitude;
  float amplitudeStddev;
  float peakDelta;
  uint32_t droppedCount;
};

struct ModuleReading {
  int pir;
  bool hasTemperature;
  float ambient;
  float object;
  CsiSummary csi;
  char reason[24];
};

CsiSummary latestCsi = {};
portMUX_TYPE csiSummaryMux = portMUX_INITIALIZER_UNLOCKED;

// ─────────────────────────────
// 전방 선언
// ─────────────────────────────
void startCSI();
void stopCSI();
bool connectWiFi();

// ─────────────────────────────
// CSI 콜백
// Wi-Fi task 문맥에서 실행되므로 필터링, 복사, Queue 전송만 수행합니다.
// ─────────────────────────────
void csiRxCallback(void *ctx, wifi_csi_info_t *info) {
  if (!csiQueue || !info || !info->buf || info->len < 2) {
    return;
  }

  // 일반 promiscuous 패킷 중 현재 연결된 공유기에서 온 프레임만 사용합니다.
  if (routerBssidValid && memcmp(info->mac, routerBssid, 6) != 0) {
    return;
  }

  CsiFrame frame;
  frame.sequence = ++csiPacketCount;
  frame.rssi = info->rx_ctrl.rssi;
  frame.len = info->len > CSI_MAX_BYTES ? CSI_MAX_BYTES : info->len;
  frame.firstWordInvalid = info->first_word_invalid;
  memcpy(frame.data, info->buf, frame.len);

  if (xQueueSend(csiQueue, &frame, 0) != pdTRUE) {
    ++csiDroppedCount;
  }
}

// ─────────────────────────────
// CSI 처리 task
// Queue에서 데이터를 꺼내 amplitude 통계를 계산하고 1초마다 한 줄만 출력합니다.
// ─────────────────────────────
void csiProcessingTask(void *parameter) {
  CsiFrame frame;

  uint32_t windowPackets = 0;
  uint32_t amplitudeSamples = 0;
  double amplitudeSum = 0.0;
  double amplitudeSquareSum = 0.0;
  float previousPeak = NAN;
  float maxPeakDelta = 0.0f;
  int8_t lastRssi = 0;
  uint16_t lastLength = 0;
  unsigned long lastReportAt = millis();

  for (;;) {
    if (xQueueReceive(csiQueue, &frame, pdMS_TO_TICKS(50)) == pdTRUE) {
      size_t start = frame.firstWordInvalid ? 4 : 0;
      float packetPeak = 0.0f;

      // CSI 버퍼는 [imaginary, real] int8 쌍입니다. amplitude에는 순서가 영향 없습니다.
      for (size_t i = start; i + 1 < frame.len; i += 2) {
        const float imaginary = frame.data[i];
        const float real = frame.data[i + 1];
        const float amplitude = sqrtf(real * real + imaginary * imaginary);

        amplitudeSum += amplitude;
        amplitudeSquareSum += (double)amplitude * amplitude;
        ++amplitudeSamples;
        if (amplitude > packetPeak) {
          packetPeak = amplitude;
        }
      }

      if (!isnan(previousPeak)) {
        const float delta = fabsf(packetPeak - previousPeak);
        if (delta > maxPeakDelta) {
          maxPeakDelta = delta;
        }
      }
      previousPeak = packetPeak;
      lastRssi = frame.rssi;
      lastLength = frame.len;
      ++windowPackets;
    }

    const unsigned long now = millis();
    if (now - lastReportAt >= CSI_REPORT_INTERVAL_MS) {
      if (windowPackets > 0 && amplitudeSamples > 0) {
        const double mean = amplitudeSum / amplitudeSamples;
        double variance = amplitudeSquareSum / amplitudeSamples - mean * mean;
        if (variance < 0.0) {
          variance = 0.0; // 부동소수점 반올림 오차 방지
        }

        const float packetRate = windowPackets * 1000.0f / (now - lastReportAt);
        const float stddev = sqrt(variance);
        Serial.printf(
          "[CSI] sensor_id=%s packets=%lu (+%lu) dropped=%lu rssi=%d len=%u "
          "mean_amp=%.2f stddev=%.2f peak_delta=%.2f\n",
          SENSOR_ID,
          (unsigned long)csiPacketCount,
          (unsigned long)windowPackets,
          (unsigned long)csiDroppedCount,
          lastRssi,
          lastLength,
          mean,
          stddev,
          maxPeakDelta
        );

        portENTER_CRITICAL(&csiSummaryMux);
        latestCsi.valid = true;
        latestCsi.packetCount = csiPacketCount;
        latestCsi.packetRate = packetRate;
        latestCsi.rssi = lastRssi;
        latestCsi.length = lastLength;
        latestCsi.meanAmplitude = mean;
        latestCsi.amplitudeStddev = stddev;
        latestCsi.peakDelta = maxPeakDelta;
        latestCsi.droppedCount = csiDroppedCount;
        portEXIT_CRITICAL(&csiSummaryMux);
      } else if (csiRunning) {
        Serial.printf(
          "[CSI] sensor_id=%s packets=%lu (+0) dropped=%lu - 수신 패킷 없음\n",
          SENSOR_ID,
          (unsigned long)csiPacketCount,
          (unsigned long)csiDroppedCount
        );
      }

      windowPackets = 0;
      amplitudeSamples = 0;
      amplitudeSum = 0.0;
      amplitudeSquareSum = 0.0;
      maxPeakDelta = 0.0f;
      lastReportAt = now;
    }
  }
}

// ─────────────────────────────
// 공유기에 소량의 ping을 보내 응답 패킷을 안정적으로 발생시킵니다.
// 별도 CSI 송신 ESP32는 필요하지 않습니다.
// ─────────────────────────────
void startRouterPing() {
  if (csiPingHandle || WiFi.status() != WL_CONNECTED) {
    return;
  }

  const IPAddress gateway = WiFi.gatewayIP();
  if (gateway == IPAddress(0, 0, 0, 0)) {
    Serial.println("[CSI] 공유기 게이트웨이 주소를 확인할 수 없어 ping을 시작하지 못했습니다.");
    return;
  }

  ip_addr_t target = {};
  target.type = IPADDR_TYPE_V4;
  IP4_ADDR(&target.u_addr.ip4, gateway[0], gateway[1], gateway[2], gateway[3]);

  esp_ping_config_t pingConfig = ESP_PING_DEFAULT_CONFIG();
  pingConfig.target_addr = target;
  pingConfig.count = 0; // 계속 실행
  pingConfig.interval_ms = CSI_PING_INTERVAL_MS;
  pingConfig.timeout_ms = 1000;
  pingConfig.data_size = 1;
  pingConfig.task_stack_size = 2048;

  esp_ping_callbacks_t callbacks = {};
  esp_err_t err = esp_ping_new_session(&pingConfig, &callbacks, &csiPingHandle);
  if (err == ESP_OK) {
    err = esp_ping_start(csiPingHandle);
  }

  if (err == ESP_OK) {
    csiPingStartedAt = millis();
    Serial.printf("[CSI] 공유기 %s에 %lums 간격 ping 시작\n",
                  gateway.toString().c_str(), (unsigned long)CSI_PING_INTERVAL_MS);
  } else {
    Serial.printf("[CSI] ping 시작 실패: %s\n", esp_err_to_name(err));
    if (csiPingHandle) {
      esp_ping_delete_session(csiPingHandle);
      csiPingHandle = nullptr;
    }
  }
}

void stopRouterPing() {
  if (!csiPingHandle) {
    return;
  }
  esp_ping_stop(csiPingHandle);
  esp_ping_delete_session(csiPingHandle);
  csiPingHandle = nullptr;
  csiPingStartedAt = 0;
}

// ─────────────────────────────
// CSI 시작/정지
// ─────────────────────────────
void startCSI() {
  if (csiRunning || WiFi.status() != WL_CONNECTED) {
    return;
  }

  wifi_ap_record_t apInfo = {};
  esp_err_t err = esp_wifi_sta_get_ap_info(&apInfo);
  if (err != ESP_OK) {
    Serial.printf("[CSI] 공유기 정보 조회 실패: %s\n", esp_err_to_name(err));
    return;
  }

  memcpy(routerBssid, apInfo.bssid, sizeof(routerBssid));
  routerBssidValid = true;

  // 기존 ESP32/ESP32-S2/S3/C3의 non-HE CSI 설정 구조체입니다.
  // LLTF는 일반 802.11 공유기와의 호환성이 가장 높습니다.
  wifi_csi_config_t csiConfig = {};
  csiConfig.lltf_en = true;
  csiConfig.htltf_en = false;
  csiConfig.stbc_htltf2_en = false;
  csiConfig.ltf_merge_en = true;
  csiConfig.channel_filter_en = true;
  csiConfig.manu_scale = false;
  csiConfig.shift = 0;
  csiConfig.dump_ack_en = false;

  // 연결된 채널에서 수신 프레임을 더 많이 확보하되 콜백에서 BSSID로 필터링합니다.
  err = esp_wifi_set_promiscuous(true);
  if (err == ESP_OK) err = esp_wifi_set_csi_rx_cb(csiRxCallback, nullptr);
  if (err == ESP_OK) err = esp_wifi_set_csi_config(&csiConfig);
  if (err == ESP_OK) err = esp_wifi_set_csi(true);

  if (err != ESP_OK) {
    Serial.printf("[CSI] 초기화 실패: %s\n", esp_err_to_name(err));
    esp_wifi_set_csi(false);
    esp_wifi_set_csi_rx_cb(nullptr, nullptr);
    esp_wifi_set_promiscuous(false);
    routerBssidValid = false;
    return;
  }

  csiRunning = true;
  lastCsiHealthCheckAt = millis();
  lastCsiHealthPacketCount = csiPacketCount;
  Serial.printf(
    "[CSI] 시작: sensor_id=%s, router=%02X:%02X:%02X:%02X:%02X:%02X, channel=%u\n",
    SENSOR_ID,
    routerBssid[0], routerBssid[1], routerBssid[2],
    routerBssid[3], routerBssid[4], routerBssid[5],
    apInfo.primary
  );
  startRouterPing();
}

void stopCSI() {
  if (!csiRunning) {
    return;
  }

  stopRouterPing();
  esp_wifi_set_csi(false);
  esp_wifi_set_csi_rx_cb(nullptr, nullptr);
  esp_wifi_set_promiscuous(false);
  routerBssidValid = false;
  csiRunning = false;

  if (csiQueue) {
    xQueueReset(csiQueue);
  }
  Serial.println("[CSI] 정지 - Wi-Fi 재연결 후 자동으로 다시 시작합니다.");
}

// ─────────────────────────────
// Raspberry Pi 고정 핫스팟 자동 연결
// ─────────────────────────────
bool connectWiFi() {
  stopCSI();
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false); // 연속 CSI 수집 시 절전으로 인한 패킷 공백 방지
  WiFi.disconnect(false, false);
  delay(100);

  Serial.printf("[WiFi] Raspberry Pi 핫스팟 연결 중: %s\n", PIUDA_WIFI_SSID);
  WiFi.begin(PIUDA_WIFI_SSID, PIUDA_WIFI_PASSWORD);
  const unsigned long startedAt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startedAt < WIFI_RETRY_TIMEOUT_MS) {
    delay(250);
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[WiFi] PIUDA-CNU 연결 실패(status=%d) - 주변 2.4GHz AP를 확인합니다.\n",
                  (int)WiFi.status());
    const int count = WiFi.scanNetworks(false, true);
    bool targetVisible = false;
    for (int i = 0; i < count; ++i) {
      if (WiFi.SSID(i) == PIUDA_WIFI_SSID) targetVisible = true;
      Serial.printf("[WiFi] scan ssid=%s rssi=%d channel=%d auth=%d\n",
                    WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i),
                    (int)WiFi.encryptionType(i));
    }
    WiFi.scanDelete();
    Serial.printf("[WiFi] target_visible=%s - 5초 뒤 다시 시도합니다.\n",
                  targetVisible ? "yes" : "no");
    return false;
  }

  serverIP = PIUDA_SERVER_HOST;
  serverUrl = "http://" + serverIP + ":" + String(PIUDA_SERVER_PORT) + "/api/v1/module-readings";
  wifiLostTime = 0;
  Serial.printf("[WiFi] 연결 완료: IP=%s, Gateway=%s, Channel=%d, Server=%s\n",
                WiFi.localIP().toString().c_str(),
                WiFi.gatewayIP().toString().c_str(),
                WiFi.channel(),
                serverUrl.c_str());
  startCSI();
  return true;
}

// ─────────────────────────────
// MLX90614 측정
// HAS_IR_SENSOR=false이면 mlx.begin/read/error 검사를 실행하지 않습니다.
// ─────────────────────────────
bool readTemperatures(float &ambient, float &object) {
  ambient = NAN;
  object = NAN;

  if (!HAS_IR_SENSOR || !irReady) {
    return false;
  }

  ambient = mlx.readAmbientTempC();
  object = mlx.readObjectTempC();
  if (isnan(ambient) || isnan(object)) {
    Serial.println("[IR] 온도 읽기 실패");
    ambient = NAN;
    object = NAN;
    return false;
  }
  return true;
}

// ─────────────────────────────
// Flask 서버 전송 Queue
// 센서 loop는 HTTP를 직접 실행하지 않고 snapshot만 Queue에 넣습니다.
// ─────────────────────────────
void sendData(int pir, const String &reason) {
  if (!uploadQueue) return;
  ModuleReading reading = {};
  reading.pir = pir;
  reading.hasTemperature = readTemperatures(reading.ambient, reading.object);
  strlcpy(reading.reason, reason.c_str(), sizeof(reading.reason));
  portENTER_CRITICAL(&csiSummaryMux);
  reading.csi = latestCsi;
  portEXIT_CRITICAL(&csiSummaryMux);

  if (xQueueSend(uploadQueue, &reading, 0) != pdTRUE) {
    ModuleReading discarded;
    xQueueReceive(uploadQueue, &discarded, 0);
    xQueueSend(uploadQueue, &reading, 0);
  }
}

void moduleUploadTask(void *parameter) {
  ModuleReading reading;
  for (;;) {
    if (xQueueReceive(uploadQueue, &reading, portMAX_DELAY) != pdTRUE) continue;
    if (WiFi.status() != WL_CONNECTED || serverIP.length() == 0) continue;

    String payload;
    payload.reserve(384);
    payload += "{\"sensor_id\":\"" SENSOR_ID "\"";
    payload += ",\"has_ir_sensor\":";
    payload += HAS_IR_SENSOR ? "true" : "false";
    payload += ",\"ambient\":";
    payload += reading.hasTemperature ? String(reading.ambient, 2) : "null";
    payload += ",\"object\":";
    payload += reading.hasTemperature ? String(reading.object, 2) : "null";
    payload += ",\"pir\":" + String(reading.pir);
    payload += ",\"reason\":\"" + String(reading.reason) + "\"";
    payload += ",\"csi\":{";
    payload += "\"packet_count\":" + String(reading.csi.packetCount);
    payload += ",\"packet_rate\":" + String(reading.csi.packetRate, 2);
    payload += ",\"rssi\":" + String(reading.csi.rssi);
    payload += ",\"length\":" + String(reading.csi.length);
    payload += ",\"mean_amplitude\":" + String(reading.csi.meanAmplitude, 2);
    payload += ",\"amplitude_stddev\":" + String(reading.csi.amplitudeStddev, 2);
    payload += ",\"peak_delta\":" + String(reading.csi.peakDelta, 2);
    payload += ",\"dropped_count\":" + String(reading.csi.droppedCount) + "}}";

    int code = -1;
    bool accepted = false;
    for (int attempt = 0; attempt < 3 && WiFi.status() == WL_CONNECTED; ++attempt) {
      HTTPClient http;
      http.setTimeout(HTTP_TIMEOUT_MS);
      if (!http.begin(serverUrl)) {
        code = -1;
      } else {
        http.addHeader("Content-Type", "application/json");
        http.addHeader("X-Piuda-Sensor-Key", PIUDA_SENSOR_KEY);
        code = http.POST(payload);
        http.end();
      }
      if (code >= 200 && code < 300) {
        accepted = true;
        break;
      }
      // 인증/페이로드 오류는 반복해도 같으므로 즉시 중단합니다.
      if (code >= 400 && code < 500) break;
      delay(250UL << attempt);
    }

    if (!accepted) {
      ++httpFailCount;
      Serial.printf("[HTTP] 서버 응답 실패: count=%d code=%d\n", httpFailCount, code);
      if (httpFailCount >= HTTP_FAIL_LIMIT) {
        Serial.println("[HTTP] 서버는 응답하지 않지만 Wi-Fi/CSI 연결은 유지합니다.");
        httpFailCount = 0;
      }
    } else {
      httpFailCount = 0;
      Serial.printf("[HTTP] 통합 측정 전송 성공: sensor_id=%s pir=%d code=%d\n",
                    SENSOR_ID, reading.pir, code);
    }
  }
}

// ─────────────────────────────
// 비차단 PIR/IR 측정 및 전송
// ─────────────────────────────
void handleSensors() {
  const unsigned long now = millis();
  if (now < PIR_STABILIZE_MS || now - lastPirSampleAt < PIR_SAMPLE_INTERVAL_MS) {
    return;
  }
  lastPirSampleAt = now;

  const int current = digitalRead(PIR_PIN) ? HIGH : LOW;

  if (!pirReady) {
    pirReady = true;
    pirStableState = current;
    pirCandidateState = current;
    pirCandidateSince = now;
    digitalWrite(LED_PIN, pirStableState);
    Serial.printf("[PIR] 초기 상태: %s\n", pirStableState ? "움직임 있음" : "없음");
    sendData(pirStableState, "FIRST_BOOT");
    lastSensorReportAt = now;
    return;
  }

  if (current != pirCandidateState) {
    pirCandidateState = current;
    pirCandidateSince = now;
  }

  if (pirCandidateState != pirStableState && now - pirCandidateSince >= PIR_DEBOUNCE_MS) {
    pirStableState = pirCandidateState;
    digitalWrite(LED_PIN, pirStableState);
    Serial.printf("[PIR] 상태 변경: %s\n", pirStableState ? "움직임 있음" : "없음");
    sendData(pirStableState, pirStableState ? "MOTION_START" : "MOTION_END");
    lastSensorReportAt = now;
  }

  if (now - lastSensorReportAt >= SENSOR_REPORT_INTERVAL_MS) {
    sendData(pirStableState, "PERIODIC");
    lastSensorReportAt = now;
  }
}

// ─────────────────────────────
// Wi-Fi/CSI 재연결 관리
// ─────────────────────────────
void maintainWiFiAndCSI() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiLostTime = 0;
    if (!csiRunning) {
      startCSI();
      return;
    }

    const unsigned long now = millis();

    // 일부 공유기는 동일 ICMP stream을 오래 보내면 응답률을 크게 낮춥니다.
    // Wi-Fi/CSI는 유지하고 ping session만 주기적으로 새로 만들어 방지합니다.
    if (csiPingHandle && now - csiPingStartedAt >= CSI_PING_RESTART_MS) {
      Serial.println("[CSI] 연속 트래픽 유지를 위해 ping session을 갱신합니다.");
      stopRouterPing();
      startRouterPing();
    }

    if (now - lastCsiHealthCheckAt >= CSI_HEALTH_CHECK_MS) {
      const uint32_t currentCount = csiPacketCount;
      const uint32_t received = currentCount - lastCsiHealthPacketCount;
      lastCsiHealthPacketCount = currentCount;
      lastCsiHealthCheckAt = now;

      if (received < CSI_MIN_PACKETS_PER_CHECK) {
        wifi_ap_record_t currentAp = {};
        const bool apChanged =
          esp_wifi_sta_get_ap_info(&currentAp) == ESP_OK &&
          memcmp(currentAp.bssid, routerBssid, sizeof(routerBssid)) != 0;

        if (apChanged) {
          Serial.println("[CSI] 공유기 BSSID 변경 감지 - CSI를 다시 연결합니다.");
          stopCSI();
          startCSI();
        } else {
          Serial.printf("[CSI] 수신률 저하(%lu packets/%lus) - ping session을 복구합니다.\n",
                        (unsigned long)received,
                        (unsigned long)(CSI_HEALTH_CHECK_MS / 1000));
          stopRouterPing();
          startRouterPing();
        }
      }
    }
    return;
  }

  if (csiRunning) {
    stopCSI();
  }

  if (wifiLostTime == 0) {
    wifiLostTime = millis();
    Serial.println("[WiFi] 연결 끊김 - 자동 재연결을 기다립니다.");
    WiFi.reconnect();
  } else if (millis() - wifiLostTime >= WIFI_RETRY_TIMEOUT_MS) {
    Serial.println("[WiFi] 장시간 재연결 실패 - PIUDA-CNU에 다시 연결합니다.");
    wifiLostTime = 0;
    connectWiFi();
  }
}

// ─────────────────────────────
// 메인
// ─────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(PIR_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  Serial.println("\n========================================");
  Serial.printf("  ESP32 Sensor + CSI (%s)\n", SENSOR_ID);
  Serial.printf("  PIR=ON, IR=%s, Deep Sleep=OFF\n", HAS_IR_SENSOR ? "ON" : "OFF");
  Serial.println("========================================");

  if (HAS_IR_SENSOR) {
    Wire.begin(SDA_PIN, SCL_PIN);
    irReady = mlx.begin();
    Serial.printf("[IR] MLX90614 초기화: %s\n", irReady ? "성공" : "실패");
  } else {
    // MLX90614 초기화, 읽기, 오류 검사를 전혀 실행하지 않습니다.
    irReady = false;
    Serial.println("[IR] HAS_IR_SENSOR=false - IR 기능 비활성화");
  }

  csiQueue = xQueueCreate(CSI_QUEUE_DEPTH, sizeof(CsiFrame));
  uploadQueue = xQueueCreate(UPLOAD_QUEUE_DEPTH, sizeof(ModuleReading));
  if (!csiQueue || !uploadQueue) {
    Serial.println("[CSI] Queue 생성 실패. 재시작합니다.");
    delay(1000);
    ESP.restart();
  }

  if (xTaskCreate(
        csiProcessingTask,
        "csi_processor",
        4096,
        nullptr,
        1,
        &csiTaskHandle
      ) != pdPASS) {
    Serial.println("[CSI] 처리 task 생성 실패. 재시작합니다.");
    delay(1000);
    ESP.restart();
  }

  if (xTaskCreate(
        moduleUploadTask,
        "module_upload",
        6144,
        nullptr,
        1,
        &uploadTaskHandle
      ) != pdPASS) {
    Serial.println("[HTTP] 전송 task 생성 실패. 재시작합니다.");
    delay(1000);
    ESP.restart();
  }

  while (!connectWiFi()) {
    delay(5000);
  }
  Serial.println("[System] 연속 시연 모드 시작 - Deep Sleep을 사용하지 않습니다.");
}

void loop() {
  maintainWiFiAndCSI();
  handleSensors();

  // CSI callback/task와 Wi-Fi 시스템 task가 실행될 시간을 양보합니다.
  delay(10);
}
