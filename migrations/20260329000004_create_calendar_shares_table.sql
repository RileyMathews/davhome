-- Create enums for calendar shares
CREATE TYPE calendar_access_mode AS ENUM ('owner', 'read', 'write');
CREATE TYPE calendar_share_status AS ENUM ('pending', 'accepted', 'declined', 'revoked');

-- Calendar shares table: stores invitations and sharing authorization
-- Separate from calendar_bindings which tracks per-user mount state
CREATE TABLE calendar_shares (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    calendar_id UUID NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
    sharee_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    invited_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

    -- Access level being offered (read or write)
    -- Note: 'owner' is not allowed here, only for bindings
    access_mode calendar_access_mode NOT NULL,
    status calendar_share_status NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    responded_at TIMESTAMPTZ,

    -- Each calendar can only have one share per sharee
    CONSTRAINT uq_calendar_shares_calendar_sharee UNIQUE (calendar_id, sharee_user_id),
    -- Don't allow sharing to owner
    CONSTRAINT chk_not_owner_share CHECK (access_mode <> 'owner')
);

CREATE INDEX idx_calendar_shares_calendar_id ON calendar_shares(calendar_id);
CREATE INDEX idx_calendar_shares_sharee_user_id ON calendar_shares(sharee_user_id);
