# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

from .view_helpers.calendar_mutation_payloads import (
    _calendar_collection_proppatch_plan,
    _mkcalendar_props_from_payload,
)
from .view_helpers.freebusy import _build_freebusy_response_lines
from .view_helpers.ical import _dedupe_duplicate_alarms
from .view_helpers.identity import (
    _calendar_home_href_for_user,
    _dav_guid_for_username,
    _dav_username_for_guid,
    _principal_href_for_user,
)
from .view_helpers.parsing import _calendar_default_tzinfo, _parse_xml_body
from .view_helpers.recurrence_serialization import (
    _append_date_or_datetime_line,
    _resolved_recurrence_text,
    _serialize_expanded_components,
    _uid_drop_recurrence_map,
)
from .view_helpers.report_paths import (
    _all_object_hrefs,
    _all_object_hrefs_for_data,
    _collection_href_for_style,
    _object_href_for_filename,
    _object_href_for_style,
    _object_href_for_style_data,
    _report_href_style,
)
from .view_helpers.sync_tokens import _build_sync_token

from .views_collections import (
    calendar_home_uid_view,
    calendar_home_users_view,
    calendar_home_view,
    calendars_collection_view,
    calendars_uids_collection_view,
    calendars_users_collection_view,
    dav_root,
    principal_uid_view,
    principal_users_view,
    principal_view,
    principals_collection_view,
    principals_users_collection_view,
    well_known_caldav,
)
from .views_common import (
    _caldav_error_response,
    _client_ip,
    _collection_exists,
    _conditional_not_modified,
    _create_calendar_change,
    _dav_common_headers,
    _dav_error_response,
    _etag_for_calendar,
    _etag_for_object,
    _generate_strong_etag,
    _home_etag_and_timestamp,
    _latest_sync_revision,
    _log_dav_create,
    _not_allowed,
    _parse_propfind_payload,
    _parse_sync_token_for_calendar,
    _proppatch_multistatus_response,
    _remote_ip,
    _require_dav_user,
    _sync_token_for_calendar,
    _sync_token_revision_from_parts,
    _valid_sync_token_error_response,
    _visible_calendars_for_home,
    _xml_response,
)
from .views_objects import (
    _copy_or_move_calendar_object,
    _remap_uid_for_copied_object,
    calendar_collection_uid_view,
    calendar_collection_users_view,
    calendar_collection_view,
    calendar_object_uid_view,
    calendar_object_users_view,
    calendar_object_view,
)
from .views_reports import (
    _build_prop_map_for_object,
    _filter_calendar_data_with_active_tz,
    _handle_report,
    _object_matches_query_with_active_tz,
    _render_freebusy_report,
    _responses_for_calendar_query,
    _responses_for_multiget,
    _sync_collection_limit,
    _sync_collection_multistatus_document,
    _sync_collection_response,
    _tzinfo_from_report,
)
