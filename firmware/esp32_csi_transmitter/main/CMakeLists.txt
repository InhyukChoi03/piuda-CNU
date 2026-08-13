idf_component_register(
    SRCS "piuda_csi_transmitter.c"
    INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_event esp_timer nvs_flash
)
