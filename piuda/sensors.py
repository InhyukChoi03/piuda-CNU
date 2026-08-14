from __future__ import annotations

from datetime import timedelta

from flask import current_app

from .clock import iso, now, parse_iso
from .db import get_db


CSI_CALIBRATION_SAMPLES = 30
HEARTBEAT_INTERVAL = timedelta(minutes=1)
FALL_EVENT_COOLDOWN = timedelta(seconds=30)


def _latest_event_time(database, device_id: int, event_type: str):
    row = database.execute(
        """
        SELECT occurred_at FROM sensor_events
        WHERE device_id=? AND event_type=?
        ORDER BY id DESC LIMIT 1
        """,
        (device_id, event_type),
    ).fetchone()
    return parse_iso(row["occurred_at"]) if row else None


def _record_event(
    database,
    device_id: int,
    event_type: str,
    value: float | None,
    confidence: float | None,
    timestamp: str,
    details: str,
) -> None:
    database.execute(
        """
        INSERT INTO sensor_events(
          device_id, event_type, value, confidence, occurred_at, received_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (device_id, event_type, value, confidence, timestamp, timestamp, details),
    )


def _classify_csi(previous, csi: dict) -> dict:
    packet_rate = csi["packet_rate"]
    length = csi["length"]
    feature = csi["peak_delta"]
    samples = int(previous["csi_samples"]) if previous else 0
    baseline = float(previous["csi_baseline"]) if previous else 0.0
    deviation = float(previous["csi_deviation"]) if previous else 0.0

    if packet_rate <= 0 or length <= 0:
        return {
            "status": "unavailable",
            "score": 0.0,
            "samples": samples,
            "baseline": baseline,
            "deviation": deviation,
        }

    if samples < CSI_CALIBRATION_SAMPLES:
        new_samples = samples + 1
        new_baseline = baseline + (feature - baseline) / new_samples
        absolute_error = abs(feature - new_baseline)
        new_deviation = deviation + (absolute_error - deviation) / new_samples
        return {
            "status": "calibrating",
            "score": min(1.0, new_samples / CSI_CALIBRATION_SAMPLES),
            "samples": new_samples,
            "baseline": new_baseline,
            "deviation": new_deviation,
        }

    spread = max(deviation, 1.5)
    motion_threshold = max(
        float(current_app.config["CSI_MOTION_THRESHOLD"]), baseline + spread * 4,
    )
    strong_threshold = max(
        float(current_app.config["CSI_STRONG_THRESHOLD"]),
        baseline + spread * 8,
        motion_threshold + 5,
    )
    score = max(0.0, min(1.0, (feature - baseline) / max(strong_threshold - baseline, 1)))
    if feature >= strong_threshold and csi["amplitude_stddev"] >= 1:
        status = "strong_change"
    elif feature >= motion_threshold:
        status = "motion"
    else:
        status = "stable"

    # 움직임이 없는 구간만 천천히 기준선에 반영해 큰 변화가 기준선을
    # 끌어올려 다음 변화를 숨기는 일을 막습니다.
    if status == "stable":
        alpha = 0.03
        baseline = baseline * (1 - alpha) + feature * alpha
        deviation = deviation * (1 - alpha) + abs(feature - baseline) * alpha

    return {
        "status": status,
        "score": score,
        "samples": samples + 1,
        "baseline": baseline,
        "deviation": deviation,
    }


def ingest_module_reading(device, reading: dict) -> dict:
    """Store one ESP32 snapshot and emit only meaningful transition events."""
    database = get_db()
    device_id = int(device["id"])
    current = now()
    timestamp = iso(current)
    previous = database.execute(
        "SELECT * FROM sensor_module_state WHERE device_id=?", (device_id,)
    ).fetchone()
    classification = _classify_csi(previous, reading["csi"])

    database.execute(
        """
        INSERT INTO sensor_module_state(
          device_id, has_ir_sensor, ambient_c, object_c, pir_state, reason,
          csi_packet_count, csi_packet_rate, csi_rssi, csi_length,
          csi_mean_amplitude, csi_amplitude_stddev, csi_peak_delta,
          csi_dropped_count, csi_baseline, csi_deviation, csi_samples,
          csi_score, csi_status, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
          has_ir_sensor=excluded.has_ir_sensor,
          ambient_c=excluded.ambient_c,
          object_c=excluded.object_c,
          pir_state=excluded.pir_state,
          reason=excluded.reason,
          csi_packet_count=excluded.csi_packet_count,
          csi_packet_rate=excluded.csi_packet_rate,
          csi_rssi=excluded.csi_rssi,
          csi_length=excluded.csi_length,
          csi_mean_amplitude=excluded.csi_mean_amplitude,
          csi_amplitude_stddev=excluded.csi_amplitude_stddev,
          csi_peak_delta=excluded.csi_peak_delta,
          csi_dropped_count=excluded.csi_dropped_count,
          csi_baseline=excluded.csi_baseline,
          csi_deviation=excluded.csi_deviation,
          csi_samples=excluded.csi_samples,
          csi_score=excluded.csi_score,
          csi_status=excluded.csi_status,
          received_at=excluded.received_at
        """,
        (
            device_id,
            int(reading["has_ir_sensor"]),
            reading["ambient"],
            reading["object"],
            reading["pir"],
            reading["reason"],
            reading["csi"]["packet_count"],
            reading["csi"]["packet_rate"],
            reading["csi"]["rssi"],
            reading["csi"]["length"],
            reading["csi"]["mean_amplitude"],
            reading["csi"]["amplitude_stddev"],
            reading["csi"]["peak_delta"],
            reading["csi"]["dropped_count"],
            classification["baseline"],
            classification["deviation"],
            classification["samples"],
            classification["score"],
            classification["status"],
            timestamp,
        ),
    )
    database.execute("UPDATE sensor_devices SET last_seen_at=? WHERE id=?", (timestamp, device_id))

    emitted: list[str] = []
    previous_pir = int(previous["pir_state"]) if previous else None
    if previous_pir is None or previous_pir != reading["pir"]:
        event_type = "pir_motion" if reading["pir"] else "pir_idle"
        _record_event(
            database,
            device_id,
            event_type,
            float(reading["pir"]),
            1.0,
            timestamp,
            '{"source":"module_reading"}',
        )
        emitted.append(event_type)

    latest_heartbeat = _latest_event_time(database, device_id, "heartbeat")
    if latest_heartbeat is None or current - latest_heartbeat >= HEARTBEAT_INTERVAL:
        _record_event(
            database,
            device_id,
            "heartbeat",
            reading["csi"]["packet_rate"],
            1.0,
            timestamp,
            '{"source":"module_reading"}',
        )
        emitted.append("heartbeat")

    previous_status = previous["csi_status"] if previous else None
    status = classification["status"]
    if status == "motion" and previous_status not in {"motion", "strong_change"}:
        confidence = max(0.55, min(0.94, classification["score"]))
        _record_event(
            database,
            device_id,
            "csi_motion",
            reading["csi"]["peak_delta"],
            confidence,
            timestamp,
            '{"source":"module_reading","classification":"motion"}',
        )
        emitted.append("csi_motion")

    latest_fall = _latest_event_time(database, device_id, "csi_fall")
    fall_ready = latest_fall is None or current - latest_fall >= FALL_EVENT_COOLDOWN
    # 강한 CSI 변화만으로 낙상을 확정하지 않습니다. PIR도 동시에 켜진 경우에만
    # 보호자 확인용 '낙상 의심' 이벤트를 만들며 UI도 후보 신호로 표시합니다.
    if status == "strong_change" and reading["pir"] == 1 and fall_ready:
        confidence = max(0.65, min(0.95, classification["score"]))
        _record_event(
            database,
            device_id,
            "csi_fall",
            reading["csi"]["peak_delta"],
            confidence,
            timestamp,
            '{"source":"module_reading","classification":"strong_change","pir_correlated":true}',
        )
        emitted.append("csi_fall")

    database.commit()
    return {
        "csi_status": status,
        "csi_score": round(float(classification["score"]), 3),
        "calibration_samples": min(int(classification["samples"]), CSI_CALIBRATION_SAMPLES),
        "calibration_required": CSI_CALIBRATION_SAMPLES,
        "events": emitted,
    }
