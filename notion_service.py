import os
import re
import time
import concurrent.futures
import httpx
from dotenv import load_dotenv

load_dotenv()

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

def get_notion_headers(api_key=None):
    token = api_key or os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")
    if not token:
        raise ValueError("Missing NOTION_API_KEY / NOTION_TOKEN. Please provide an API key or configure it in the environment.")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json"
    }

def format_notion_dob(dob_raw):
    """Format Notion date or string (e.g. '2000-07-14' or '14/07/2000') into 'DDMMYYYY'.
    Returns '-' if empty or invalid.
    """
    if not dob_raw:
        return "-"
    dob_str = str(dob_raw).strip()
    if not dob_str or dob_str == "()":
        return "-"
    
    # Check if ISO format YYYY-MM-DD
    iso_match = re.match(r'^(\d{4})[-/.](\d{2})[-/.](\d{2})', dob_str)
    if iso_match:
        year, month, day = iso_match.groups()
        return f"{day}{month}{year}"
    
    # Check if DD/MM/YYYY or DD-MM-YYYY
    parts = re.split(r'[-/.]', dob_str)
    if len(parts) == 3:
        day, month, year = [p.strip() for p in parts]
        if day.isdigit() and month.isdigit() and year.isdigit():
            day = day.zfill(2)
            month = month.zfill(2)
            if len(year) == 2:
                year = "19" + year if int(year) >= 30 else "20" + year
            if len(day) == 2 and len(month) == 2 and len(year) == 4:
                return f"{day}{month}{year}"
                
    digits = re.sub(r'[^0-9]', '', dob_str)
    if len(digits) == 8:
        return digits
    return digits if digits else "-"

def extract_prop_text(prop):
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "title":
        return "".join([t.get("plain_text", "") for t in prop.get("title", [])]).strip()
    if ptype == "rich_text":
        return "".join([t.get("plain_text", "") for t in prop.get("rich_text", [])]).strip()
    if ptype == "formula":
        f = prop.get("formula", {})
        ftype = f.get("type")
        return str(f.get(ftype, "")).strip() if ftype else ""
    return ""

def extract_prop_select(prop):
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "").strip() if sel else ""
    return ""

def extract_prop_date(prop):
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "date":
        d = prop.get("date")
        return d.get("start", "").strip() if d else ""
    return ""

def extract_prop_boolean(prop):
    if not prop:
        return False
    ptype = prop.get("type")
    if ptype == "checkbox":
        return bool(prop.get("checkbox", False))
    if ptype == "formula":
        f = prop.get("formula", {})
        return bool(f.get("boolean", False))
    return False

def extract_prop_rollup_lab(prop):
    """Extract receiving lab from rollup property or array."""
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "rollup":
        r = prop.get("rollup", {})
        rtype = r.get("type")
        if rtype == "array":
            items = r.get("array", [])
            for item in items:
                itype = item.get("type")
                if itype == "select":
                    name = item.get("select", {}).get("name", "")
                    if name:
                        return name.strip().upper()
                elif itype == "rich_text":
                    txt = "".join([t.get("plain_text", "") for t in item.get("rich_text", [])])
                    if txt:
                        return txt.strip().upper()
                elif itype == "title":
                    txt = "".join([t.get("plain_text", "") for t in item.get("title", [])])
                    if txt:
                        return txt.strip().upper()
                elif itype == "string":
                    val = item.get("string", "")
                    if val:
                        return val.strip().upper()
        elif rtype == "string":
            return r.get("string", "").strip().upper()
    return ""

def extract_prop_rollup_status(prop):
    """Extract case status name from rollup property or array."""
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "rollup":
        r = prop.get("rollup", {})
        rtype = r.get("type")
        if rtype == "array":
            items = r.get("array", [])
            for item in items:
                itype = item.get("type")
                if itype == "select":
                    name = item.get("select", {}).get("name", "")
                    if name:
                        return name.strip()
                elif itype == "status":
                    name = item.get("status", {}).get("name", "")
                    if name:
                        return name.strip()
                elif itype == "rich_text":
                    txt = "".join([t.get("plain_text", "") for t in item.get("rich_text", [])])
                    if txt:
                        return txt.strip()
        elif rtype == "string":
            return r.get("string", "").strip()
    elif ptype == "select":
        return prop.get("select", {}).get("name", "").strip()
    elif ptype == "status":
        return prop.get("status", {}).get("name", "").strip()
    return ""

def parse_part_number(entry_part_str):
    """Convert 'Part 1', 'Part 2', etc. to integer (default 1)."""
    if not entry_part_str:
        return 1
    match = re.search(r'\d+', entry_part_str)
    return int(match.group(0)) if match else 1

def process_raw_page(page):
    """Extract relevant fields and participants from a single Notion page."""
    props = page.get("properties", {})
    
    # Entry ID (e.g. PL-58)
    entry_id_obj = props.get("Entry ID", {}).get("unique_id", {})
    prefix = entry_id_obj.get("prefix", "PL")
    num = entry_id_obj.get("number", "")
    entry_id = f"{prefix}-{num}" if num else page.get("id")
    
    case_code = extract_prop_text(props.get("Case Code")).upper()
    manifest_test_type = extract_prop_text(props.get("Manifest Test Type")).upper()
    entry_part = extract_prop_select(props.get("Entry Part")) or "Part 1"
    part_num = parse_part_number(entry_part)
    special_requests = extract_prop_text(props.get("Special Requests"))
    receiving_lab = extract_prop_rollup_lab(props.get("Receiving Lab"))
    ready_for_manifest = extract_prop_boolean(props.get("Ready for Manifest"))
    verified_reviewed = extract_prop_boolean(props.get("Verified/Reviewed"))
    case_status = extract_prop_rollup_status(props.get("Case Status")) or "Manifest Awaiting"
    
    # Extract related Case page IDs from Case relation property
    case_relations = props.get("Case", {}).get("relation", [])
    case_page_ids = [rel["id"] for rel in case_relations if isinstance(rel, dict) and "id" in rel]
    
    # Extract participants in this row
    participants = []
    
    # P1 (Lead / Father in Part 1; Child in Part 2, Part 3, Part 4, etc.)
    p1_name = extract_prop_text(props.get("P1 Name (Father or Lead)")).upper()
    if p1_name:
        p1_dob_raw = extract_prop_date(props.get("P1 Date of Birth"))
        p1_role_raw = extract_prop_select(props.get("P1 Role")).upper()
        if p1_role_raw and p1_role_raw not in ["FATHER", "ALLEGED FATHER"]:
            p1_role = p1_role_raw
        elif part_num > 1:
            # In Part 2, Part 3, etc., P1 holds subsequent children (e.g. 5th child, 9th child)
            p1_role = "CHILD"
        else:
            p1_role = p1_role_raw or "ALLEGED FATHER"

        p1_ethnicity = extract_prop_select(props.get("P1 Ethnicity")).upper() or "AFRICAN"
        participants.append({
            "name": p1_name,
            "dob": format_notion_dob(p1_dob_raw),
            "role": p1_role,
            "ethnicity": p1_ethnicity
        })
        
    # P2, P3, P4
    for idx in range(2, 5):
        p_name = extract_prop_text(props.get(f"P{idx} Name")).upper()
        if p_name:
            p_dob_raw = extract_prop_date(props.get(f"P{idx} Date of Birth"))
            p_role = extract_prop_select(props.get(f"P{idx} Role")).upper() or "CHILD"
            p_ethnicity = extract_prop_select(props.get(f"P{idx} Ethnicity")).upper() or "AFRICAN"
            participants.append({
                "name": p_name,
                "dob": format_notion_dob(p_dob_raw),
                "role": p_role,
                "ethnicity": p_ethnicity
            })
            
    return {
        "page_id": page.get("id"),
        "entry_id": entry_id,
        "case_code": case_code,
        "manifest_test_type": manifest_test_type,
        "entry_part": entry_part,
        "part_num": part_num,
        "special_requests": special_requests,
        "receiving_lab": receiving_lab,
        "ready_for_manifest": ready_for_manifest,
        "verified_reviewed": verified_reviewed,
        "case_status": case_status,
        "case_page_ids": case_page_ids,
        "participants": participants
    }

def get_role_sort_rank(role_str):
    """
    Sort hierarchy for participants within a case:
    Rank 1: Alleged Father / Father / Lead Male Relative
    Rank 2: Mother / Alleged Mother / Mother (Not Alleged)
    Rank 3: Other Adult Relatives / Grandparents / Male 1
    Rank 4: Children / Grandchildren / Twins / Dependents
    """
    role = (role_str or "").upper().strip()
    if any(k in role for k in ["FATHER", "ALLEGED FATHER", "PATERNAL"]):
        return 1
    if any(k in role for k in ["MOTHER", "ALLEGED MOTHER", "MATERNAL"]):
        return 2
    if any(k in role for k in ["GRANDPARENT", "MALE 1", "MALE RELATIVE", "RELATIVE"]):
        return 3
    return 4

def merge_multipart_cases(processed_rows, default_test_type="PRIVATE PATERNITY"):
    """
    Groups entries by Case Code.
    When a case has >4 participants split across Part 1, Part 2, Part 3, etc.:
    - Sorts parts dynamically by part_num ascending (no cap: Part 1, Part 2, Part 3, Part 4, ...).
    - Sequentially appends participants:
        Part 1: Father (P1), Child 1 (P2), Child 2 (P3), Child 3 (P4)
        Part 2: Child 4 (P1), Child 5 (P2), Child 6 (P3), Child 7 (P4)
        Part 3: Child 8 (P1), Child 9 (P2), Child 10 (P3), Child 11 (P4)
    - Normalizes participant order so parents always precede children:
        Father -> Mother -> Children.
    - Merges into one single case object for the Excel manifest.
    """
    groups = {}
    for row in processed_rows:
        code = row["case_code"].strip()
        # Group by Case Code. If Case Code is blank, group by entry_id to avoid accidental merging
        key = code if code else f"PENDING-{row['entry_id']}"
        if key not in groups:
            groups[key] = []
        groups[key].append(row)
        
    consolidated_cases = []
    
    for key, rows in groups.items():
        # Sort by Part number (Part 1, Part 2, Part 3, ...) without any cap
        rows_sorted = sorted(rows, key=lambda r: r["part_num"])
        primary = rows_sorted[0]
        
        # Combine participants in sequential order across all parts
        merged_participants = []
        for r in rows_sorted:
            # For parts > 1, make sure any P1 that might still have FATHER role is normalized to CHILD
            if r["part_num"] > 1:
                for p in r["participants"]:
                    if p["role"] in ["FATHER", "ALLEGED FATHER"]:
                        p["role"] = "CHILD"
            merged_participants.extend(r["participants"])
            
        # Stable sort participants so parents come first, followed by children:
        # Alleged Father (Rank 1) -> Mother (Rank 2) -> Relatives (Rank 3) -> Children (Rank 4)
        merged_participants.sort(key=lambda p: get_role_sort_rank(p.get("role")))
            
        # Determine test type
        test_type = primary["manifest_test_type"]
        if not test_type:
            bdo_tag = primary["case_code"] or primary["entry_id"]
            if "IM" in bdo_tag.upper():
                test_type = "LEGAL PATERNITY"
            else:
                test_type = default_test_type
                
        special_req = "None"
        for r in rows_sorted:
            if r["special_requests"] and r["special_requests"].upper() not in ["NONE", "IN", ""]:
                special_req = r["special_requests"].upper()
                break
                
        # Receiving lab
        rec_lab = "UNKNOWN"
        for r in rows_sorted:
            if r["receiving_lab"]:
                rec_lab = r["receiving_lab"].upper()
                break
                
        # Determine case status and ready_for_manifest
        case_status = primary.get("case_status") or "Manifest Awaiting"
        for r in rows_sorted:
            if r.get("case_status"):
                case_status = r["case_status"]
                break
                
        is_ready = all(r.get("ready_for_manifest", False) for r in rows_sorted) if rows_sorted else False
        parts_merged = [r["entry_part"] for r in rows_sorted]
        
        # Collect page IDs and related Case database IDs across all parts
        page_ids = [r["page_id"] for r in rows_sorted if r.get("page_id")]
        case_page_ids = []
        for r in rows_sorted:
            for cid in r.get("case_page_ids", []):
                if cid and cid not in case_page_ids:
                    case_page_ids.append(cid)
        
        consolidated_cases.append({
            "section": "Notion Manifest Batch",
            "bdo_tag": primary["case_code"] if primary["case_code"] else primary["entry_id"],
            "test_type": test_type,
            "special_requests": special_req,
            "receiving_lab": rec_lab,
            "participants": merged_participants,
            "entry_id": primary["entry_id"],
            "parts_merged": parts_merged,
            "is_multipart": len(rows_sorted) > 1,
            "participant_count": len(merged_participants),
            "case_status": case_status,
            "ready_for_manifest": is_ready,
            "page_ids": page_ids,
            "case_page_ids": case_page_ids
        })
        
    return consolidated_cases

def query_notion_database(api_key=None, database_id=None, filter_mode="manifest_batch"):
    """
    Query Notion database with pagination.
    filter_mode:
      - 'manifest_batch': queries Case Status == 'Manifest Awaiting' (recommended safe batch mode)
      - 'ready': queries Ready for Manifest == True
      - 'verified': queries Verified/Reviewed == True
      - 'all': queries all database pages
    """
    token = api_key or os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")
    db_id = database_id or os.getenv("NOTION_DATABASE_ID") or os.getenv("NOTION_PARTICIPANT_DB_ID") or os.getenv("NOTION_CASES_DB_ID")
    if not db_id:
        raise ValueError("Missing NOTION_DATABASE_ID / NOTION_PARTICIPANT_DB_ID. Please configure it in the environment.")
    headers = get_notion_headers(token)
    
    url = f"{NOTION_BASE_URL}/databases/{db_id}/query"
    all_pages = []
    has_more = True
    start_cursor = None
    
    payload = {"page_size": 100}
    if filter_mode == "manifest_batch":
        payload["filter"] = {
            "property": "Case Status",
            "rollup": {
                "any": {
                    "select": {
                        "equals": "Manifest Awaiting"
                    }
                }
            }
        }
    elif filter_mode == "ready":
        payload["filter"] = {
            "property": "Ready for Manifest",
            "formula": {"checkbox": {"equals": True}}
        }
    elif filter_mode == "verified":
        payload["filter"] = {
            "property": "Verified/Reviewed",
            "checkbox": {"equals": True}
        }
        
    with httpx.Client(timeout=30.0) as client:
        while has_more:
            if start_cursor:
                payload["start_cursor"] = start_cursor
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Notion API error ({resp.status_code}): {resp.text}")
            data = resp.json()
            all_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
            
    return all_pages

import time

_manifest_cache = {
    "data": None,
    "timestamp": 0,
    "filter_mode": None
}

def clear_manifest_cache():
    global _manifest_cache
    _manifest_cache.update({"data": None, "timestamp": 0, "filter_mode": None})

def fetch_manifest_cases(api_key=None, database_id=None, filter_mode="manifest_batch", lab_filter=None, force_refresh=False):
    """
    Main entry point: Queries Notion with in-memory caching to shield Notion API quota.
    Caches results for NOTION_CACHE_TTL_SECONDS (default 180s) unless force_refresh is True.
    """
    global _manifest_cache
    cache_ttl = int(os.getenv("NOTION_CACHE_TTL_SECONDS", 180))
    now = time.time()
    
    # Check cache if valid, not forcing refresh, and same filter_mode
    if not force_refresh and _manifest_cache["data"] is not None and (now - _manifest_cache["timestamp"] < cache_ttl) and (_manifest_cache["filter_mode"] == filter_mode):
        consolidated = _manifest_cache["data"]
        from_cache = True
    else:
        raw_pages = query_notion_database(api_key=api_key, database_id=database_id, filter_mode=filter_mode)
        processed_rows = [process_raw_page(p) for p in raw_pages]
        consolidated = merge_multipart_cases(processed_rows)
        _manifest_cache = {
            "data": consolidated,
            "timestamp": now,
            "filter_mode": filter_mode
        }
        from_cache = False
    
    # Extract unique receiving labs
    labs = sorted(list({c["receiving_lab"] for c in consolidated if c.get("receiving_lab") and c["receiving_lab"] != "UNKNOWN"}))
    
    # Filter by lab if requested
    if lab_filter and lab_filter.upper() != "ALL":
        filtered = [c for c in consolidated if c.get("receiving_lab", "").upper() == lab_filter.upper()]
    else:
        filtered = consolidated
        
    return {
        "cases": filtered,
        "available_labs": labs,
        "total_cases": len(filtered),
        "unfiltered_count": len(consolidated),
        "from_cache": from_cache
    }

def update_single_case_status(client, headers, case_id, target_status="Dispatched to Laboratory", max_retries=3):
    """Update Status select property for a single Case page in Notion."""
    url = f"{NOTION_BASE_URL}/pages/{case_id}"
    payload = {
        "properties": {
            "Status": {
                "select": {
                    "name": target_status
                }
            }
        }
    }
    
    for attempt in range(max_retries):
        try:
            resp = client.patch(url, headers=headers, json=payload, timeout=20.0)
            if resp.status_code == 200:
                return {"case_id": case_id, "success": True}
            elif resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 1.0))
                time.sleep(retry_after + 0.25)
                continue
            else:
                return {
                    "case_id": case_id,
                    "success": False,
                    "error": f"HTTP {resp.status_code}: {resp.text}"
                }
        except Exception as e:
            if attempt == max_retries - 1:
                return {"case_id": case_id, "success": False, "error": str(e)}
            time.sleep(0.5 * (attempt + 1))
            
    return {"case_id": case_id, "success": False, "error": "Max retries exceeded"}

def update_case_status_in_notion(case_page_ids, target_status="Dispatched to Laboratory", api_key=None, max_workers=3):
    """
    Updates the Status select property in Notion for all provided Case page IDs.
    Uses concurrency with rate limiting to respect Notion API thresholds,
    and invalidates the in-memory manifest cache upon completion.
    """
    token = api_key or os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")
    headers = get_notion_headers(token)
    
    unique_ids = list(dict.fromkeys([cid for cid in case_page_ids if cid]))
    if not unique_ids:
        return {"total": 0, "updated": 0, "failed": 0, "failed_cases": []}
        
    updated_count = 0
    failed_cases = []
    
    with httpx.Client(timeout=30.0) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(update_single_case_status, client, headers, cid, target_status)
                for cid in unique_ids
            ]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res.get("success"):
                    updated_count += 1
                else:
                    failed_cases.append(res)
                    
    # Invalidate cache so subsequent fetches reflect the new statuses in Notion
    clear_manifest_cache()
    
    return {
        "total": len(unique_ids),
        "updated": updated_count,
        "failed": len(failed_cases),
        "failed_cases": failed_cases
    }


