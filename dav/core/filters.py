from dav.xml import NS_CALDAV, qname


def property_lines(component_text, property_name):
    lines = component_text.replace("\r\n", "\n").split("\n")
    prefix = f"{property_name.upper()}"
    result = []
    for line in lines:
        if not line:
            continue
        upper = line.upper()
        if upper.startswith(prefix + ":") or upper.startswith(prefix + ";"):
            result.append(line)
    return result


def parse_property_params(prop_line):
    head = prop_line.split(":", 1)[0]
    parts = head.split(";")
    params = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params.setdefault(key.upper(), []).append(value)
    return params


def text_match(value, matcher):
    if value is None:
        return False
    text = matcher.text or ""
    negate = (matcher.get("negate-condition") or "").lower() == "yes"
    coll = (matcher.get("collation") or "i;ascii-casemap").lower()

    left = value
    right = text
    if coll == "i;ascii-casemap":
        left = left.lower()
        right = right.lower()

    match_type = (matcher.get("match-type") or "contains").lower()
    if match_type == "starts-with":
        ok = left.startswith(right)
    elif match_type == "ends-with":
        ok = left.endswith(right)
    elif match_type == "equals":
        ok = left == right
    else:
        ok = right in left
    return (not ok) if negate else ok


def combine_filter_results(results, test_attr):
    test = (test_attr or "allof").lower()
    if test == "anyof":
        return any(results)
    return all(results)


def matches_param_filter(prop_lines, param_filter):
    param_name = (param_filter.get("name") or "").upper()
    if not param_name:
        return True

    is_not_defined = param_filter.find(qname(NS_CALDAV, "is-not-defined")) is not None
    text_match_elem = param_filter.find(qname(NS_CALDAV, "text-match"))

    params_present = []
    for line in prop_lines:
        params = parse_property_params(line)
        values = params.get(param_name, [])
        params_present.extend(values)

    if is_not_defined:
        return len(params_present) == 0

    if text_match_elem is None:
        return len(params_present) > 0

    if not params_present:
        return False

    return any(text_match(value, text_match_elem) for value in params_present)


def matches_prop_filter(component_text, prop_filter, line_matches_time_range):
    prop_name = (prop_filter.get("name") or "").upper()
    if not prop_name:
        return True

    lines = property_lines(component_text, prop_name)
    is_not_defined = prop_filter.find(qname(NS_CALDAV, "is-not-defined")) is not None
    text_matches = prop_filter.findall(qname(NS_CALDAV, "text-match"))
    param_filters = prop_filter.findall(qname(NS_CALDAV, "param-filter"))
    time_ranges = prop_filter.findall(qname(NS_CALDAV, "time-range"))
    test_attr = prop_filter.get("test")

    if is_not_defined:
        return len(lines) == 0

    if not lines:
        return False

    if text_matches:
        values = [line.split(":", 1)[1] if ":" in line else "" for line in lines]
        matches = [
            any(text_match(value, matcher) for value in values)
            for matcher in text_matches
        ]
        if not combine_filter_results(matches, test_attr):
            return False

    param_results = [
        matches_param_filter(lines, param_filter) for param_filter in param_filters
    ]
    if param_results and not combine_filter_results(param_results, test_attr):
        return False

    if time_ranges:
        range_results = [
            any(line_matches_time_range(line, timerange) for line in lines)
            for timerange in time_ranges
        ]
        if not combine_filter_results(range_results, test_attr):
            return False

    return True
