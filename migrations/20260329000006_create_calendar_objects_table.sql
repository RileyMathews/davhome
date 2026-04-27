-- Calendar objects table: stores individual iCalendar resources within a calendar collection.
CREATE TABLE calendar_objects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    calendar_id UUID NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,

    -- Last path segment under the calendar collection, e.g. event-123.ics.
    href VARCHAR(255) NOT NULL,

    -- CalDAV requires UID uniqueness within a calendar collection.
    uid VARCHAR(255) NOT NULL,
    component_type calendar_component NOT NULL,

    -- Preserve the submitted iCalendar bytes as UTF-8 text for strong ETag parity.
    icalendar TEXT NOT NULL,
    etag VARCHAR(128) NOT NULL,
    last_modified_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Reserved for storage-level calendar-query prefiltering.
    dtstart_at TIMESTAMPTZ,
    dtend_at TIMESTAMPTZ,

    extra_properties JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_calendar_objects_calendar_href UNIQUE (calendar_id, href),
    CONSTRAINT uq_calendar_objects_calendar_uid UNIQUE (calendar_id, uid),
    CONSTRAINT chk_calendar_objects_href_not_empty CHECK (length(trim(href)) > 0),
    CONSTRAINT chk_calendar_objects_uid_not_empty CHECK (length(trim(uid)) > 0)
);

CREATE INDEX idx_calendar_objects_calendar_id ON calendar_objects(calendar_id);
CREATE INDEX idx_calendar_objects_calendar_component ON calendar_objects(calendar_id, component_type);
CREATE INDEX idx_calendar_objects_time_range ON calendar_objects(calendar_id, dtstart_at, dtend_at);
