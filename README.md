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

## Enrichment writes

Subcommands for posting signals, triggers, runs, contacts, and locations
through the colorado-biz API. These mirror the
[POST endpoints](https://github.com/kyle799/colorado-biz/blob/main/README.md#signals--triggers--enrichment-runs-endpoints)
added for the AI enricher; they're useful by hand for data corrections too.

### `signals create`

```bash
python_crm_tool.py signals create <entity_id> --data '<json>'
```

```bash
python_crm_tool.py signals create 0001234567 --data '{
  "signal_type": "web_presence",
  "signal_category": "site_status",
  "source_name": "manual",
  "confidence_score": 90
}'
```

### `triggers create`

```bash
python_crm_tool.py triggers create <entity_id> --data '<json>'
```

```bash
python_crm_tool.py triggers create 0001234567 --data '{
  "trigger_type": "review_spike",
  "title": "10 new reviews in 7 days",
  "intensity_score": 75
}'
```

### `enrichment-runs create` / `enrichment-runs finish`

Two-step open-then-close pattern for enrichment runs:

```bash
# Open a run
python_crm_tool.py enrichment-runs create 0001234567 --data '{
  "source_name": "manual",
  "status": "in_progress"
}'
# → returns the new run row, including its `id`

# Close it
python_crm_tool.py enrichment-runs finish 42 \
  --status ok \
  --signals-created 3
```

`finish` is shorthand — under the hood it's a `PATCH /enrichment-runs/<id>`. Send `--status error --error-message "..."` to record a failure. Terminal status auto-stamps `finished_at` server-side.

### `contacts create` / `locations create`

```bash
python_crm_tool.py contacts create 0001234567 --data '{
  "full_name": "Jane Doe",
  "title": "Owner",
  "email": "jane@acme.com",
  "is_primary": true
}'

python_crm_tool.py locations create 0001234567 --data '{
  "address1": "123 Main St",
  "city": "Denver",
  "state": "CO",
  "is_primary": true
}'
```

### `list-needing-enrichment`

Find entities that still need a given field filled. Feeds the AI enricher's selector — also useful by itself for "what's left to do" reporting.

```bash
python_crm_tool.py list-needing-enrichment --field foundwebsite --limit 25
```

| Option              | Default       | Description                                    |
|---------------------|---------------|------------------------------------------------|
| `--field NAME`      | `foundwebsite`| Entity field that must be empty/null           |
| `--max-age-days N`  | `0`           | Skip entities with a successful enrichment_runs row from `--source-name` within this window. **0 disables the check** (saves N+1 GETs). |
| `--source-name NAME`| `enricher`    | enrichment_runs.source_name to match           |
| `--limit N`         | `50`          | Max candidates returned                        |
| `--scan N`          | `500`         | How many entities to scan before filtering     |

The colorado-biz API does not currently expose a "field IS NULL" filter, so this command scans + filters client-side. For larger datasets, push the filter into colorado-biz.

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
