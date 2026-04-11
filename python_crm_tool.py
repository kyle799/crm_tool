#!/usr/bin/env python3
"""
Simple CLI client for the CRM-style API.

Usage examples:

  export API_BASE_URL="https://example.com"
  export API_KEY="your-key"

  ./crm_api.py health
  ./crm_api.py list --q "Acme" --status active --limit 100
  ./crm_api.py list --active-companies
  ./crm_api.py get 123
  ./crm_api.py create --data '{"name":"Acme LLC","status":"active","type":"customer"}'
  ./crm_api.py update 123 --data '{"contacted": true, "city": "Pueblo"}'
  ./crm_api.py delete 123

Notes:
- Base URL is required via --base-url or API_BASE_URL
- API key is required via --api-key or API_KEY
- /health does not require auth per your spec, but this client can still send the header if provided
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
import sys
from typing import Any, Dict, Optional

import requests


MAX_PAGE_SIZE = 500
NAME_COLUMN_WIDTH = 52
STATUS_COLUMN_WIDTH = 9
CONTACT_COLUMN_WIDTH = 7
LOCATION_COLUMN_WIDTH = 18
TYPE_COLUMN_WIDTH = 6
AGE_COLUMN_WIDTH = 3
ID_COLUMN_WIDTH = 11

ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_CYAN = "\033[36m"
ANSI_DIM = "\033[2m"


class APIClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self, include_auth: bool = True) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if include_auth and self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        include_auth: bool = True,
    ) -> Any:
        url = f"{self.base_url}{path}"

        response = requests.request(
            method=method,
            url=url,
            headers=self._headers(include_auth=include_auth),
            params=params,
            json=json_body,
            timeout=self.timeout,
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            print(f"HTTP error: {exc}", file=sys.stderr)
            print(f"Status: {response.status_code}", file=sys.stderr)
            try:
                print(json.dumps(response.json(), indent=2), file=sys.stderr)
            except ValueError:
                print(response.text, file=sys.stderr)
            sys.exit(1)

        if response.status_code == 204 or not response.content:
            return {"ok": True, "status_code": response.status_code}

        try:
            return response.json()
        except ValueError:
            return {
                "ok": True,
                "status_code": response.status_code,
                "text": response.text,
            }

    def health(self) -> Any:
        return self._request("GET", "/health", include_auth=False)

    def list_entities(
        self,
        q: Optional[str] = None,
        status: Optional[str] = None,
        entity_type: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        contacted: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Any:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        params: Dict[str, Any] = {}

        if q is not None:
            params["q"] = q
        if status is not None:
            params["status"] = status
        if entity_type is not None:
            params["type"] = entity_type
        if city is not None:
            params["city"] = city
        if state is not None:
            params["state"] = state
        if contacted is not None:
            params["contacted"] = str(contacted).lower()

        if limit <= MAX_PAGE_SIZE:
            params["limit"] = limit
            params["offset"] = offset
            return self._request("GET", "/api/v1/entities", params=params)

        return self._list_entities_paginated(params=params, limit=limit, offset=offset)

    def _list_entities_paginated(self, *, params: Dict[str, Any], limit: int, offset: int) -> Any:
        remaining = limit
        current_offset = offset
        merged_result: Any = None

        while remaining > 0:
            page_limit = min(remaining, MAX_PAGE_SIZE)
            page_params = dict(params)
            page_params["limit"] = page_limit
            page_params["offset"] = current_offset
            page_result = self._request("GET", "/api/v1/entities", params=page_params)

            merged_result = merge_paginated_results(merged_result, page_result)

            page_size = len(_get_entities_from_result(page_result))
            if page_size < page_limit:
                break

            remaining -= page_limit
            current_offset += page_limit

        return _normalize_result_metadata(merged_result, limit=limit, offset=offset)

    def get_entity(self, entity_id: str) -> Any:
        return self._request("GET", f"/api/v1/entities/{entity_id}")

    def create_entity(self, data: Dict[str, Any]) -> Any:
        return self._request("POST", "/api/v1/entities", json_body=data)

    def update_entity(self, entity_id: str, data: Dict[str, Any]) -> Any:
        return self._request("PATCH", f"/api/v1/entities/{entity_id}", json_body=data)

    def delete_entity(self, entity_id: str) -> Any:
        return self._request("DELETE", f"/api/v1/entities/{entity_id}")

    def export_entities(self) -> Any:
        return self._request("GET", "/api/v1/entities/export")

    def import_entities(self, data: Any) -> Any:
        return self._request("POST", "/api/v1/entities/import", json_body=data)


def parse_json_arg(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("JSON payload must be an object")
    return parsed


def _extract_entity_list(result: Any) -> Optional[tuple[Optional[str], list[Dict[str, Any]]]]:
    if isinstance(result, list):
        entities = [item for item in result if isinstance(item, dict)]
        return None, entities

    if not isinstance(result, dict):
        return None

    for key in ("entities", "items", "results", "data"):
        value = result.get(key)
        if isinstance(value, list):
            entities = [item for item in value if isinstance(item, dict)]
            return key, entities

    return None


def _get_entities_from_result(result: Any) -> list[Dict[str, Any]]:
    extracted = _extract_entity_list(result)
    if extracted is None:
        return []
    _, entities = extracted
    return entities


def _normalize_result_metadata(result: Any, *, limit: int, offset: int) -> Any:
    if not isinstance(result, dict):
        return result

    normalized = dict(result)
    normalized["limit"] = limit
    normalized["offset"] = offset
    normalized["returned_count"] = len(_get_entities_from_result(result))
    return normalized


def merge_paginated_results(existing: Any, new_result: Any) -> Any:
    if existing is None:
        return new_result

    existing_extracted = _extract_entity_list(existing)
    new_extracted = _extract_entity_list(new_result)
    if existing_extracted is None or new_extracted is None:
        return new_result

    existing_key, existing_entities = existing_extracted
    new_key, new_entities = new_extracted
    if existing_key != new_key:
        return new_result

    merged_entities = existing_entities + new_entities

    if existing_key is None:
        return merged_entities

    merged = dict(existing)
    merged[existing_key] = merged_entities

    total = merged.get("total")
    if isinstance(total, int):
        merged["returned_count"] = len(merged_entities)
    else:
        merged["total"] = len(merged_entities)

    return merged


def _is_company(entity: Dict[str, Any]) -> bool:
    company_markers = {"company", "business", "organization", "org"}
    company_type_suffixes = ("LLC", "INC", "CORP", "CORPORATION", "LP", "LLP", "LLLP", "PC")

    for key in ("entitytype", "type", "entity_type", "kind", "category", "record_type"):
        value = entity.get(key)
        if not isinstance(value, str):
            continue

        normalized = value.strip().upper()
        if normalized.lower() in company_markers:
            return True
        if normalized.endswith(company_type_suffixes):
            return True

    return False


def _is_active(entity: Dict[str, Any]) -> bool:
    active_statuses = {"GOOD STANDING", "EXISTS"}
    for key in ("entitystatus", "status"):
        value = entity.get(key)
        if isinstance(value, str) and value.strip().upper() in active_statuses:
            return True
    return False


def filter_active_companies(result: Any) -> Any:
    extracted = _extract_entity_list(result)
    if extracted is None:
        return result

    list_key, entities = extracted
    filtered = [entity for entity in entities if _is_active(entity) and _is_company(entity)]

    if list_key is None:
        return filtered

    updated = dict(result)
    updated[list_key] = filtered
    updated["filtered_count"] = len(filtered)
    if "returned_count" in updated:
        updated["returned_count"] = len(filtered)
    return updated


def _first_present(entity: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = entity.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return "-"


def _truncate_text(text: str, width: int) -> str:
    if len(text) <= width:
        return text.ljust(width)
    return f"{text[: width - 1].rstrip()}…"


def _apply_color(text: str, color: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{color}{text}{ANSI_RESET}"


def _normalize_status(status: str) -> tuple[str, str]:
    normalized = status.strip().upper()
    if normalized in {"GOOD STANDING", "EXISTS"}:
        return "GOOD", ANSI_GREEN
    if normalized in {"DELINQUENT", "NONCOMPLIANT"}:
        return "DELINQ", ANSI_YELLOW
    if any(marker in normalized for marker in ("DISSOLVED", "WITHDRAWN", "RELINQUISHED")):
        return "DISSOLVED", ANSI_RED
    return normalized[:10], ANSI_CYAN


def _format_contacted(contacted: Any) -> tuple[str, str]:
    if contacted is True:
        return "REACHED", ANSI_GREEN
    if contacted is False:
        return "NEW", ANSI_DIM
    return "-", ANSI_CYAN


def _format_age(form_date: str) -> str:
    try:
        dt = parsedate_to_datetime(form_date)
    except (TypeError, ValueError, IndexError):
        return "-"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    age_days = (datetime.now(timezone.utc) - dt).days
    if age_days < 0:
        return "0y"
    return f"{int(age_days / 365.25)}y"


def _pad_text(text: str, width: int) -> str:
    if len(text) >= width:
        return text[:width]
    return text.ljust(width)


def _format_location(city: str, state: str) -> str:
    if city == "-" and state == "-":
        return "-"
    if state == "-":
        return city
    return f"{city}, {state}"


def _format_entity_header() -> str:
    columns = [
        _pad_text("NAME", NAME_COLUMN_WIDTH),
        _pad_text("STATUS", STATUS_COLUMN_WIDTH),
        _pad_text("CONTACT", CONTACT_COLUMN_WIDTH),
        _pad_text("LOCATION", LOCATION_COLUMN_WIDTH),
        _pad_text("TYPE", TYPE_COLUMN_WIDTH),
        _pad_text("AGE", AGE_COLUMN_WIDTH),
        _pad_text("ENTITY ID", ID_COLUMN_WIDTH),
    ]
    header = "  ".join(columns)
    divider = "  ".join(
        [
            "-" * NAME_COLUMN_WIDTH,
            "-" * STATUS_COLUMN_WIDTH,
            "-" * CONTACT_COLUMN_WIDTH,
            "-" * LOCATION_COLUMN_WIDTH,
            "-" * TYPE_COLUMN_WIDTH,
            "-" * AGE_COLUMN_WIDTH,
            "-" * ID_COLUMN_WIDTH,
        ]
    )
    return f"{header}\n{divider}"


def _format_entity_row(entity: Dict[str, Any], *, use_color: bool) -> str:
    name = _truncate_text(_first_present(entity, "entityname", "name"), NAME_COLUMN_WIDTH)
    entity_id = _first_present(entity, "entityid", "id")
    status_label, status_color = _normalize_status(_first_present(entity, "entitystatus", "status"))
    contact_label, contact_color = _format_contacted(entity.get("contacted"))
    city = _first_present(entity, "principalcity", "city")
    state = _first_present(entity, "principalstate", "state")
    entity_type = _first_present(entity, "entitytype", "type", "entity_type")
    age = _format_age(_first_present(entity, "entityformdate"))
    location = _truncate_text(_format_location(city, state), LOCATION_COLUMN_WIDTH)

    status_text = _apply_color(_pad_text(status_label, STATUS_COLUMN_WIDTH), status_color, enabled=use_color)
    contact_text = _apply_color(_pad_text(contact_label, CONTACT_COLUMN_WIDTH), contact_color, enabled=use_color)

    return (
        f"{name}  "
        f"{status_text}  "
        f"{contact_text}  "
        f"{location}  "
        f"{_pad_text(entity_type, TYPE_COLUMN_WIDTH)}  "
        f"{age.rjust(AGE_COLUMN_WIDTH)}  "
        f"{_pad_text(entity_id, ID_COLUMN_WIDTH)}"
    )


def format_pretty(result: Any) -> str:
    use_color = sys.stdout.isatty()
    extracted = _extract_entity_list(result)
    if extracted is not None:
        _, entities = extracted
        lines = [
            f"count: {len(entities)}",
        ]
        if isinstance(result, dict):
            for key in ("total", "limit", "offset", "filtered_count"):
                if key in result:
                    lines.append(f"{key}: {result[key]}")

        if not entities:
            lines.append("results: none")
            return "\n".join(lines)

        lines.append("results:")
        lines.append(_format_entity_header())
        lines.extend(_format_entity_row(entity, use_color=use_color) for entity in entities)
        return "\n".join(lines)

    if isinstance(result, dict):
        return "\n".join(f"{key}: {json.dumps(value, sort_keys=True)}" for key, value in sorted(result.items()))

    return json.dumps(result, indent=2, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI client for the entities API")
    parser.add_argument(
        "--base-url",
        default=os.getenv("API_BASE_URL"),
        help="API base URL, e.g. https://example.com (or set API_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("API_KEY"),
        help="API key for X-API-Key header (or set API_KEY)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--pretty-format",
        action="store_true",
        help="Render output in a readable text format instead of JSON",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="GET /health")

    list_parser = subparsers.add_parser("list", help="GET /api/v1/entities")
    list_parser.add_argument("--q")
    list_parser.add_argument("--status")
    list_parser.add_argument("--type", dest="entity_type")
    list_parser.add_argument("--city")
    list_parser.add_argument("--state")
    list_parser.add_argument(
        "--contacted",
        choices=["true", "false"],
        help="Filter by contacted=true|false",
    )
    list_parser.add_argument(
        "--active-companies",
        action="store_true",
        help="Filter list results to active companies after fetching",
    )
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--offset", type=int, default=0)

    get_parser = subparsers.add_parser("get", help="GET /api/v1/entities/<id>")
    get_parser.add_argument("id", help="Entity ID")

    create_parser = subparsers.add_parser("create", help="POST /api/v1/entities")
    create_parser.add_argument(
        "--data",
        required=True,
        type=parse_json_arg,
        help='JSON object, e.g. \'{"name":"Acme","status":"active"}\'',
    )

    update_parser = subparsers.add_parser("update", help="PATCH /api/v1/entities/<id>")
    update_parser.add_argument("id", help="Entity ID")
    update_parser.add_argument(
        "--data",
        required=True,
        type=parse_json_arg,
        help='JSON object, e.g. \'{"contacted":true}\'',
    )

    delete_parser = subparsers.add_parser("delete", help="DELETE /api/v1/entities/<id>")
    delete_parser.add_argument("id", help="Entity ID")

    export_parser = subparsers.add_parser("export", help="GET /api/v1/entities/export — download all entities")
    export_parser.add_argument(
        "--out",
        metavar="FILE",
        help="Write output to FILE instead of stdout",
    )

    import_parser = subparsers.add_parser("import", help="POST /api/v1/entities/import — bulk upsert from export file")
    import_parser.add_argument(
        "file",
        metavar="FILE",
        help="Path to a JSON export file produced by the export command",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.base_url:
        print("Error: --base-url or API_BASE_URL is required", file=sys.stderr)
        sys.exit(2)

    if args.command != "health" and not args.api_key:
        print("Error: --api-key or API_KEY is required", file=sys.stderr)
        sys.exit(2)

    client = APIClient(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
    )

    if args.command == "health":
        result = client.health()

    elif args.command == "list":
        contacted_val = None
        if args.contacted is not None:
            contacted_val = args.contacted == "true"

        result = client.list_entities(
            q=args.q,
            status=args.status,
            entity_type=args.entity_type,
            city=args.city,
            state=args.state,
            contacted=contacted_val,
            limit=args.limit,
            offset=args.offset,
        )
        if args.active_companies:
            result = filter_active_companies(result)

    elif args.command == "get":
        result = client.get_entity(args.id)

    elif args.command == "create":
        result = client.create_entity(args.data)

    elif args.command == "update":
        result = client.update_entity(args.id, args.data)

    elif args.command == "delete":
        result = client.delete_entity(args.id)

    elif args.command == "export":
        result = client.export_entities()
        if args.out:
            with open(args.out, "w") as fh:
                json.dump(result, fh, indent=2, sort_keys=True)
            print(f"Exported to {args.out}", file=sys.stderr)
            return
        # fall through to normal output below

    elif args.command == "import":
        try:
            with open(args.file) as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Error reading {args.file}: {exc}", file=sys.stderr)
            sys.exit(1)
        result = client.import_entities(payload)

    else:
        parser.error(f"Unknown command: {args.command}")
        return

    if args.pretty_format:
        print(format_pretty(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
