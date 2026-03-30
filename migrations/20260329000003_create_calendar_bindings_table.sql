-- Create enums used by the calendar_bindings table
CREATE TYPE schedule_transparency AS ENUM ('opaque', 'transparent');

-- Calendar bindings table: per-user mount/view of a calendar
-- Each user has a binding record when they access a calendar (owned or shared)
CREATE TABLE calendar_bindings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    calendar_id UUID NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
    principal_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- The URI segment this calendar appears under for this user
    -- e.g., "work" for /dav/calendars/john/work/
    uri VARCHAR(255) NOT NULL,

    -- Per-user metadata (can differ per binding even for same calendar)
    displayname VARCHAR(255),
    calendar_description TEXT,

    -- Timezone that this user views the calendar in (overrides global if set)
    calendar_timezone TEXT,
    calendar_timezone_id VARCHAR(255),

    -- Free-busy contribution setting (per RFC 6638, must be per-instance for shared calendars)
    schedule_transparency schedule_transparency NOT NULL DEFAULT 'opaque',

    -- Client preferences
    calendar_color VARCHAR(32),
    calendar_order INTEGER NOT NULL DEFAULT 0,

    -- Per-user extension properties
    extra_properties JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Each calendar can only have one binding per user
    CONSTRAINT uq_calendar_bindings_calendar_principal UNIQUE (calendar_id, principal_user_id),
    -- Each user can only have one binding per URI
    CONSTRAINT uq_calendar_bindings_principal_uri UNIQUE (principal_user_id, uri),
    CONSTRAINT chk_calendar_order_positive CHECK (calendar_order >= 0)
);

CREATE INDEX idx_calendar_bindings_calendar_id ON calendar_bindings(calendar_id);
CREATE INDEX idx_calendar_bindings_principal_user_id ON calendar_bindings(principal_user_id);
CREATE INDEX idx_calendar_bindings_extra_properties_gin ON calendar_bindings USING GIN (extra_properties);
