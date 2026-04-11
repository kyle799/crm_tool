# crm_tool

A CLI client for a CRM-style entities API. Supports listing, filtering, creating, updating, and deleting entities with optional pretty-printed output.

## Requirements

- Python 3.10+
- `requests` library (`pip install requests`)

## Configuration

Set the following environment variables (or pass them as flags):

| Variable      | Flag          | Description                        |
|---------------|---------------|------------------------------------|
| `API_BASE_URL` | `--base-url` | Base URL of the API, e.g. `https://example.com` |
| `API_KEY`      | `--api-key`  | API key sent as `X-API-Key` header |

```bash
export API_BASE_URL="https://example.com"
export API_KEY="your-key"
```

## Usage

```
python_crm_tool.py [--base-url URL] [--api-key KEY] [--timeout SECS] [--pretty-format] <command> [options]
```

### Global flags

| Flag              | Default | Description                                      |
|-------------------|---------|--------------------------------------------------|
| `--base-url`      | `$API_BASE_URL` | API base URL                           |
| `--api-key`       | `$API_KEY`      | API key                                |
| `--timeout`       | `30`    | HTTP timeout in seconds                          |
| `--pretty-format` | off     | Render output as a formatted table instead of JSON |

---

## Commands

### `health`

Check API health. Does not require an API key.

```bash
python_crm_tool.py health
```

---

### `list`

List entities with optional filtering. Automatically paginates for limits above 500.

```bash
python_crm_tool.py list [options]
```

| Option              | Description                                         |
|---------------------|-----------------------------------------------------|
| `--q TEXT`          | Full-text search query                              |
| `--status STATUS`   | Filter by status                                    |
| `--type TYPE`       | Filter by entity type                               |
| `--city CITY`       | Filter by city                                      |
| `--state STATE`     | Filter by state                                     |
| `--contacted true\|false` | Filter by contacted flag                     |
| `--active-companies`| Post-filter results to active companies only        |
| `--limit N`         | Max results to return (default: 50)                 |
| `--offset N`        | Pagination offset (default: 0)                      |

```bash
# Search by name
python_crm_tool.py list --q "Acme" --status active --limit 100

# List all active companies (client-side filtered)
python_crm_tool.py list --active-companies --pretty-format

# Uncontacted companies in Colorado
python_crm_tool.py list --state CO --contacted false --type company
```

---

### `get`

Fetch a single entity by ID.

```bash
python_crm_tool.py get <id>
```

---

### `create`

Create a new entity.

```bash
python_crm_tool.py create --data '<json>'
```

```bash
python_crm_tool.py create --data '{"name": "Acme LLC", "status": "active", "type": "company"}'
```

---

### `update`

Partially update an entity (PATCH).

```bash
python_crm_tool.py update <id> --data '<json>'
```

```bash
python_crm_tool.py update 123 --data '{"contacted": true, "city": "Denver"}'
```

---

### `delete`

Delete an entity by ID.

```bash
python_crm_tool.py delete <id>
```

---

---

## Backup and Restore

### `export`

Download all entities to a JSON file.

```bash
python_crm_tool.py export --out backup.json
```

Without `--out`, the JSON is printed to stdout (useful for piping).

| Option       | Description                              |
|--------------|------------------------------------------|
| `--out FILE` | Write output to FILE instead of stdout   |

---

### `import`

Bulk upsert entities from an export file. Safe to re-run — upserts on `entityid`, never overwrites CRM fields (`contacted`, `notes`, etc.) already set.

```bash
python_crm_tool.py import backup.json
```

Returns `{"imported": N, "skipped": N}`.

---

## Pretty-format output

Pass `--pretty-format` to any command for a fixed-width table view. When writing to a terminal, status and contact columns are color-coded:

| Status        | Display    | Color  |
|---------------|------------|--------|
| Good Standing / Exists | GOOD | Green |
| Delinquent / Noncompliant | DELINQ | Yellow |
| Dissolved / Withdrawn | DISSOLVED | Red |
| Other         | (truncated) | Cyan |

| Contacted | Display | Color |
|-----------|---------|-------|
| `true`    | REACHED | Green |
| `false`   | NEW     | Dim   |

Example:

```
count: 3
total: 3
results:
NAME                                                  STATUS     CONTACT  LOCATION            TYPE    AGE  ENTITY ID
----------------------------------------------------  ---------  -------  ------------------  ------  ---  -----------
Acme LLC                                              GOOD       REACHED  Denver, CO          LLC      5y  0001234567
Beta Corp                                             DELINQ     NEW      Pueblo, CO          CORP     2y  0009876543
Gamma Inc                                             DISSOLVED  -        -                   INC     12y  0005551234
```
