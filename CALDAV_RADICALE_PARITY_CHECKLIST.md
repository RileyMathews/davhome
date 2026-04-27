# CalDAV Radicale Parity Checklist

This checklist tracks the WebDAV and CalDAV behavior needed for davhome to reach roughly the same calendar-client compliance level as Radicale. It is based on a read-only inspection of the Radicale checkout at `~/code/Radicale` on 2026-04-26.

Use this as the DAV implementation backlog. Mark an item complete only when the behavior is implemented in davhome and covered by relevant tests. If an item is partially scaffolded, keep the checkbox open and add a short note.

## Scope

- Target parity is CalDAV plus the WebDAV behavior needed by calendar clients.
- Calendar sharing is in scope because it is a core davhome product feature.
- CardDAV and Radicale-specific web/API behavior are listed as optional unless project scope changes.
- RFC details should be checked in `RFC/` before implementing each feature.

## Radicale References

- Request method dispatch: `~/code/Radicale/radicale/app/__init__.py`
- HTTP helpers and DAV capability headers: `~/code/Radicale/radicale/httputils.py`
- XML namespaces and property parsing: `~/code/Radicale/radicale/xmlutils.py`
- DAV handlers: `~/code/Radicale/radicale/app/*.py`
- Calendar filtering: `~/code/Radicale/radicale/item/filter.py`
- Calendar item validation and serialization: `~/code/Radicale/radicale/item/__init__.py`
- Storage, ETags, CTags, and sync tokens: `~/code/Radicale/radicale/storage/**/*.py`
- Radicale unit behavior: `~/code/Radicale/radicale/tests/test_base.py` and `~/code/Radicale/radicale/tests/test_expand.py`
- Radicale sharing behavior: `~/code/Radicale/SHARING.md` and `~/code/Radicale/radicale/tests/test_sharing.py`

## Current Davhome Foundations

- [x] Basic DAV Basic authentication helper exists in `src/auth.rs`.
- [x] Custom Axum routing exists for non-standard DAV methods in `src/custom_method_router.rs` and `src/dav_method.rs`.
- [x] Calendar, binding, share, and sync-change schema foundations exist in `migrations/`.
- [x] Minimal collection `MKCOL` exists in `src/routes/dav.rs`, currently as a temporary shortcut that creates a calendar binding rather than full extended WebDAV `MKCOL` behavior.
- [x] Minimal collection `DELETE` exists in `src/routes/dav.rs` for owner-owned calendar bindings.

## HTTP And WebDAV Surface

- [ ] Route DAV paths for `GET`.
- [ ] Route DAV paths for `HEAD`.
- [ ] Route DAV paths for `OPTIONS`.
- [ ] Route DAV paths for `PROPFIND`.
- [ ] Route DAV paths for `PROPPATCH`.
- [ ] Route DAV paths for `MKCOL`.
- [ ] Route DAV paths for `MKCALENDAR`.
- [ ] Route DAV paths for `PUT`.
- [ ] Route DAV paths for `DELETE` on both collections and objects.
- [ ] Route DAV paths for `MOVE`.
- [ ] Route DAV paths for `REPORT`.
- [ ] Return accurate `Allow` headers for each route.
- [ ] Return accurate `DAV` capability headers, including `1`, `2`, `3`, `calendar-access`, and `extended-mkcol` when implemented.
- [ ] Implement `/.well-known/caldav` redirect behavior.
- [ ] Normalize or reject unsafe paths consistently, including double slashes and encoded path components.
- [ ] Preserve a stable `/dav` namespace and principal/calendar homes under it.

## XML And Response Infrastructure

- [ ] Return Askama-rendered XML templates for every DAV XML response.
- [ ] Add namespace support for `DAV:`, `urn:ietf:params:xml:ns:caldav`, `http://calendarserver.org/ns/`, and `http://apple.com/ns/ical/`.
- [ ] Add reusable XML response templates for `D:multistatus`.
- [ ] Add reusable XML response templates for `D:response` and `D:propstat`.
- [ ] Add reusable XML response templates for `D:error` precondition responses.
- [ ] Format WebDAV status values as full HTTP status lines such as `HTTP/1.1 200 OK`.
- [ ] Parse XML request bodies safely and return `400 Bad Request` for invalid XML.
- [ ] Correctly quote and escape `D:href` values.

## Authentication And Authorization

- [x] Challenge unauthenticated DAV requests with Basic auth.
- [ ] Resolve the authenticated principal from DAV requests for all handlers.
- [ ] Enforce owner-only access for private calendar homes and collections.
- [ ] Add a high-level authorization helper for read, write-content, write-properties, collection create, and collection delete checks.
- [ ] Return `D:current-user-principal` for authenticated users.
- [ ] Return `D:unauthenticated` or challenge unauthenticated principal discovery in the client-compatible way we choose.
- [ ] Return `D:current-user-privilege-set` with accurate read/write privileges.
- [ ] Integrate shared calendar permissions into every DAV handler.

## Data Model And Storage

- [x] Calendar collection tables exist.
- [x] Per-user calendar binding tables exist.
- [x] Sharing tables exist.
- [x] Sync-change table scaffold exists.
- [ ] Add calendar object storage with calendar id, href, UID, component type, serialized iCalendar, ETag, last modified, and time range fields.
- [ ] Enforce unique object href per calendar.
- [ ] Enforce unique UID per calendar where CalDAV requires it.
- [ ] Store arbitrary live/dead DAV properties as JSON blobs where appropriate.
- [ ] Compute stable object ETags from serialized object content.
- [ ] Compute collection CTags from object and metadata changes.
- [ ] Maintain last-modified values for objects and collections.
- [ ] Record add, modify, delete, and move operations for sync reporting.
- [ ] Add storage queries for object lookup by href.
- [ ] Add storage queries for object lookup by UID.
- [ ] Add storage queries for all objects in a calendar.
- [ ] Add storage queries optimized by component type and time range.

## Discovery And PROPFIND

- [ ] Treat empty `PROPFIND` bodies as `D:allprop`.
- [ ] Support `D:allprop`.
- [ ] Support `D:propname`.
- [ ] Support explicit `D:prop` requests.
- [ ] Support `Depth: 0` discovery.
- [ ] Support `Depth: 1` discovery.
- [ ] Return `D:principal-collection-set`.
- [ ] Return `D:current-user-principal`.
- [ ] Return `D:principal-URL`.
- [ ] Return `C:calendar-home-set`.
- [ ] Return `C:calendar-user-address-set`.
- [ ] Return `D:resourcetype` for root, principals, collections, calendars, and objects.
- [ ] Return `D:displayname` from binding or collection metadata.
- [ ] Return `D:owner`.
- [ ] Return `D:getetag` for calendar objects and leaf collections where appropriate.
- [ ] Return `D:getlastmodified`.
- [ ] Return `D:getcontenttype`.
- [ ] Return `D:getcontentlength`.
- [ ] Return `D:supported-report-set`.
- [ ] Return `C:supported-calendar-component-set`.
- [ ] Return `C:max-resource-size`.
- [ ] Return `CS:getctag`.
- [ ] Return `D:sync-token`.
- [ ] Return Apple/CalendarServer display properties such as `ICAL:calendar-color` and order when stored.
- [ ] Return stored arbitrary properties with `404` propstat for missing requested properties.

## Collection Creation And Properties

- [ ] Implement real `MKCALENDAR` calendar creation.
- [ ] Parse `MKCALENDAR` property bodies.
- [ ] Reject `MKCALENDAR` when the destination already exists with the proper WebDAV error.
- [ ] Reject `MKCALENDAR` when intermediate collections are missing.
- [ ] Implement generic `MKCOL` for plain WebDAV collections if needed by litmus.
- [ ] Implement extended `MKCOL` for calendar collections via `D:resourcetype`.
- [ ] Parse `D:resourcetype` from `MKCOL` bodies.
- [ ] Parse and store `C:supported-calendar-component-set`.
- [ ] Parse and store `D:displayname`.
- [ ] Parse and store `C:calendar-description`.
- [ ] Parse and store `ICAL:calendar-color`.
- [ ] Implement `PROPPATCH` set operations for collection metadata.
- [ ] Implement `PROPPATCH` remove operations for collection metadata.
- [ ] Return per-property `207 Multi-Status` results from `PROPPATCH`.
- [ ] Reject invalid or unsupported property values with useful WebDAV errors.

## Calendar Object Operations

- [ ] Implement `PUT` for single calendar objects.
- [ ] Implement whole-calendar `PUT` that splits `VEVENT`, `VTODO`, and `VJOURNAL` components by UID.
- [ ] Support `VEVENT` calendar objects.
- [ ] Support `VTODO` calendar objects.
- [ ] Support `VJOURNAL` calendar objects, or explicitly decide not to target Radicale parity for journals.
- [ ] Preserve and serialize `VTIMEZONE` components attached to calendar objects.
- [ ] Validate that single-object `PUT` contains exactly one object UID.
- [ ] Reject single calendar objects containing mixed main component types.
- [ ] Reject malformed iCalendar payloads.
- [ ] Enforce collection `supported-calendar-component-set`.
- [ ] Enforce maximum request body size.
- [ ] Enforce maximum resource size.
- [ ] Detect UID conflicts and return CalDAV `C:no-uid-conflict` errors.
- [ ] Implement `If-Match` handling for updates.
- [ ] Implement `If-None-Match: *` handling for creates.
- [ ] Decide whether to require strict preconditions for updates, then implement consistently.
- [ ] Return object `ETag` headers from successful `PUT`.
- [ ] Return `201 Created` for new objects.
- [ ] Return `204 No Content` for updated objects.
- [ ] Implement `GET` for individual calendar objects.
- [ ] Implement `GET` for calendar collections as aggregate `.ics` output.
- [ ] Deduplicate `VTIMEZONE` entries in aggregate calendar output.
- [ ] Include `X-WR-CALNAME` and `X-WR-CALDESC` in aggregate output when display metadata exists.
- [ ] Implement `HEAD` using the same headers as `GET` without a response body.
- [ ] Return `Content-Type: text/calendar;charset=utf-8;component=...` for calendar objects.
- [ ] Return `Last-Modified` headers for `GET` and `HEAD`.
- [ ] Return `ETag` headers for `GET` and `HEAD`.
- [ ] Implement `DELETE` for individual calendar objects.
- [ ] Implement `DELETE` for calendar collections with explicit authorization checks.
- [ ] Implement `If-Match` handling for `DELETE`.
- [ ] Implement same-server object `MOVE`.
- [ ] Reject remote-destination `MOVE`.
- [ ] Reject collection `MOVE` with method-not-allowed unless full collection moves are intentionally implemented.
- [ ] Honor `Overwrite` handling for `MOVE`.
- [ ] Detect UID conflicts during `MOVE`.

## REPORT Support

- [ ] Advertise supported reports through `D:supported-report-set`.
- [ ] Implement `C:calendar-multiget`.
- [ ] Resolve `D:href` values in `calendar-multiget` relative to the DAV base path.
- [ ] Return `404` response entries for missing `calendar-multiget` hrefs.
- [ ] Implement `C:calendar-query` on calendar collections.
- [ ] Implement `C:calendar-query` on individual objects where clients send it.
- [ ] Return `C:calendar-data` in report responses.
- [ ] Return `D:getetag` in report responses.
- [ ] Return `D:getcontenttype` in report responses.
- [ ] Return requested unsupported report properties as `404` propstats.
- [ ] Reject reports against incompatible collection types with `D:supported-report` errors.
- [ ] Return empty multistatus for unsupported principal reports if needed for client compatibility.
- [ ] Implement `C:free-busy-query`.
- [ ] Generate `VFREEBUSY` from matching `VEVENT` objects.
- [ ] Respect transparent events in free-busy generation.
- [ ] Map `CONFIRMED`, missing status, `CANCELLED`, and `TENTATIVE` to Radicale-like free-busy output.
- [ ] Enforce a free-busy occurrence limit.

## Calendar Query Filtering

- [ ] Implement `C:comp-filter`.
- [ ] Implement nested `C:comp-filter` for `VCALENDAR` to `VEVENT`, `VTODO`, and `VJOURNAL`.
- [ ] Implement nested `VALARM` component filters.
- [ ] Implement `C:prop-filter` presence checks.
- [ ] Implement `C:is-not-defined` for component and property filters.
- [ ] Implement `C:param-filter`.
- [ ] Implement `C:text-match` for calendar properties.
- [ ] Implement case-insensitive text matching compatible with typical client expectations.
- [ ] Implement `negate-condition` for text matches.
- [ ] Implement `C:time-range` at the `VCALENDAR` level.
- [ ] Implement `C:time-range` for `VEVENT` with `DTSTART`, `DTEND`, `DURATION`, all-day dates, and missing end semantics.
- [ ] Implement `C:time-range` for recurring `VEVENT` objects.
- [ ] Implement `C:time-range` for overridden `RECURRENCE-ID` instances.
- [ ] Implement `C:time-range` for `VTODO` with `DTSTART`, `DUE`, `DURATION`, `COMPLETED`, and `CREATED` semantics.
- [ ] Implement `C:time-range` for recurring `VTODO` objects.
- [ ] Implement `C:time-range` for `VJOURNAL`.
- [ ] Implement `C:time-range` for `VALARM` absolute triggers.
- [ ] Implement `C:time-range` for `VALARM` relative triggers.
- [ ] Add storage-level prefiltering for component type and time ranges.

## Recurrence And Calendar-Data Expansion

- [ ] Parse and validate recurrence rules during object writes.
- [ ] Support `RRULE` in query filters.
- [ ] Support `RDATE` in query filters.
- [ ] Support `EXDATE` in query filters.
- [ ] Support recurrence overrides with `RECURRENCE-ID`.
- [ ] Support recurring all-day events.
- [ ] Support recurring events with timezone-aware date-times.
- [ ] Implement `C:calendar-data` `C:expand`.
- [ ] Require both `start` and `end` on `C:expand`.
- [ ] Expand recurring events into individual `VEVENT` instances.
- [ ] Strip `RRULE`, recurrence metadata, and unnecessary `VTIMEZONE` output from expanded responses where appropriate.
- [ ] Preserve correct `UID` values in expanded instances.
- [ ] Emit correct `RECURRENCE-ID` values in expanded instances.
- [ ] Enforce a report occurrence limit for expansion.

## WebDAV Sync

- [ ] Return a collection `D:sync-token` through `PROPFIND`.
- [ ] Implement `D:sync-collection` REPORT.
- [ ] Return current objects for an empty or initial sync token.
- [ ] Return no objects when a valid sync token has no changes.
- [ ] Return added objects since a sync token.
- [ ] Return modified objects since a sync token.
- [ ] Return deleted objects as `404` responses since a sync token.
- [ ] Return moved objects as deleted old href plus added new href.
- [ ] Return a new sync token in every successful sync response.
- [ ] Reject malformed or expired sync tokens with `D:valid-sync-token` errors.
- [ ] Define sync-token retention and cleanup behavior.

## Sharing

- [x] Calendar share schema exists.
- [ ] Add web UI flow for creating shares.
- [ ] Add web UI flow for accepting or declining shares.
- [ ] Create per-user calendar bindings for accepted shares.
- [ ] Surface shared calendars in the sharee's calendar home discovery.
- [ ] Enforce read-only shared-calendar access in every DAV handler.
- [ ] Enforce read-write shared-calendar access in every DAV handler.
- [ ] Keep owner-only operations restricted to the calendar owner.
- [ ] Support per-user shared calendar display name.
- [ ] Support per-user shared calendar color.
- [ ] Support per-user shared calendar order.
- [ ] Support per-user shared calendar description when desired.
- [ ] Support per-binding schedule transparency for free-busy behavior.
- [ ] Decide whether token/public sharing is in scope; Radicale supports it, but davhome's stated feature is first-class authenticated user sharing.
- [ ] Decide whether property overlays should be owner-controlled, sharee-controlled, or both.

## Client Compatibility Quirks

- [ ] Add DAVx5-compatible handling for `current-user-principal` discovery.
- [ ] Add Apple Calendar-compatible `ICAL:calendar-color` behavior.
- [ ] Add CalendarServer-compatible `CS:getctag` behavior.
- [ ] Add robust URI percent-decoding and href re-encoding.
- [ ] Add path validation that rejects unsafe or ambiguous paths.
- [ ] Handle iCalendar line endings and serialization consistently.
- [ ] Handle malformed but common client payload quirks only when justified by tests or client behavior.
- [ ] Decide whether to match Radicale's Lightning zero-duration workaround.
- [ ] Decide whether to match Radicale's EXDATE/RDATE value-type repair behavior.

## Testing And Compliance Tracking

- [ ] Add unit tests for XML template rendering.
- [ ] Add HTTP integration tests for every DAV method.
- [ ] Add storage tests for calendar objects, UIDs, ETags, CTags, and sync changes.
- [ ] Add fixtures for `VEVENT`, `VTODO`, `VJOURNAL`, `VALARM`, recurrence, and timezone cases.
- [ ] Add tests modeled after Radicale's `test_base.py` for PUT, GET, DELETE, MOVE, PROPFIND, PROPPATCH, REPORT, and sync behavior.
- [ ] Add tests modeled after Radicale's `test_expand.py` for recurrence expansion behavior.
- [ ] Run caldavtester after meaningful DAV changes.
- [ ] Record caldavtester passing-test counts in the top-level `README.md` when that file exists or is added.
- [ ] Run litmus after meaningful general WebDAV changes.
- [ ] Record litmus progress in the top-level `README.md` when that file exists or is added.
- [ ] Investigate any regression where caldavtester or litmus failures increase from the documented count.

## Optional Or Out Of Scope Until Decided

- [ ] CardDAV addressbook collections.
- [ ] CardDAV `addressbook-query` and `addressbook-multiget` reports.
- [ ] vCard 3.0 and 4.0 storage and validation.
- [ ] Birthday-calendar conversion from contacts.
- [ ] Radicale-compatible `/.web` DAV-adjacent web interface behavior.
- [ ] Radicale-compatible `/.sharing` API behavior.
- [ ] Radicale-compatible token sharing.
- [ ] Multiple Radicale-style auth backends such as LDAP, IMAP, PAM, OAuth2, or htpasswd.
- [ ] External hook systems for email, RabbitMQ, or command execution.
- [ ] Full CalDAV scheduling beyond what is needed for shared-calendar visibility and free-busy behavior.
