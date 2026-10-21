import os
import re
import time
import logging
import random
import threading
import concurrent.futures
from datetime import datetime, date
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

class NotionRateLimiter:
    """
    Thread-safe rate limiter to strictly respect Notion's API quota
    (average 3 requests per second per token).
    Paces outgoing requests to ~2.5 requests per second across all threads,
    preventing 429 Too Many Requests spikes during concurrent batch processing.
    """
    def __init__(self, requests_per_second: float = 2.5):
        self.interval = 1.0 / requests_per_second
        self.lock = threading.Lock()
        self.last_time = 0.0

    def acquire(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_time
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self.last_time = time.time()

global_notion_limiter = NotionRateLimiter(requests_per_second=2.5)


def execute_notion_request(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict,
    payload: dict | None = None,
    files: dict | None = None,
    timeout: float = 30.0,
    max_retries: int = 5,
) -> httpx.Response:
    """
    Executes an HTTP request to Notion API with:
    1. Global pacing to stay under Notion's 3 req/sec rate limit.
    2. Automatic HTTP 429 backoff reading 'Retry-After' header + jitter.
    3. Automatic 502/503/504 transient error exponential backoff.
    """
    method_upper = method.upper()
    for attempt in range(max_retries):
        global_notion_limiter.acquire()
        try:
            req_kwargs = {"headers": headers, "timeout": timeout}
            if payload is not None:
                req_kwargs["json"] = payload
            if files is not None:
                req_kwargs["files"] = files

            caller = getattr(client, method_upper.lower(), None)
            if callable(caller):
                resp = caller(url, **req_kwargs)
            else:
                resp = client.request(method_upper, url, **req_kwargs)

            # Check 429 Rate Limit
            if resp.status_code == 429:
                retry_header = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
                try:
                    retry_after = float(retry_header) if retry_header else 1.5 * (attempt + 1)
                except (ValueError, TypeError):
                    retry_after = 1.5 * (attempt + 1)
                sleep_duration = retry_after + random.uniform(0.2, 0.5)
                logger.warning(
                    "Notion API 429 Rate Limit encountered for %s %s. Pausing %.2fs before retry (%d/%d)...",
                    method_upper, url, sleep_duration, attempt + 1, max_retries
                )
                time.sleep(sleep_duration)
                continue

            # Check 502/503/504 transient cloud errors
            if resp.status_code in (502, 503, 504) and attempt < max_retries - 1:
                sleep_duration = (1.0 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                logger.warning(
                    "Notion API transient error %d for %s %s. Retrying in %.2fs (%d/%d)...",
                    resp.status_code, method_upper, url, sleep_duration, attempt + 1, max_retries
                )
                time.sleep(sleep_duration)
                continue

            return resp
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt == max_retries - 1:
                logger.error("Notion API network error on attempt %d: %s", attempt + 1, exc)
                raise
            sleep_duration = 0.5 * (attempt + 1)
            time.sleep(sleep_duration)

    raise RuntimeError(f"Exceeded max retries ({max_retries}) for Notion API {method_upper} {url}")


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
    if ptype == "title" or ("title" in prop and ptype is None):
        return "".join([t.get("plain_text", "") for t in prop.get("title", [])]).strip()
    if ptype == "rich_text" or ("rich_text" in prop and ptype is None):
        return "".join([t.get("plain_text", "") for t in prop.get("rich_text", [])]).strip()
    if ptype == "formula" or ("formula" in prop and ptype is None):
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
            names: list[str] = []
            for item in items:
                itype = item.get("type")
                if itype == "select":
                    name = item.get("select", {}).get("name", "")
                    if name:
                        names.append(name.strip())
                elif itype == "status":
                    name = item.get("status", {}).get("name", "")
                    if name:
                        names.append(name.strip())
                elif itype == "rich_text":
                    txt = "".join([t.get("plain_text", "") for t in item.get("rich_text", [])])
                    if txt:
                        names.append(txt.strip())
            if names:
                # If there are multiple rolled-up statuses and any is non-'Manifest Awaiting' (e.g. 'Result Delivered'),
                # prioritize the non-'Manifest Awaiting' status so downstream can identify completed/dispatched cases
                non_awaiting = [n for n in names if n != "Manifest Awaiting"]
                return non_awaiting[0] if non_awaiting else names[0]
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
        test_type = ""
        for r in rows_sorted:
            if r.get("manifest_test_type") and r["manifest_test_type"].strip():
                test_type = r["manifest_test_type"].strip()
                break

        if not test_type:
            bdo_tag = primary.get("case_code") or primary.get("entry_id") or ""
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
        
    return sort_manifest_cases(consolidated_cases, ascending=True)

def extract_case_date_and_seq(case_tag: str):
    """
    Extracts the collection date (DDMMYY) and sequence number from a case reference tag.
    Returns: (parsed_date: date | None, seq_num: int, tag: str)
    Handles patterns such as:
      - 'CD-OMC-260526-001' -> (date(2026, 5, 26), 1, 'CD-OMC-260526-001')
      - 'CD-BDO-HOM-031026-01' -> (date(2026, 10, 3), 1, ...)
      - 'X-101026-X' -> (date(2026, 10, 10), 999999, ...)
      - 'BDO-HOM-291025-001' -> (date(2025, 10, 29), 1, ...)
      - 'PENDING-PL-58' -> (None, 58, ...)
    """
    if not case_tag:
        return (None, 999999, "")
    tag = str(case_tag).strip().upper()
    parts = tag.split("-")
    parsed_date = None
    date_idx = -1

    for i, p in enumerate(parts):
        if len(p) == 6 and p.isdigit():
            dd, mm, yy = int(p[:2]), int(p[2:4]), int(p[4:])
            full_year = 2000 + yy if yy < 70 else 1900 + yy
            try:
                parsed_date = date(full_year, mm, dd)
                date_idx = i
                break
            except ValueError:
                pass

    if not parsed_date:
        match = re.search(r"\b(\d{2})(\d{2})(\d{2})\b", tag)
        if match:
            dd, mm, yy = int(match.group(1)), int(match.group(2)), int(match.group(3))
            full_year = 2000 + yy if yy < 70 else 1900 + yy
            try:
                parsed_date = date(full_year, mm, dd)
            except ValueError:
                parsed_date = None

    seq_num = 999999
    if date_idx != -1 and date_idx + 1 < len(parts):
        seq_match = re.search(r"\d+", parts[date_idx + 1])
        if seq_match:
            seq_num = int(seq_match.group(0))
    if seq_num == 999999:
        last_num = re.search(r"(\d+)(?!.*\d)", tag)
        if last_num:
            seq_num = int(last_num.group(1))

    return (parsed_date, seq_num, tag)

def sort_manifest_cases(cases: list[dict], ascending: bool = True, sort_by: str = "date") -> list[dict]:
    """
    Sorts a list of manifest case dictionaries.
    sort_by:
      - 'date': sorts chronologically by reference date, then sequence number, then tag string.
      - 'tag': sorts alphabetically by bdo_tag / case code.
    """
    if sort_by == "tag":
        return sorted(
            cases,
            key=lambda c: str(c.get("bdo_tag") or c.get("case_code") or "").upper(),
            reverse=not ascending,
        )

    def _sort_key(c):
        tag = c.get("bdo_tag") or c.get("case_code") or c.get("entry_id") or ""
        d, s, t = extract_case_date_and_seq(tag)
        has_date = 0 if d is not None else 1
        d_val = d or (date.max if ascending else date.min)
        return (has_date, d_val, s, t)

    return sorted(cases, key=_sort_key, reverse=not ascending)

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
                "every": {
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
            resp = execute_notion_request(client, "POST", url, headers, payload=payload, timeout=30.0)
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
        if filter_mode == "manifest_batch":
            consolidated = [
                c for c in consolidated
                if c.get("case_status") == "Manifest Awaiting"
            ]
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
    try:
        resp = execute_notion_request(client, "PATCH", url, headers=headers, payload=payload, timeout=20.0, max_retries=max_retries)
        if resp.status_code == 200:
            return {"case_id": case_id, "success": True}
        return {
            "case_id": case_id,
            "success": False,
            "error": f"HTTP {resp.status_code}: {resp.text}"
        }
    except Exception as e:
        return {"case_id": case_id, "success": False, "error": str(e)}

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


def find_case_page_by_code(client, headers, case_code, cases_db_id=None, part_db_id=None):
    """
    Finds the Notion Case page ID corresponding to a given Case Code (e.g. CD-KBDO-021026-01).
    Attempts:
    1. Primary: Direct title query in the Cases database ('BDO CASES 2026').
       Title format is typically '[Name] ([Case Code])'.
    2. Fallback: Query Participant database by 'Case Code' property, then follow the 'Case' relation.
    Also handles multi-report collision suffixes (e.g. 'SS-HOM-071026-01_1' -> 'SS-HOM-071026-01').
    """
    if not case_code:
        return None

    clean_code = case_code.strip()
    base_code = re.sub(r"_\d+$", "", clean_code)
    candidates = list(dict.fromkeys([clean_code, base_code]))

    cases_db = cases_db_id or os.getenv("NOTION_CASES_DB_ID") or "382c226a-2e86-48f1-b70e-c4e9e8f62d1d"
    part_db = part_db_id or os.getenv("NOTION_DATABASE_ID") or os.getenv("NOTION_PARTICIPANT_DB_ID")

    for candidate in candidates:
        if not candidate:
            continue
        # 1. Try querying Cases DB by title containing candidate
        if cases_db:
            url = f"{NOTION_BASE_URL}/databases/{cases_db}/query"
            payload = {
                "page_size": 5,
                "filter": {
                    "property": "title",
                    "title": {
                        "contains": candidate
                    }
                }
            }
            try:
                resp = execute_notion_request(client, "POST", url, headers, payload=payload, timeout=20.0)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if results:
                        return results[0].get("id")
            except Exception:
                pass

        # 2. Fallback: Query Participant DB by Case Code (tries formula, then rich_text)
        if part_db:
            url = f"{NOTION_BASE_URL}/databases/{part_db}/query"
            payload_formula = {
                "page_size": 5,
                "filter": {
                    "property": "Case Code",
                    "formula": {
                        "string": {
                            "equals": candidate
                        }
                    }
                }
            }
            try:
                resp = execute_notion_request(client, "POST", url, headers, payload=payload_formula, timeout=20.0)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    for page in results:
                        case_rel = page.get("properties", {}).get("Case", {}).get("relation", [])
                        if case_rel:
                            return case_rel[0].get("id")
                elif resp.status_code == 400:
                    # Fallback if Case Code property is rich_text in this workspace
                    payload_rt = {
                        "page_size": 5,
                        "filter": {
                            "property": "Case Code",
                            "rich_text": {
                                "equals": candidate
                            }
                        }
                    }
                    resp_rt = execute_notion_request(client, "POST", url, headers, payload=payload_rt, timeout=20.0)
                    if resp_rt.status_code == 200:
                        results = resp_rt.json().get("results", [])
                        for page in results:
                            case_rel = page.get("properties", {}).get("Case", {}).get("relation", [])
                            if case_rel:
                                return case_rel[0].get("id")
            except Exception:
                pass

    return None


def update_cases_by_codes(case_codes, target_status="Result Review", api_key=None, max_workers=3):
    """
    Takes a list of case codes (e.g. from converted PDF reports),
    finds each case in Notion, updates its Status to target_status (default 'Result Review'),
    and returns a summary of updated, failed, and unmatched case codes.
    """
    token = api_key or os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")
    headers = get_notion_headers(token)

    # Normalize status to match Notion select option exactly
    if target_status.lower().replace(" ", "") in ["resultsreview", "resultreview"]:
        normalized_status = "Result Review"
    else:
        normalized_status = target_status

    # De-duplicate codes while preserving order
    unique_codes = list(dict.fromkeys([code.strip() for code in case_codes if code and code.strip()]))
    if not unique_codes:
        return {
            "total": 0,
            "updated": 0,
            "updated_count": 0,
            "failed": 0,
            "unmatched": 0,
            "target_status": normalized_status,
            "case_codes": [],
            "results": [],
            "failed_cases": [],
            "unmatched_codes": []
        }

    cases_db = os.getenv("NOTION_CASES_DB_ID") or "382c226a-2e86-48f1-b70e-c4e9e8f62d1d"
    part_db = os.getenv("NOTION_DATABASE_ID") or os.getenv("NOTION_PARTICIPANT_DB_ID")

    results = []
    unmatched_codes = []
    page_to_code = {}

    with httpx.Client(timeout=30.0) as client:
        # Step 1: Resolve case codes to Notion page IDs
        for code in unique_codes:
            page_id = find_case_page_by_code(client, headers, code, cases_db_id=cases_db, part_db_id=part_db)
            if page_id:
                page_to_code[page_id] = code
            else:
                unmatched_codes.append(code)

        # Step 2: Update resolved case pages in parallel
        updated_count = 0
        failed_cases = []

        if page_to_code:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_page = {
                    executor.submit(update_single_case_status, client, headers, page_id, normalized_status): page_id
                    for page_id in page_to_code
                }
                for future in concurrent.futures.as_completed(future_to_page):
                    page_id = future_to_page[future]
                    code = page_to_code[page_id]
                    res = future.result()
                    is_success = res.get("success", False)
                    if is_success:
                        updated_count += 1
                        results.append({"case_code": code, "page_id": page_id, "success": True})
                    else:
                        failed_cases.append({"case_code": code, "page_id": page_id, "error": res.get("error")})

    clear_manifest_cache()

    return {
        "total": len(unique_codes),
        "updated": updated_count,
        "updated_count": updated_count,
        "failed": len(failed_cases),
        "unmatched": len(unmatched_codes),
        "target_status": normalized_status,
        "case_codes": unique_codes,
        "results": results,
        "failed_cases": failed_cases,
        "unmatched_codes": unmatched_codes
    }


# =============================================================================
# NOTION DIRECT FILE UPLOAD & ATTACHMENT TO "CUSTODY PICS "
# =============================================================================

CUSTODY_PICS_PROPERTY = "Custody Pics "


def initiate_notion_file_upload(client: httpx.Client, headers: dict, filename: str, content_type: str = "application/pdf") -> tuple[str, str]:
    """
    Initializes a file upload session with Notion API.
    Returns (file_upload_id, upload_url).
    """
    url = f"{NOTION_BASE_URL}/file_uploads"
    init_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    init_headers["Content-Type"] = "application/json"
    payload = {"filename": filename, "content_type": content_type}
    resp = execute_notion_request(client, "POST", url, headers=init_headers, payload=payload, timeout=30.0)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Failed to initiate Notion file upload ({resp.status_code}): {resp.text}")
    data = resp.json()
    file_upload_id = data.get("id")
    upload_url = data.get("upload_url") or f"{NOTION_BASE_URL}/file_uploads/{file_upload_id}/send"
    return file_upload_id, upload_url


def send_notion_file_bytes(client: httpx.Client, headers: dict, upload_url: str, file_bytes: bytes, filename: str) -> None:
    """
    Streams binary file bytes to Notion's file upload URL.
    """
    send_headers = {
        "Authorization": headers.get("Authorization"),
        "Notion-Version": headers.get("Notion-Version", NOTION_API_VERSION),
    }
    files = {"file": (filename, file_bytes, "application/pdf")}
    resp = execute_notion_request(client, "POST", upload_url, headers=send_headers, files=files, timeout=60.0)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Failed to send file content to Notion ({resp.status_code}): {resp.text}")


def attach_report_to_case_page(
    client: httpx.Client,
    headers: dict,
    page_id: str,
    file_upload_id: str,
    filename: str,
    target_property: str = CUSTODY_PICS_PROPERTY,
    update_status: bool = True,
    target_status: str = "Result Review",
) -> dict:
    """
    Appends an uploaded file to the 'Custody Pics ' property of a Notion case page
    while preserving all preexisting documents and photos.
    Optionally updates the case status to 'Result Review'.
    """
    # 1. Fetch current page to retrieve existing files
    get_url = f"{NOTION_BASE_URL}/pages/{page_id}"
    resp = execute_notion_request(client, "GET", get_url, headers=headers, timeout=25.0)
    if resp.status_code != 200:
        raise RuntimeError(f"Could not retrieve Notion page {page_id} ({resp.status_code}): {resp.text}")

    page_data = resp.json()
    props = page_data.get("properties", {})

    # Match exact target_property or find property containing 'Custody'
    matched_prop_name = target_property
    if matched_prop_name not in props:
        for p_name in props:
            if "custody" in p_name.lower():
                matched_prop_name = p_name
                break

    existing_files = props.get(matched_prop_name, {}).get("files", [])

    # Preserve existing files, omitting any previous upload with the exact same report filename
    preserved_files = []
    for f in existing_files:
        f_name = f.get("name", "")
        if f_name == filename:
            continue
        ftype = f.get("type")
        if ftype == "file" and "file" in f:
            preserved_files.append({"name": f_name, "type": "file", "file": {"url": f["file"]["url"]}})
        elif ftype == "external" and "external" in f:
            preserved_files.append({"name": f_name, "type": "external", "external": {"url": f["external"]["url"]}})
        elif ftype == "file_upload" and "file_upload" in f:
            preserved_files.append({"name": f_name, "type": "file_upload", "file_upload": {"id": f["file_upload"]["id"]}})

    # Append new file_upload object
    new_entry = {
        "name": filename,
        "type": "file_upload",
        "file_upload": {"id": file_upload_id}
    }
    combined_files = preserved_files + [new_entry]

    properties_update: dict = {
        matched_prop_name: {
            "files": combined_files
        }
    }

    if update_status:
        status_prop = props.get("Status")
        if status_prop:
            stype = status_prop.get("type")
            if stype == "status":
                properties_update["Status"] = {"status": {"name": target_status}}
            else:
                properties_update["Status"] = {"select": {"name": target_status}}

    # 2. Patch page
    patch_url = f"{NOTION_BASE_URL}/pages/{page_id}"
    patch_resp = execute_notion_request(client, "PATCH", patch_url, headers=headers, payload={"properties": properties_update}, timeout=30.0)
    if patch_resp.status_code != 200:
        raise RuntimeError(f"Failed to attach report to Notion page {page_id} ({patch_resp.status_code}): {patch_resp.text}")

    return {
        "success": True,
        "page_id": page_id,
        "property": matched_prop_name,
        "total_files": len(combined_files),
        "status_updated": update_status,
    }


def upload_single_case_report(
    case_code: str,
    pdf_bytes: bytes,
    filename: str,
    page_id: str | None = None,
    api_key: str | None = None,
    update_status: bool = True,
    target_status: str = "Result Review",
) -> dict:
    """
    Handles end-to-end file upload and attachment for a single report.
    Resolves page_id if not provided.
    """
    token = api_key or os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")
    headers = get_notion_headers(token)
    cases_db = os.getenv("NOTION_CASES_DB_ID", "382c226a-2e86-48f1-b70e-c4e9e8f62d1d")
    part_db = os.getenv("NOTION_DATABASE_ID") or os.getenv("NOTION_PARTICIPANT_DB_ID")

    with httpx.Client(timeout=45.0) as client:
        # Resolve page ID if needed
        resolved_page_id = page_id
        if not resolved_page_id:
            resolved_page_id = find_case_page_by_code(client, headers, case_code, cases_db_id=cases_db, part_db_id=part_db)

        if not resolved_page_id:
            return {
                "success": False,
                "case_code": case_code,
                "filename": filename,
                "error": f"Case code '{case_code}' not found in Notion database",
            }

        # 1. Initiate upload
        file_upload_id, upload_url = initiate_notion_file_upload(client, headers, filename)

        # 2. Send bytes
        send_notion_file_bytes(client, headers, upload_url, pdf_bytes, filename)

        # 3. Attach to page
        res = attach_report_to_case_page(
            client=client,
            headers=headers,
            page_id=resolved_page_id,
            file_upload_id=file_upload_id,
            filename=filename,
            update_status=update_status,
            target_status=target_status,
        )

        return {
            "success": True,
            "case_code": case_code,
            "page_id": resolved_page_id,
            "filename": filename,
            "details": res,
        }


def upload_batch_reports_to_notion(
    items_data: list[dict],
    api_key: str | None = None,
    update_status: bool = True,
    target_status: str = "Result Review",
    max_workers: int = 2,
) -> dict:
    """
    Uploads a list of generated report files to their respective Notion case pages.
    items_data: list of dicts with keys:
      - 'item_id': str
      - 'case_code': str
      - 'filename': str
      - 'pdf_bytes': bytes
      - 'page_id': str | None (optional)
    """
    results = []
    success_count = 0
    fail_count = 0

    token = api_key or os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")

    def _worker(item):
        code = item.get("case_code", "")
        filename = item.get("filename", "")
        pdf_bytes = item.get("pdf_bytes", b"")
        page_id = item.get("page_id")
        item_id = item.get("item_id")

        try:
            res = upload_single_case_report(
                case_code=code,
                pdf_bytes=pdf_bytes,
                filename=filename,
                page_id=page_id,
                api_key=token,
                update_status=update_status,
                target_status=target_status,
            )
            res["item_id"] = item_id
            return res
        except Exception as exc:
            return {
                "success": False,
                "item_id": item_id,
                "case_code": code,
                "filename": filename,
                "error": str(exc),
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_worker, item) for item in items_data]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            results.append(res)
            if res.get("success"):
                success_count += 1
            else:
                fail_count += 1

    return {
        "total": len(items_data),
        "uploaded": success_count,
        "failed": fail_count,
        "results": results,
    }



