PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO schema_meta(key, value) VALUES ('schema_version', '6')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    user_name TEXT NOT NULL DEFAULT '사용자',
    birth_year INTEGER,
    caregiver_name TEXT NOT NULL DEFAULT '보호자',
    caregiver_phone TEXT,
    locale TEXT NOT NULL DEFAULT 'ko-KR',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS caregivers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('caregiver')),
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('meal','medication','cleaning','sleep','outing','hospital','other')),
    scheduled_time TEXT NOT NULL,
    days_mask INTEGER NOT NULL DEFAULT 127 CHECK (days_mask BETWEEN 1 AND 127),
    instructions TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id INTEGER NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
    due_at TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','missed','skipped')),
    completed_at TEXT,
    note TEXT,
    UNIQUE(routine_id, due_at)
);

CREATE TABLE IF NOT EXISTS sensor_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    api_key_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS sensor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES sensor_devices(id) ON DELETE CASCADE,
    event_id TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN ('pir_motion','pir_idle','csi_motion','csi_fall','heartbeat')),
    value REAL,
    confidence REAL,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sensor_events_time ON sensor_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_sensor_events_type_time ON sensor_events(event_type, occurred_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sensor_events_device_event
ON sensor_events(device_id, event_id) WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_task_occurrences_date ON task_occurrences(due_date, status);

-- ESP32 통합 모듈은 1초마다 PIR·비접촉 온도·CSI 요약을 보냅니다.
-- 원시 측정값을 무한히 쌓지 않고 기기별 최신 상태와 교정 기준만 보관합니다.
-- 의미 있는 움직임/낙상 후보만 sensor_events에 별도로 기록합니다.
CREATE TABLE IF NOT EXISTS sensor_module_state (
    device_id INTEGER PRIMARY KEY REFERENCES sensor_devices(id) ON DELETE CASCADE,
    has_ir_sensor INTEGER NOT NULL DEFAULT 0 CHECK (has_ir_sensor IN (0,1)),
    ambient_c REAL,
    object_c REAL,
    pir_state INTEGER NOT NULL DEFAULT 0 CHECK (pir_state IN (0,1)),
    reason TEXT NOT NULL DEFAULT 'PERIODIC',
    csi_packet_count INTEGER NOT NULL DEFAULT 0,
    csi_packet_rate REAL NOT NULL DEFAULT 0,
    csi_rssi INTEGER NOT NULL DEFAULT 0,
    csi_length INTEGER NOT NULL DEFAULT 0,
    csi_mean_amplitude REAL NOT NULL DEFAULT 0,
    csi_amplitude_stddev REAL NOT NULL DEFAULT 0,
    csi_peak_delta REAL NOT NULL DEFAULT 0,
    csi_dropped_count INTEGER NOT NULL DEFAULT 0,
    csi_baseline REAL NOT NULL DEFAULT 0,
    csi_deviation REAL NOT NULL DEFAULT 0,
    csi_samples INTEGER NOT NULL DEFAULT 0,
    csi_score REAL NOT NULL DEFAULT 0,
    csi_status TEXT NOT NULL DEFAULT 'calibrating'
        CHECK (csi_status IN ('unavailable','calibrating','stable','motion','strong_change')),
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    level TEXT NOT NULL CHECK (level IN ('normal','caution','danger','emergency')),
    factors_json TEXT NOT NULL,
    assessed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_risk_assessments_time ON risk_assessments(assessed_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_assessment_id INTEGER REFERENCES risk_assessments(id) ON DELETE SET NULL,
    level TEXT NOT NULL CHECK (level IN ('info','caution','danger','emergency')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(created_at DESC);

CREATE TABLE IF NOT EXISTS feedback_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    scenario_key TEXT NOT NULL,
    scenario_title TEXT NOT NULL,
    description TEXT NOT NULL,
    risk_score INTEGER NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
    risk_level TEXT NOT NULL CHECK (risk_level IN ('normal','caution','danger','emergency')),
    factors_json TEXT NOT NULL DEFAULT '[]',
    user_message TEXT NOT NULL,
    activated_at TEXT NOT NULL
);
