import re
import docx

def clean_dob(dob_str):
    """Clean the DOB string by removing slashes and formatting as DDMMYYYY.
    Pads single digit days and months to ensure an 8-character format.
    Returns '-' if empty or invalid.
    """
    if not dob_str:
        return "-"
    dob_str = dob_str.strip()
    if not dob_str or dob_str == "()":
        return "-"
    
    # Try to split on common delimiters (slash, dash, dot)
    parts = re.split(r'[-/.]', dob_str)
    if len(parts) == 3:
        day, month, year = [p.strip() for p in parts]
        if day.isdigit() and month.isdigit() and year.isdigit():
            # Pad day and month with leading zeros
            day = day.zfill(2)
            month = month.zfill(2)
            # Standardize 2-digit years to 4-digit years
            if len(year) == 2:
                year = "19" + year if int(year) >= 30 else "20" + year
            if len(day) == 2 and len(month) == 2 and len(year) == 4:
                return f"{day}{month}{year}"
                
    # Fallback: keep only numeric digits
    digits = re.sub(r'[^0-9]', '', dob_str)
    if len(digits) == 8:
        return digits
    return digits if digits else "-"

def parse_participant_string(part_str):
    """Parse a single participant string e.g., 'ISAAC AMGBOME (10/05/1998)'
    into (name, dob).
    """
    part_str = part_str.strip()
    # Find parenthesis with content at the end of the string
    match = re.search(r'\s*\(([^)]*)\)\s*$', part_str)
    if match:
        dob = match.group(1).strip()
        name = part_str[:match.start()].strip()
    else:
        name = part_str
        dob = ""
    
    # Clean up name (remove extra whitespace)
    name = re.sub(r'\s+', ' ', name).upper()
    return name, clean_dob(dob)

def parse_docx(file_path):
    """Parse a docx file and return a list of cases."""
    doc = docx.Document(file_path)
    cases = []
    row_count = 0
    
    current_section = "Main Table"
    
    # Process both paragraphs and tables in order if possible, or just scan them.
    # To keep section headings, we can iterate through document elements.
    for element in doc.element.body:
        if element.tag.endswith('p'):
            p = docx.text.paragraph.Paragraph(element, doc)
            text = p.text.strip()
            if text and ("SSK" in text.upper() or "MAIN" in text.upper() or "CASES" in text.upper()):
                current_section = text
        elif element.tag.endswith('tbl'):
            tbl = docx.table.Table(element, doc)
            if not tbl.rows:
                continue
                
            # Check if Row 0 is a title row (e.g., "TOTAL NO. OF CASES - 40")
            first_cell_text = tbl.rows[0].cells[0].text.strip().upper()
            is_title_row = "TOTAL" in first_cell_text or len(tbl.rows[0].cells) == 1
            
            # Check if there is a header row
            has_header_row = False
            header_row_idx = 1 if is_title_row else 0
            
            if len(tbl.rows) > header_row_idx:
                row_to_check = tbl.rows[header_row_idx]
                cell_texts = [c.text.strip().upper() for c in row_to_check.cells]

                def normalize_header_text(txt):
                    return re.sub(r'[^A-Z0-9 ]', ' ', txt)

                def has_header_word(txt, keywords):
                    txt = normalize_header_text(txt)
                    return any(re.search(rf'\b{re.escape(keyword)}\b', txt) for keyword in keywords)

                def is_header_row_candidate(cell_texts):
                    name_header = any(has_header_word(txt, ['NAME', 'PARTICIPANT', 'PARTICIPANTS']) for txt in cell_texts)
                    bdo_header = any(has_header_word(txt, ['BDO', 'TAG', 'LAB']) for txt in cell_texts)
                    test_header = any(has_header_word(txt, ['TEST', 'TYPE']) for txt in cell_texts)
                    status_header = any(has_header_word(txt, ['STATUS']) for txt in cell_texts)
                    # Require at least a name/participant header plus one other common header column
                    return name_header and (bdo_header or test_header or status_header)

                if is_header_row_candidate(cell_texts):
                    has_header_row = True
            
            # One-row tables should be treated as data-only unless they are a title row plus header.
            if len(tbl.rows) == 1 and not is_title_row:
                has_header_row = False

            if is_title_row and has_header_row:
                start_row_idx = 2
                header_row = tbl.rows[1]
            elif has_header_row:
                start_row_idx = 1
                header_row = tbl.rows[0]
            else:
                start_row_idx = 0
                header_row = None

            # Map column indices
            col_indices = {'participants': 0, 'bdo': 1, 'test_type': 2, 'status': 3}
            if header_row:
                def normalize_header_text(txt):
                    return re.sub(r'[^A-Z0-9 ]', ' ', txt)

                def has_header_word(txt, keywords):
                    txt = normalize_header_text(txt.upper())
                    return any(re.search(rf'\b{re.escape(keyword)}\b', txt) for keyword in keywords)

                for i, cell in enumerate(header_row.cells):
                    cell_text = cell.text.strip()
                    if has_header_word(cell_text, ['PARTICIPANTS', 'PARTICIPANT', 'NAME']):
                        col_indices['participants'] = i
                    elif has_header_word(cell_text, ['BDO', 'TAG', 'LAB']):
                        col_indices['bdo'] = i
                    elif has_header_word(cell_text, ['TEST', 'TYPE']):
                        col_indices['test_type'] = i
                    elif has_header_word(cell_text, ['STATUS']):
                        col_indices['status'] = i

            # Process data rows
            for r_idx in range(start_row_idx, len(tbl.rows)):
                row = tbl.rows[r_idx]
                cells = row.cells
                
                # Make sure we don't index out of bounds for the minimum required columns (participants and BDO tag)
                min_cols = max(col_indices['participants'], col_indices['bdo'])
                if len(cells) <= min_cols:
                    continue
                
                raw_parts = cells[col_indices['participants']].text.strip()
                raw_bdo = cells[col_indices['bdo']].text.strip()
                if not raw_parts or "NAME OF PARTICIPANTS" in raw_parts.upper():
                    continue
                row_count += 1
                
                # Safe read for optional test type and status columns
                raw_test_type = ""
                if 'test_type' in col_indices and len(cells) > col_indices['test_type']:
                    raw_test_type = cells[col_indices['test_type']].text.strip()
                    
                raw_status = ""
                if 'status' in col_indices and len(cells) > col_indices['status']:
                    raw_status = cells[col_indices['status']].text.strip()
                
                if not raw_parts or "NAME OF PARTICIPANTS" in raw_parts.upper():
                    continue  # Skip header-like rows inside the table if any
                
                # Split by "&"
                part_strings = raw_parts.split('&')
                parsed_parts = []
                
                # Determine test category
                raw_test_type_upper = raw_test_type.upper()
                is_y_chrom = any(x in raw_test_type_upper for x in ["Y CHROM", "Y-CHROM", "Y STR", "Y-STR", "YSTR"])
                is_grandparent = any(x in raw_test_type_upper for x in ["GRANDPARENT", "GRAND PARENT"])
                is_maternity = "MATERNITY" in raw_test_type_upper
                is_paternity = "PATERNITY" in raw_test_type_upper or (not is_y_chrom and not is_grandparent and not is_maternity)
                
                num_parts = len(part_strings)
                
                for idx, part_str in enumerate(part_strings):
                    name, dob = parse_participant_string(part_str)
                    if not name:
                        continue
                    
                    # Assign role dynamically
                    if is_y_chrom:
                        role = f"MALE {idx + 1}"
                    elif is_grandparent:
                        if idx == 0:
                            role = "ALLEGED GRANDPARENT"
                        elif idx == 1 and num_parts == 3:
                            role = "MOTHER"
                        else:
                            role = "GRANDCHILD"
                    elif idx == 0:
                        role = "ALLEGED MOTHER" if (is_maternity and not is_paternity) else "ALLEGED FATHER"
                    elif idx == 1 and (is_paternity and is_maternity):
                        role = "MOTHER"
                    else:
                        role = "CHILD"
                        
                    parsed_parts.append({
                        'name': name.upper(),
                        'dob': dob,
                        'role': role,
                        'ethnicity': "AFRICAN"
                    })
                
                # Determine default Excel test type mapping
                bdo_upper = raw_bdo.upper()
                is_legal = any(k in bdo_upper for k in ["IM", "OVCD", "WWCD", "OVIM", "WWIM"]) or "LEGAL" in raw_test_type_upper
                prefix = "LEGAL" if is_legal else "PRIVATE"

                if is_paternity and is_maternity:
                    excel_test_type = f"{prefix} PATERNITY + MATERNITY"
                elif is_y_chrom:
                    excel_test_type = f"{prefix} Y CHROMOSOME TEST"
                elif is_grandparent:
                    excel_test_type = f"{prefix} GRANDPARENTAGE TEST"
                elif is_maternity:
                    excel_test_type = f"{prefix} MATERNITY"
                else:
                    excel_test_type = f"{prefix} PATERNITY"
                
                # Map special requests: if status contains anything else besides just "IN"
                status_clean = raw_status.upper().strip()
                status_clean = re.sub(r'\s+', ' ', status_clean)
                special_requests_val = "NONE"
                if status_clean and status_clean != "IN":
                    if status_clean.startswith("IN "):
                        special_requests_val = status_clean[3:].strip()
                    else:
                        special_requests_val = status_clean
                
                # Ensure parents always precede children: Father -> Mother -> Children
                from notion_service import get_role_sort_rank, sort_manifest_cases
                sorted_parts = sorted(parsed_parts, key=lambda p: get_role_sort_rank(p.get('role')))

                cases.append({
                    'section': current_section,
                    'bdo_tag': raw_bdo.upper(),
                    'test_type': excel_test_type,
                    'special_requests': special_requests_val,
                    'participants': sorted_parts
                })
                
    return {
        'cases': sort_manifest_cases(cases, ascending=True),
        'row_count': row_count
    }

if __name__ == "__main__":
    # Test execution
    import sys
    test_file = "/Users/ghost/Documents/Project Manifest/2ND JUNE GERMANY NAME OF PARTICIPANTS 327 GHANA & FOREIGN.docx"
    parsed_cases = parse_docx(test_file)
    print(f"Successfully parsed {len(parsed_cases)} cases:")
    for i, c in enumerate(parsed_cases[:3]):
        print(f"Case {i+1}: Tag={c['bdo_tag']} | Type={c['test_type']} | Section={c['section']}")
        for p in c['participants']:
            print(f"  - {p['role']}: {p['name']} ({p['dob']})")
