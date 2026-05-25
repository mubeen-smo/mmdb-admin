import sys

from dotenv import load_dotenv

load_dotenv()

USAGE = """\
Usage: python main.py <command> [subcommand]

Commands:
  serve               Run the submissions web app (http://localhost:8080)

  sync blogs          Sync blog posts from Notion → Supabase
  sync places    Sync places + dishes from Notion → Supabase (via Groq)
  sync all            Run all syncs

  embed               Embed all sources (places, items, blogs)
  embed blogs         Embed only blog posts
  embed places        Embed only places and items

  backfill places     Backfill all places + items from Supabase → Notion Places DB
"""


def cmd_sync(sub: str) -> None:
    if sub == "blogs":
        from sync.blogs import sync_blogs
        print(sync_blogs())
    elif sub == "places":
        from sync.places import sync_places
        print(sync_places())
    elif sub == "all":
        from sync.blogs import sync_blogs
        from sync.places import sync_places
        print("-- blogs --")
        print(sync_blogs())
        print("-- places --")
        print(sync_places())
    else:
        print(f"Unknown sync target: {sub!r}")
        print(USAGE)
        sys.exit(1)


def cmd_backfill(sub: str) -> None:
    if sub == "places":
        from sync.backfill import backfill_places_to_notion
        print(backfill_places_to_notion())
    else:
        print(f"Unknown backfill target: {sub!r}")
        print(USAGE)
        sys.exit(1)


def cmd_embed(sub: str) -> None:
    from embeddings.pipeline import run_embeddings
    if sub == "all":
        print(run_embeddings("all"))
    elif sub == "blogs":
        print(run_embeddings("blog"))
    elif sub == "places":
        results: dict = {}
        results.update(run_embeddings("place"))
        results.update(run_embeddings("item"))
        print(results)
    else:
        print(f"Unknown embed target: {sub!r}")
        print(USAGE)
        sys.exit(1)


def cmd_serve() -> None:
    import uvicorn
    uvicorn.run(
        "submissions.app:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
    )


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print(USAGE)
        sys.exit(0)

    command = args[0]

    if command == "serve":
        cmd_serve()
    elif command == "sync":
        sub = args[1] if len(args) > 1 else "all"
        cmd_sync(sub)
    elif command == "embed":
        sub = args[1] if len(args) > 1 else "all"
        cmd_embed(sub)
    elif command == "backfill":
        sub = args[1] if len(args) > 1 else ""
        cmd_backfill(sub)
    else:
        print(f"Unknown command: {command!r}")
        print(USAGE)
        sys.exit(1)
