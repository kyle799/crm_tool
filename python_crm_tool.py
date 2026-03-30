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
import json
import os
import sys
from typing import Any, Dict, Optional

import requests


MAX_PAGE_SIZE = 500


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


def _format_entity_summary(entity: Dict[str, Any]) -> str:
    name = _first_present(entity, "entityname", "name")
    entity_id = _first_present(entity, "entityid", "id")
    status = _first_present(entity, "entitystatus", "status")
    entity_type = _first_present(entity, "entitytype", "type", "entity_type")
    city = _first_present(entity, "principalcity", "city")
    state = _first_present(entity, "principalstate", "state")
    contacted = _first_present(entity, "contacted")
    return (
        f"- {name}\n"
        f"  id: {entity_id}\n"
        f"  status: {status}\n"
        f"  type: {entity_type}\n"
        f"  location: {city}, {state}\n"
        f"  contacted: {contacted}"
    )


def format_pretty(result: Any) -> str:
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
        lines.extend(_format_entity_summary(entity) for entity in entities)
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

    else:
        parser.error(f"Unknown command: {args.command}")
        return

    if args.pretty_format:
        print(format_pretty(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
