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
    commit_order BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ
);

CREATE FUNCTION reject_control_plane_truth_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE TRIGGER workflow_transitions_no_change
BEFORE UPDATE OR DELETE ON workflow_transitions
FOR EACH ROW EXECUTE FUNCTION reject_control_plane_truth_change();

CREATE TRIGGER workflow_transitions_no_truncate
BEFORE TRUNCATE ON workflow_transitions
FOR EACH STATEMENT EXECUTE FUNCTION reject_control_plane_truth_change();

CREATE TRIGGER run_records_no_change
BEFORE UPDATE OR DELETE ON run_records
FOR EACH ROW EXECUTE FUNCTION reject_control_plane_truth_change();

CREATE TRIGGER run_records_no_truncate
BEFORE TRUNCATE ON run_records
FOR EACH STATEMENT EXECUTE FUNCTION reject_control_plane_truth_change();

CREATE TRIGGER artifact_manifests_no_change
BEFORE UPDATE OR DELETE ON artifact_manifests
FOR EACH ROW EXECUTE FUNCTION reject_control_plane_truth_change();

CREATE TRIGGER artifact_manifests_no_truncate
BEFORE TRUNCATE ON artifact_manifests
FOR EACH STATEMENT EXECUTE FUNCTION reject_control_plane_truth_change();
