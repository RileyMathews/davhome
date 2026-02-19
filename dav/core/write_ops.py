from dataclasses import dataclass

from .contracts import ProtocolError, WriteDecision, WritePrecondition


@dataclass(frozen=True, slots=True)
class PayloadValidationPlan:
    content_type: str
    is_ical: bool


def build_write_precondition(
    *,
    if_match_header,
    if_none_match_header,
    existing_etag,
    parse_if_match_values,
):
    if_match_values = ()
    if if_match_header:
        if_match_values = tuple(parse_if_match_values(if_match_header))
    return WritePrecondition(
        if_match=if_match_values,
        if_none_match=if_none_match_header,
        existing_etag=existing_etag,
    )


def decide_precondition(precondition: WritePrecondition) -> WriteDecision:
    if precondition.if_none_match == "*" and precondition.existing_etag is not None:
        return WriteDecision(
            allowed=False,
            error=ProtocolError(
                code="precondition-failed",
                namespace="dav",
                http_status=412,
            ),
        )

    if precondition.if_match:
        if precondition.existing_etag is None:
            return WriteDecision(
                allowed=False,
                error=ProtocolError(
                    code="precondition-failed",
                    namespace="dav",
                    http_status=412,
                ),
            )
        if (
            "*" not in precondition.if_match
            and precondition.existing_etag not in precondition.if_match
        ):
            return WriteDecision(
                allowed=False,
                error=ProtocolError(
                    code="precondition-failed",
                    namespace="dav",
                    http_status=412,
                ),
            )

    return WriteDecision(allowed=True)


def build_payload_validation_plan(
    *,
    filename,
    raw_content_type,
    normalize_content_type,
    is_ical_resource,
):
    content_type = normalize_content_type(raw_content_type)
    return PayloadValidationPlan(
        content_type=content_type,
        is_ical=is_ical_resource(filename, content_type),
    )


def decide_component_kind(*, parsed_component_kind, calendar_component_kind):
    if (
        parsed_component_kind is None
        or parsed_component_kind != calendar_component_kind
    ):
        return WriteDecision(
            allowed=False,
            error=ProtocolError(code="supported-calendar-component"),
        )
    return WriteDecision(allowed=True)
