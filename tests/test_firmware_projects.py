from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECEIVER = ROOT / "firmware" / "esp32_sensor"
TRANSMITTER = ROOT / "firmware" / "esp32_csi_transmitter"
DEFAULT_CSI_SENDER_MAC = "1a:50:49:55:44:41"


def test_csi_sender_and_receiver_projects_are_complete():
    required = (
        RECEIVER / "CMakeLists.txt",
        RECEIVER / "main" / "CMakeLists.txt",
        RECEIVER / "main" / "Kconfig.projbuild",
        RECEIVER / "main" / "piuda_sensor.c",
        TRANSMITTER / "CMakeLists.txt",
        TRANSMITTER / "main" / "CMakeLists.txt",
        TRANSMITTER / "main" / "Kconfig.projbuild",
        TRANSMITTER / "main" / "piuda_csi_transmitter.c",
    )
    assert all(path.is_file() for path in required)


def test_csi_projects_share_a_sender_identity_and_controlled_packet_source():
    receiver_config = (RECEIVER / "main" / "Kconfig.projbuild").read_text()
    transmitter_config = (TRANSMITTER / "main" / "Kconfig.projbuild").read_text()
    receiver_source = (RECEIVER / "main" / "piuda_sensor.c").read_text()
    transmitter_source = (
        TRANSMITTER / "main" / "piuda_csi_transmitter.c"
    ).read_text()

    assert DEFAULT_CSI_SENDER_MAC in receiver_config
    assert DEFAULT_CSI_SENDER_MAC in transmitter_config
    assert "memcmp(info->mac, csi_sender_mac" in receiver_source
    assert "esp_wifi_set_promiscuous(true)" in receiver_source
    assert 'xTaskCreate(pir_task, "pir"' in receiver_source
    assert 'xTaskCreate(csi_analysis_task, "csi-analysis"' in receiver_source
    assert "esp_wifi_set_mac(WIFI_IF_STA, station_mac)" in transmitter_source
    assert "esp_now_send(" in transmitter_source
    assert "CONFIG_PIUDA_TX_PACKETS_PER_SECOND" in transmitter_source
