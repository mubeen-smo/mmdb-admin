# mmdb-admin

Sync tooling for MavenMunch. Pulls published blog pages from a Notion database and upserts them into the Supabase `blogs` table.

## Setup

1. Copy `.env.example` to `.env` and fill in the values:

```
NOTION_API_KEY=        # Notion integration token
NOTION_DATABASE_ID=    # ID of the Notion blogs database
SUPABASE_URL=          # Supabase project URL
SUPABASE_SERVICE_ROLE_KEY=  # Service role key (not anon key)
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Run manually

```bash
python main.py blogs
```

Prints a summary: `{"synced": N, "skipped": N, "errors": [...]}`.

## GitHub Actions

The workflow at `.github/workflows/sync-blogs.yml` runs automatically every Monday at 2am UTC and can also be triggered manually via `workflow_dispatch`.

Add the following secrets to your GitHub repo (`Settings → Secrets and variables → Actions`):

- `NOTION_API_KEY`
- `NOTION_DATABASE_ID`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
