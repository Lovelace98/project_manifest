import re
import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side
from notion_service import sort_manifest_cases

def sanitize_sheet_title(title):
    """Sanitize sheet title for Excel (max 30 characters, no invalid chars)."""
    if not title:
        return "Sheet"
    # Replace invalid chars: \ / ? * : [ ] with '-'
    sanitized = re.sub(r'[\\/\?\*\:\[\]]', '-', title)
    # Truncate to 30 chars
    return sanitized[:30]

def create_excel_manifest(cases, output_path, auto_sort=False):
    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active
    wb.remove(default_sheet)
    
    used_titles = set()
    
    # Define styles
    arial_font = Font(name="Arial", size=12, bold=False)
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    
    thin_border_side = Side(style="thin", color="000000")
    thin_border = Border(
        left=thin_border_side,
        right=thin_border_side,
        top=thin_border_side,
        bottom=thin_border_side
    )
    
    cases_to_export = sort_manifest_cases(cases, ascending=True) if auto_sort else cases
    for c_idx, case in enumerate(cases_to_export):
        # Determine unique sheet name
        bdo_tag = case.get('bdo_tag', f"CASE-{c_idx+1}")
        sheet_title = sanitize_sheet_title(bdo_tag)
        
        # Resolve duplicates
        base_title = sheet_title
        suffix = 1
        while sheet_title.upper() in used_titles:
            suffix_str = f"-{suffix}"
            sheet_title = base_title[:30 - len(suffix_str)] + suffix_str
            suffix += 1
            
        used_titles.add(sheet_title.upper())
        ws = wb.create_sheet(title=sheet_title)
        
        # Configure grid lines visibility
        ws.sheet_view.showGridLines = True
        
        # Set column widths
        ws.column_dimensions['A'].width = 20
        ws.column_dimensions['B'].width = 70
        
        # Row heights
        ws.row_dimensions[1].height = 15
        ws.row_dimensions[2].height = 20
        ws.row_dimensions[3].height = 20
        ws.row_dimensions[4].height = 20
        ws.row_dimensions[5].height = 15
        ws.row_dimensions[6].height = 15
        
        # A2: Reference Number: | B2: BDO Tag
        ws.cell(row=2, column=1, value="Reference Number:").font = arial_font
        ws.cell(row=2, column=1).alignment = left_align
        
        b2_val = str(case.get('bdo_tag', '')).strip().upper()
        ws.cell(row=2, column=2, value=b2_val).font = arial_font
        ws.cell(row=2, column=2).alignment = left_align
        
        # A3: Test type: | B3: Test Type
        ws.cell(row=3, column=1, value="Test type:").font = arial_font
        ws.cell(row=3, column=1).alignment = left_align
        
        b3_val = str(case.get('test_type', '')).strip().upper()
        ws.cell(row=3, column=2, value=b3_val).font = arial_font
        ws.cell(row=3, column=2).alignment = left_align
        
        # A4: Special Requests: | B4: Special Requests
        ws.cell(row=4, column=1, value="Special Requests:").font = arial_font
        ws.cell(row=4, column=1).alignment = left_align
        
        b4_val = str(case.get('special_requests', 'None')).strip()
        # Ensure sentence case or all caps as requested. For None, user used 'None' or empty, but let's make it ALL CAPS or None.
        if b4_val.upper() in ["NONE", ""]:
            b4_val = "None"
        else:
            b4_val = b4_val.upper()
        ws.cell(row=4, column=2, value=b4_val).font = arial_font
        ws.cell(row=4, column=2).alignment = left_align
        
        # Participants loop
        participants = case.get('participants', [])
        for p_idx, part in enumerate(participants):
            start_row = 7 + p_idx * 5
            
            ws.row_dimensions[start_row].height = 20
            ws.row_dimensions[start_row + 1].height = 20
            ws.row_dimensions[start_row + 2].height = 20
            ws.row_dimensions[start_row + 3].height = 20
            ws.row_dimensions[start_row + 4].height = 15  # Empty row height
            
            # Row start_row: Role
            c_role_lbl = ws.cell(row=start_row, column=1, value="Role")
            c_role_lbl.font = arial_font
            c_role_lbl.alignment = center_align
            c_role_lbl.border = thin_border
            
            c_role_val = ws.cell(row=start_row, column=2, value=str(part.get('role', 'CHILD')).strip().upper())
            c_role_val.font = arial_font
            c_role_val.alignment = left_align
            c_role_val.border = thin_border
            
            # Row start_row + 1: Name
            c_name_lbl = ws.cell(row=start_row + 1, column=1, value="Name")
            c_name_lbl.font = arial_font
            c_name_lbl.alignment = center_align
            c_name_lbl.border = thin_border
            
            c_name_val = ws.cell(row=start_row + 1, column=2, value=str(part.get('name', '')).strip().upper())
            c_name_val.font = arial_font
            c_name_val.alignment = left_align
            c_name_val.border = thin_border
            
            # Row start_row + 2: Date of Birth
            c_dob_lbl = ws.cell(row=start_row + 2, column=1, value="Date of Birth")
            c_dob_lbl.font = arial_font
            c_dob_lbl.alignment = center_align
            c_dob_lbl.border = thin_border
            
            # Keep as string format DDMMYYYY or '-'
            dob_val = str(part.get('dob', '-')).strip()
            c_dob_val = ws.cell(row=start_row + 2, column=2, value=dob_val)
            c_dob_val.font = arial_font
            c_dob_val.alignment = left_align
            c_dob_val.border = thin_border
            
            # Row start_row + 3: Ethnicity
            c_eth_lbl = ws.cell(row=start_row + 3, column=1, value="Ethnicity")
            c_eth_lbl.font = arial_font
            c_eth_lbl.alignment = center_align
            c_eth_lbl.border = thin_border
            
            c_eth_val = ws.cell(row=start_row + 3, column=2, value=str(part.get('ethnicity', 'AFRICAN')).strip().upper())
            c_eth_val.font = arial_font
            c_eth_val.alignment = left_align
            c_eth_val.border = thin_border

    wb.save(output_path)
