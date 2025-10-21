import zipfile
from xml.etree import ElementTree as ET


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XMLNS_NAMESPACE = "http://www.w3.org/2000/xmlns/"


def _replace_placeholders_in_xml(xml_bytes, replacements):
    """Replace placeholders in the given Word document XML bytes."""

    if not replacements:
        return xml_bytes

    root = ET.fromstring(xml_bytes)

    # Ensure existing namespace prefixes are preserved when the tree is serialized.
    for attr_key, uri in root.attrib.items():
        if attr_key.startswith(f"{{{XMLNS_NAMESPACE}}}"):
            prefix = attr_key.split("}", 1)[1]
            ET.register_namespace(prefix, uri)

    ET.register_namespace("w", WORD_NAMESPACE)
    text_elements = list(root.iter(f"{{{WORD_NAMESPACE}}}t"))

    def apply_replacements(text):
        if not text:
            return text
        updated = text
        for placeholder, value in replacements.items():
            updated = updated.replace(placeholder, value)
        return updated

    i = 0
    total = len(text_elements)
    while i < total:
        elem = text_elements[i]
        text = elem.text or ""

        if "{{" in text and "}}" not in text:
            buffer = []
            combined = ""
            while i < total:
                current_elem = text_elements[i]
                current_text = current_elem.text or ""
                buffer.append(current_elem)
                combined += current_text
                i += 1
                if "}}" in combined:
                    break

            replaced_text = apply_replacements(combined)
            if buffer:
                buffer[0].text = replaced_text
                for extra_elem in buffer[1:]:
                    extra_elem.text = ""
        else:
            elem.text = apply_replacements(text)
            i += 1

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def populate_work_order(values, template_path, output_path):
    """Populate the Word template with the provided values.

    Parameters
    ----------
    values: Mapping[str, Any]
        Dictionary with placeholder names as keys.
    template_path: str
        Path to the Word template (.docx) containing placeholders in double braces.
    output_path: str
        Destination path for the populated document.
    """
    replacements = {f"{{{{{key}}}}}": ("" if value is None else str(value)) for key, value in values.items()}

    with zipfile.ZipFile(template_path) as zin:
        with zipfile.ZipFile(output_path, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/document.xml":
                    data = _replace_placeholders_in_xml(data, replacements)
                zout.writestr(item, data)
