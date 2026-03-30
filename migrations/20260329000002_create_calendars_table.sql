-- Create the calendar_component enum used by the calendars table
CREATE TYPE calendar_component AS ENUM ('VEVENT', 'VTODO');

-- Calendars table: stores canonical calendar collections and their global properties
CREATE TABLE calendars (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Collection policy: what component types are allowed
    supported_component_set calendar_component[] NOT NULL DEFAULT ARRAY['VEVENT', 'VTODO']::calendar_component[],

    -- Server-imposed limits (optional, NULL means no limit)
    max_resource_size BIGINT,
    min_date_at TIMESTAMPTZ,
    max_date_at TIMESTAMPTZ,
    max_instances INTEGER,
    max_attendees_per_instance INTEGER,

    -- Timezone configuration
    calendar_timezone TEXT,
    calendar_timezone_id VARCHAR(255),

    -- Global extension properties (dead properties that are calendar-wide)
    extra_properties JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ,

    CONSTRAINT chk_max_resource_size_positive CHECK (max_resource_size IS NULL OR max_resource_size > 0),
    CONSTRAINT chk_max_instances_positive CHECK (max_instances IS NULL OR max_instances > 0),
    CONSTRAINT chk_max_attendees_positive CHECK (max_attendees_per_instance IS NULL OR max_attendees_per_instance > 0),
    CONSTRAINT chk_date_range CHECK (min_date_at IS NULL OR max_date_at IS NULL OR min_date_at <= max_date_at)
);

CREATE INDEX idx_calendars_owner_user_id ON calendars(owner_user_id);
CREATE INDEX idx_calendars_deleted_at ON calendars(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_calendars_extra_properties_gin ON calendars USING GIN (extra_properties);
