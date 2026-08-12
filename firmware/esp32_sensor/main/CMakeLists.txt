idf_component_register(
    SRCS "piuda_sensor.c"
    INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_event esp_http_client nvs_flash driver json
)
