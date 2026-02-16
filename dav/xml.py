from xml.etree import ElementTree as ET


NS_DAV = "DAV:"
NS_CALDAV = "urn:ietf:params:xml:ns:caldav"
NS_CS = "http://calendarserver.org/ns/"

ET.register_namespace("D", NS_DAV)
ET.register_namespace("C", NS_CALDAV)
ET.register_namespace("CS", NS_CS)


def qname(namespace, local):
    return f"{{{namespace}}}{local}"


def href_element(parent, href):
    elem = ET.SubElement(parent, qname(NS_DAV, "href"))
    elem.text = href
    return elem


def status_element(parent, status):
    elem = ET.SubElement(parent, qname(NS_DAV, "status"))
    elem.text = f"HTTP/1.1 {status}"
    return elem


def multistatus_document(responses):
    root = ET.Element(qname(NS_DAV, "multistatus"))
    for response in responses:
        root.append(response)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def response_with_props(href, ok_props, missing_props=None):
    response = ET.Element(qname(NS_DAV, "response"))
    href_element(response, href)

    if ok_props:
        propstat = ET.SubElement(response, qname(NS_DAV, "propstat"))
        prop = ET.SubElement(propstat, qname(NS_DAV, "prop"))
        for element in ok_props:
            prop.append(element)
        status_element(propstat, "200 OK")

    if missing_props:
        propstat = ET.SubElement(response, qname(NS_DAV, "propstat"))
        prop = ET.SubElement(propstat, qname(NS_DAV, "prop"))
        for element in missing_props:
            prop.append(element)
        status_element(propstat, "404 Not Found")

    return response


def parse_requested_properties(request_body):
    if not request_body:
        return None

    try:
        root = ET.fromstring(request_body)
    except ET.ParseError:
        return None

    prop = root.find(qname(NS_DAV, "prop"))
    if prop is None:
        return None

    requested = []
    for child in list(prop):
        requested.append(child.tag)
    return requested
