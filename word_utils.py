import zipfile


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
                    xml = data.decode("utf-8")
                    for placeholder, replacement in replacements.items():
                        if placeholder in xml:
                            xml = xml.replace(placeholder, replacement)
                    data = xml.encode("utf-8")
                zout.writestr(item, data)

