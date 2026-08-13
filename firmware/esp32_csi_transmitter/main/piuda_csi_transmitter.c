#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#define WIFI_CONNECTED_BIT BIT0
#define CSI_PAYLOAD_MAGIC 0x50495544UL
#define STATUS_LOG_INTERVAL_US 5000000

static const char *TAG = "piuda-csi-tx";
static const uint8_t broadcast_mac[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static EventGroupHandle_t wifi_events;
static uint8_t station_mac[6];

typedef struct {
    uint32_t magic;
    uint32_t sequence;
    int64_t sent_at_us;
    uint8_t padding[48];
} csi_probe_t;

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
    // The address must be unicast. A locally administered address avoids
    // impersonating the factory-programmed hardware MAC.
    return (output[0] & 0x01U) == 0 && (output[0] & 0x02U) != 0;
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
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, station_mac));

    wifi_config_t wifi_config = {0};
    strlcpy((char *)wifi_config.sta.ssid, CONFIG_PIUDA_TX_WIFI_SSID, sizeof(wifi_config.sta.ssid));
    strlcpy(
        (char *)wifi_config.sta.password,
        CONFIG_PIUDA_TX_WIFI_PASSWORD,
        sizeof(wifi_config.sta.password)
    );
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.pmf_cfg.capable = true;
    wifi_config.sta.pmf_cfg.required = false;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

static void initialise_esp_now(void)
{
    esp_now_peer_info_t peer = {
        .channel = 0,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
    };
    memcpy(peer.peer_addr, broadcast_mac, sizeof(broadcast_mac));
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
}

static void csi_transmit_task(void *arg)
{
    csi_probe_t probe = {
        .magic = CSI_PAYLOAD_MAGIC,
        .padding = {0x50, 0x49, 0x55, 0x44, 0x41},
    };
    uint32_t accepted = 0;
    uint32_t rejected = 0;
    int64_t last_log_us = esp_timer_get_time();
    TickType_t interval = pdMS_TO_TICKS(1000 / CONFIG_PIUDA_TX_PACKETS_PER_SECOND);
    if (interval == 0) interval = 1;

    while (true) {
        xEventGroupWaitBits(
            wifi_events,
            WIFI_CONNECTED_BIT,
            pdFALSE,
            pdTRUE,
            portMAX_DELAY
        );
        probe.sequence++;
        probe.sent_at_us = esp_timer_get_time();
        esp_err_t result = esp_now_send(
            broadcast_mac,
            (const uint8_t *)&probe,
            sizeof(probe)
        );
        if (result == ESP_OK) {
            accepted++;
        } else {
            rejected++;
            ESP_LOGW(TAG, "ESP-NOW queue error: %s", esp_err_to_name(result));
        }

        if (probe.sent_at_us - last_log_us >= STATUS_LOG_INTERVAL_US) {
            ESP_LOGI(
                TAG,
                "CSI probes: queued=%lu rejected=%lu latest_sequence=%lu",
                (unsigned long)accepted,
                (unsigned long)rejected,
                (unsigned long)probe.sequence
            );
            last_log_us = probe.sent_at_us;
        }
        vTaskDelay(interval);
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

    if (!parse_mac(CONFIG_PIUDA_TX_STATION_MAC, station_mac)) {
        ESP_LOGE(
            TAG,
            "invalid locally administered unicast station MAC: %s",
            CONFIG_PIUDA_TX_STATION_MAC
        );
        return;
    }

    initialise_wifi();
    xEventGroupWaitBits(wifi_events, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
    initialise_esp_now();

    uint8_t primary_channel = 0;
    wifi_second_chan_t secondary_channel = WIFI_SECOND_CHAN_NONE;
    ESP_ERROR_CHECK(esp_wifi_get_channel(&primary_channel, &secondary_channel));
    ESP_LOGI(
        TAG,
        "CSI transmitter ready: MAC=" MACSTR " channel=%u rate=%d packets/s",
        MAC2STR(station_mac),
        primary_channel,
        CONFIG_PIUDA_TX_PACKETS_PER_SECOND
    );
    xTaskCreate(csi_transmit_task, "csi-transmit", 4096, NULL, 5, NULL);
}
