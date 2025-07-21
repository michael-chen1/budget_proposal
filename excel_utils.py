from openpyxl import load_workbook

def populate_template(extracted: dict, template_path: str, output_path: str):
    """
    Open the Excel file at `template_path`, and for each key in `extracted`,
    write its value into the single cell defined by the named range of the same name.
    """
    wb = load_workbook(template_path)
    
    for key, value in extracted.items():
        # Skip keys that aren't defined names in the workbook
        if key not in wb.defined_names:
            continue

        # Grab the DefinedName object, then its single destination
        dn = wb.defined_names[key]
        for sheet_name, coord in dn.destinations:
            ws = wb[sheet_name]
            ws[coord] = value

    wb.save(output_path)
