-- Calendar changes table: append-only log of all changes to calendars and their objects
-- Used for WebDAV sync protocol to answer "what changed since sync_token X?"
CREATE TABLE calendar_changes (
    id BIGSERIAL PRIMARY KEY,
    calendar_id UUID NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
    sync_token BIGINT NOT NULL,
    changed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    binding_id UUID REFERENCES calendar_bindings(id) ON DELETE SET NULL,
    object_uri VARCHAR(255),
    operation SMALLINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Operation codes: 1=added, 2=modified, 3=removed
    CONSTRAINT chk_operation_code CHECK (operation IN (1, 2, 3))
);

-- Index for fast sync queries: find changes for a calendar since a token
CREATE INDEX idx_calendar_changes_calendar_token ON calendar_changes(calendar_id, sync_token);

-- Index for cleanup: quickly find all changes for a calendar
CREATE INDEX idx_calendar_changes_calendar_id ON calendar_changes(calendar_id);
