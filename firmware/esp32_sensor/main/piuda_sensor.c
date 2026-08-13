#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#define WIFI_CONNECTED_BIT BIT0
#define EVENT_QUEUE_LENGTH 16
#define EVENT_TYPE_LENGTH 16
#define EVENT_ID_LENGTH 33
#define CSI_SAMPLE_QUEUE_LENGTH 64
#define CSI_LOG_INTERVAL_US 1000000
#define HTTP_MAX_ATTEMPTS 5
#define HTTP_RETRY_INITIAL_MS 500
#define HTTP_RETRY_MAX_MS 8000

static const char *TAG = "piuda-sensor";
static EventGroupHandle_t wifi_events;
static QueueHandle_t sensor_events;
static QueueHandle_t csi_sample_queue;
static uint8_t csi_sender_mac[6];
static volatile uint32_t csi_dropped_samples;
static int64_t last_motion_us;
static int64_t last_fall_us;

typedef struct {
    char id[EVENT_ID_LENGTH];
    char type[EVENT_TYPE_LENGTH];
    float value;
    float confidence;
} piuda_event_t;

typedef enum {
    EVENT_POST_SUCCESS,
    EVENT_POST_CONFIG_ERROR,
    EVENT_POST_RETRYABLE,
} event_post_outcome_t;

typedef struct {
    event_post_outcome_t outcome;
    esp_err_t transport_error;
    int http_status;
} event_post_result_t;

typedef struct {
    float magnitude;
    int8_t rssi;
    int64_t received_us;
} csi_sample_t;

static bool parse_mac(const char *text, uint8_t output[6])
{
    unsigned int values[6];
    int consumed = 0;
    if (sscanf(
            text,
            "%2x:%2x:%2x:%2x:%2x:%2x%n",
            &values[0],
            &values[1],
            &values[2],
            &values[3],
            &values[4],
            &values[5],
            &consumed
        ) != 6 || text[consumed] != '\0') {
        return false;
    }
    for (int index = 0; index < 6; index++) {
        if (values[index] > UINT8_MAX) return false;
        output[index] = (uint8_t)values[index];
    }
    return true;
}

static void enqueue_event(const char *type, float value, float confidence)
{
    piuda_event_t event = {.value = value, .confidence = confidence};
    snprintf(
        event.id,
        sizeof(event.id),
        "%08lx%08lx%08lx%08lx",
        (unsigned long)esp_random(),
        (unsigned long)esp_random(),
        (unsigned long)esp_random(),
        (unsigned long)esp_random()
    );
    strlcpy(event.type, type, sizeof(event.type));
    if (xQueueSend(sensor_events, &event, 0) != pdTRUE) {
        ESP_LOGW(TAG, "event queue full: %s", type);
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(wifi_events, WIFI_CONNECTED_BIT);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(wifi_events, WIFI_CONNECTED_BIT);
        ESP_LOGI(TAG, "Wi-Fi connected");
    }
}

static void initialise_wifi(void)
{
    wifi_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));

    wifi_config_t wifi_config = {0};
    strlcpy((char *)wifi_config.sta.ssid, CONFIG_PIUDA_WIFI_SSID, sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password, CONFIG_PIUDA_WIFI_PASSWORD, sizeof(wifi_config.sta.password));
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.pmf_cfg.capable = true;
    wifi_config.sta.pmf_cfg.required = false;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

static void csi_receive_callback(void *ctx, wifi_csi_info_t *info)
{
    if (info == NULL || info->buf == NULL || info->len < 16) {
        return;
    }
    if (memcmp(info->mac, csi_sender_mac, sizeof(csi_sender_mac)) != 0) {
        return;
    }

    float power_sum = 0.0f;
    int pairs = 0;
    for (int i = 0; i + 1 < info->len; i += 2) {
        int8_t imaginary = info->buf[i];
        int8_t real = info->buf[i + 1];
        power_sum += (float)(real * real + imaginary * imaginary);
        pairs++;
    }
    if (pairs == 0) return;

    csi_sample_t sample = {
        .magnitude = sqrtf(power_sum / pairs),
        .rssi = info->rx_ctrl.rssi,
        .received_us = esp_timer_get_time(),
    };
    if (xQueueSend(csi_sample_queue, &sample, 0) != pdTRUE) {
        csi_dropped_samples++;
    }
}

static void csi_analysis_task(void *arg)
{
    csi_sample_t sample;
    float baseline = 0.0f;
    float delta_ema = 0.0f;
    uint32_t calibration_count = 0;
    uint32_t received_count = 0;
    int motion_streak = 0;
    int fall_streak = 0;
    int64_t last_log_us = 0;

    ESP_LOGI(
        TAG,
        "CSI calibration: keep the sensing area still for %d packets",
        CONFIG_PIUDA_CSI_CALIBRATION_PACKETS
    );
    while (true) {
        if (xQueueReceive(csi_sample_queue, &sample, pdMS_TO_TICKS(2000)) != pdTRUE) {
            ESP_LOGW(TAG, "CSI sender packets not received; check sender, channel, and sender MAC");
            continue;
        }
        received_count++;

        if (calibration_count < CONFIG_PIUDA_CSI_CALIBRATION_PACKETS) {
            baseline = calibration_count == 0
                ? sample.magnitude
                : baseline * 0.98f + sample.magnitude * 0.02f;
            calibration_count++;
            if (calibration_count == CONFIG_PIUDA_CSI_CALIBRATION_PACKETS) {
                ESP_LOGI(TAG, "CSI baseline ready: magnitude=%.2f rssi=%d", baseline, sample.rssi);
                last_log_us = sample.received_us;
            }
            continue;
        }

        float delta_percent = fabsf(sample.magnitude - baseline) / fmaxf(baseline, 1.0f) * 100.0f;
        delta_ema = delta_ema == 0.0f ? delta_percent : delta_ema * 0.75f + delta_percent * 0.25f;

        // Do not teach active movement into the baseline. Only slow drift while the
        // link is quiet is absorbed.
        if (delta_ema < CONFIG_PIUDA_CSI_MOTION_DELTA) {
            baseline = baseline * 0.997f + sample.magnitude * 0.003f;
        }

        fall_streak = delta_ema >= CONFIG_PIUDA_CSI_FALL_DELTA ? fall_streak + 1 : 0;
        motion_streak = delta_ema >= CONFIG_PIUDA_CSI_MOTION_DELTA ? motion_streak + 1 : 0;

        if (fall_streak >= CONFIG_PIUDA_CSI_FALL_CONSECUTIVE &&
            sample.received_us - last_fall_us > 15000000) {
            float confidence = fminf(
                1.0f,
                delta_ema / (CONFIG_PIUDA_CSI_FALL_DELTA * 1.5f)
            );
            enqueue_event("csi_fall", delta_ema, confidence);
            ESP_LOGW(TAG, "CSI fall candidate: delta=%.1f%% confidence=%.2f", delta_ema, confidence);
            last_fall_us = sample.received_us;
            fall_streak = 0;
            motion_streak = 0;
        } else if (motion_streak >= CONFIG_PIUDA_CSI_MOTION_CONSECUTIVE &&
                   sample.received_us - last_motion_us > 3000000) {
            float confidence = fminf(
                1.0f,
                delta_ema / (CONFIG_PIUDA_CSI_MOTION_DELTA * 2.0f)
            );
            enqueue_event("csi_motion", delta_ema, confidence);
            ESP_LOGI(TAG, "CSI motion: delta=%.1f%% confidence=%.2f", delta_ema, confidence);
            last_motion_us = sample.received_us;
            motion_streak = 0;
        }

        if (sample.received_us - last_log_us >= CSI_LOG_INTERVAL_US) {
            UBaseType_t queued = uxQueueMessagesWaiting(csi_sample_queue);
            ESP_LOGI(
                TAG,
                "CSI live: frames=%lu rssi=%d magnitude=%.2f baseline=%.2f delta=%.1f%% queued=%u drops=%lu",
                (unsigned long)received_count,
                sample.rssi,
                sample.magnitude,
                baseline,
                delta_ema,
                (unsigned int)queued,
                (unsigned long)csi_dropped_samples
            );
            last_log_us = sample.received_us;
        }
    }
}

static void initialise_esp_now_receiver(void)
{
    const uint8_t broadcast_mac[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    esp_now_peer_info_t peer = {
        .channel = 0,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
    };
    memcpy(peer.peer_addr, broadcast_mac, sizeof(broadcast_mac));
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
}

static void initialise_csi(void)
{
    wifi_csi_config_t config = {
        .lltf_en = true,
        .htltf_en = true,
        .stbc_htltf2_en = true,
        .ltf_merge_en = true,
        .channel_filter_en = true,
        .manu_scale = false,
        .shift = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_receive_callback, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

static void pir_task(void *arg)
{
    gpio_config_t config = {
        .pin_bit_mask = 1ULL << CONFIG_PIUDA_PIR_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&config));
    int previous = 0;
    while (true) {
        int current = gpio_get_level(CONFIG_PIUDA_PIR_GPIO);
        if (current && !previous) {
            enqueue_event("pir_motion", 1.0f, 1.0f);
        }
        previous = current;
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}

static event_post_result_t post_event_once(const piuda_event_t *event)
{
    event_post_result_t post_result = {
        .outcome = EVENT_POST_RETRYABLE,
        .transport_error = ESP_FAIL,
        .http_status = 0,
    };
    char url[256];
    int url_length = snprintf(url, sizeof(url), "%s/api/v1/sensor-events", CONFIG_PIUDA_SERVER_URL);
    if (url_length < 0 || url_length >= (int)sizeof(url)) {
        post_result.outcome = EVENT_POST_CONFIG_ERROR;
        post_result.transport_error = ESP_ERR_INVALID_SIZE;
        return post_result;
    }

    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        post_result.transport_error = ESP_ERR_NO_MEM;
        return post_result;
    }
    bool json_ready =
        cJSON_AddStringToObject(root, "device_uid", CONFIG_PIUDA_DEVICE_UID) != NULL &&
        cJSON_AddStringToObject(root, "event_id", event->id) != NULL &&
        cJSON_AddStringToObject(root, "event_type", event->type) != NULL &&
        cJSON_AddNumberToObject(root, "value", event->value) != NULL &&
        cJSON_AddNumberToObject(root, "confidence", event->confidence) != NULL;
    cJSON *details = cJSON_AddObjectToObject(root, "details");
    json_ready = json_ready && details != NULL &&
        cJSON_AddStringToObject(details, "location", CONFIG_PIUDA_LOCATION) != NULL;
    if (!json_ready) {
        cJSON_Delete(root);
        post_result.transport_error = ESP_ERR_NO_MEM;
        return post_result;
    }

    char *body = cJSON_PrintUnformatted(root);
    if (body == NULL) {
        cJSON_Delete(root);
        post_result.transport_error = ESP_ERR_NO_MEM;
        return post_result;
    }

    esp_http_client_config_t config = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 8000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        cJSON_free(body);
        cJSON_Delete(root);
        post_result.transport_error = ESP_ERR_NO_MEM;
        return post_result;
    }

    esp_err_t setup_result = esp_http_client_set_header(client, "Content-Type", "application/json");
    if (setup_result == ESP_OK) {
        setup_result = esp_http_client_set_header(
            client, "X-Piuda-Sensor-Key", CONFIG_PIUDA_SENSOR_KEY
        );
    }
    if (setup_result == ESP_OK) {
        setup_result = esp_http_client_set_post_field(client, body, (int)strlen(body));
    }
    if (setup_result == ESP_OK) {
        post_result.transport_error = esp_http_client_perform(client);
        if (post_result.transport_error == ESP_OK) {
            post_result.http_status = esp_http_client_get_status_code(client);
            if (post_result.http_status >= 200 && post_result.http_status < 300) {
                post_result.outcome = EVENT_POST_SUCCESS;
            } else if (post_result.http_status >= 400 && post_result.http_status < 500) {
                post_result.outcome = EVENT_POST_CONFIG_ERROR;
            }
        }
    } else {
        post_result.transport_error = setup_result;
    }

    esp_http_client_cleanup(client);
    cJSON_free(body);
    cJSON_Delete(root);
    return post_result;
}

static bool send_event_with_retry(const piuda_event_t *event)
{
    uint32_t backoff_ms = HTTP_RETRY_INITIAL_MS;
    for (int attempt = 1; attempt <= HTTP_MAX_ATTEMPTS; attempt++) {
        xEventGroupWaitBits(
            wifi_events, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY
        );
        event_post_result_t result = post_event_once(event);

        if (result.outcome == EVENT_POST_SUCCESS) {
            ESP_LOGI(
                TAG, "%s accepted: HTTP %d (attempt %d)",
                event->type, result.http_status, attempt
            );
            return true;
        }

        if (result.outcome == EVENT_POST_CONFIG_ERROR) {
            if (result.http_status >= 400 && result.http_status < 500) {
                ESP_LOGE(
                    TAG,
                    "%s rejected: HTTP %d; check server URL, device UID, sensor key, and payload",
                    event->type,
                    result.http_status
                );
            } else {
                ESP_LOGE(
                    TAG, "%s configuration error: %s",
                    event->type, esp_err_to_name(result.transport_error)
                );
            }
            return false;
        }

        if (result.transport_error != ESP_OK) {
            ESP_LOGW(
                TAG, "%s transport failed on attempt %d/%d: %s",
                event->type,
                attempt,
                HTTP_MAX_ATTEMPTS,
                esp_err_to_name(result.transport_error)
            );
        } else {
            ESP_LOGW(
                TAG, "%s server returned HTTP %d on attempt %d/%d",
                event->type,
                result.http_status,
                attempt,
                HTTP_MAX_ATTEMPTS
            );
        }

        if (attempt == HTTP_MAX_ATTEMPTS) {
            ESP_LOGE(
                TAG, "%s delivery abandoned after %d attempts",
                event->type, HTTP_MAX_ATTEMPTS
            );
            return false;
        }

        ESP_LOGW(TAG, "%s retrying in %u ms", event->type, (unsigned int)backoff_ms);
        vTaskDelay(pdMS_TO_TICKS(backoff_ms));
        if (backoff_ms < HTTP_RETRY_MAX_MS) {
            backoff_ms *= 2;
            if (backoff_ms > HTTP_RETRY_MAX_MS) {
                backoff_ms = HTTP_RETRY_MAX_MS;
            }
        }
    }
    return false;
}

static void sender_task(void *arg)
{
    piuda_event_t event;
    TickType_t last_heartbeat = xTaskGetTickCount();
    while (true) {
        if (xQueueReceive(sensor_events, &event, pdMS_TO_TICKS(1000)) == pdTRUE) {
            // Keep the dequeued event in this task until it succeeds, is rejected as
            // a 4xx configuration error, or exhausts the bounded retry budget.
            send_event_with_retry(&event);
        }
        if (xTaskGetTickCount() - last_heartbeat >= pdMS_TO_TICKS(60000)) {
            enqueue_event("heartbeat", 1.0f, 1.0f);
            last_heartbeat = xTaskGetTickCount();
        }
    }
}

void app_main(void)
{
    esp_err_t nvs_result = nvs_flash_init();
    if (nvs_result == ESP_ERR_NVS_NO_FREE_PAGES || nvs_result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_result = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_result);

    if (!parse_mac(CONFIG_PIUDA_CSI_SENDER_MAC, csi_sender_mac)) {
        ESP_LOGE(TAG, "invalid CSI sender MAC: %s", CONFIG_PIUDA_CSI_SENDER_MAC);
        return;
    }
    sensor_events = xQueueCreate(EVENT_QUEUE_LENGTH, sizeof(piuda_event_t));
    csi_sample_queue = xQueueCreate(CSI_SAMPLE_QUEUE_LENGTH, sizeof(csi_sample_t));
    if (sensor_events == NULL || csi_sample_queue == NULL) {
        ESP_LOGE(TAG, "failed to allocate sensor queues");
        return;
    }
    initialise_wifi();
    xEventGroupWaitBits(wifi_events, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
    initialise_esp_now_receiver();
    initialise_csi();
    xTaskCreate(pir_task, "pir", 3072, NULL, 5, NULL);
    xTaskCreate(csi_analysis_task, "csi-analysis", 4096, NULL, 5, NULL);
    xTaskCreate(sender_task, "sender", 6144, NULL, 4, NULL);
    ESP_LOGI(
        TAG,
        "Piuda sensor ready: %s (%s), CSI sender " MACSTR,
        CONFIG_PIUDA_DEVICE_UID,
        CONFIG_PIUDA_LOCATION,
        MAC2STR(csi_sender_mac)
    );
}
