CREATE TABLE workflow_transitions (
    task_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    record_hash TEXT NOT NULL UNIQUE,
    previous_hash TEXT,
    record_json TEXT NOT NULL,
    PRIMARY KEY (task_id, sequence)
);

CREATE TABLE run_records (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    task_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    record_hash TEXT NOT NULL UNIQUE,
    previous_hash TEXT,
    record_json TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);

CREATE TABLE artifact_manifests (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL
);

CREATE TABLE outbox_events (
    commit_order INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TRIGGER workflow_transitions_no_update
BEFORE UPDATE ON workflow_transitions
BEGIN
    SELECT RAISE(ABORT, 'workflow transitions are append-only');
END;

CREATE TRIGGER workflow_transitions_no_delete
BEFORE DELETE ON workflow_transitions
BEGIN
    SELECT RAISE(ABORT, 'workflow transitions are append-only');
END;

CREATE TRIGGER run_records_no_update
BEFORE UPDATE ON run_records
BEGIN
    SELECT RAISE(ABORT, 'run records are append-only');
END;

CREATE TRIGGER run_records_no_delete
BEFORE DELETE ON run_records
BEGIN
    SELECT RAISE(ABORT, 'run records are append-only');
END;

CREATE TRIGGER artifact_manifests_no_update
BEFORE UPDATE ON artifact_manifests
BEGIN
    SELECT RAISE(ABORT, 'artifact manifests are immutable');
END;

CREATE TRIGGER artifact_manifests_no_delete
BEFORE DELETE ON artifact_manifests
BEGIN
    SELECT RAISE(ABORT, 'artifact manifests are immutable');
END;
