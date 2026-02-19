from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReportExecutionContext:
    calendars: tuple
    request_path: str
    root: object
    report_kind: str
    requested_props: tuple | None
    calendar_data_request: object | None
    parsed_report: object


def build_report_execution_context(
    *,
    parsed_report,
    calendars,
    request_path,
    classify_report_kind,
):
    return ReportExecutionContext(
        calendars=tuple(calendars),
        request_path=request_path,
        root=parsed_report.root,
        report_kind=classify_report_kind(parsed_report.root.tag),
        requested_props=parsed_report.requested_props,
        calendar_data_request=parsed_report.calendar_data_request,
        parsed_report=parsed_report,
    )


def dispatch_report(
    *,
    context,
    report_kind_multiget,
    report_kind_query,
    report_kind_freebusy,
    report_kind_sync_collection,
    handle_multiget,
    handle_query,
    handle_freebusy,
    handle_sync_collection,
    handle_unknown,
):
    if context.report_kind == report_kind_multiget:
        return handle_multiget(context)
    if context.report_kind == report_kind_query:
        return handle_query(context)
    if context.report_kind == report_kind_freebusy:
        return handle_freebusy(context)
    if context.report_kind == report_kind_sync_collection:
        return handle_sync_collection(context)
    return handle_unknown(context)
