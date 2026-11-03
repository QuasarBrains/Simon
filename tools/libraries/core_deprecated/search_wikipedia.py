from typing import Any

import wikipediaapi

from application.events import PubSub
from tools.index import Tool

client = wikipediaapi.Wikipedia(
    user_agent="Klank-Agent/0.1 (https://github.com/QuasarBrains/Simon)",
    language="en",
)


def run_search_wikipedia(args: Any, pubsub: PubSub) -> str:
    try:
        if not args or "query" not in args:
            return "Error running search_wikipedia: No query provided."

        page = client.page(args["query"])

        if not page.exists():
            return "No articles found for the given query."

        results = f"""Results for search query: {args.get("query")}
        {page.title}
        ---
        {page.text}
        ---
        """

        truncated_results = results[:5000]
        return truncated_results
    except Exception as e:
        return f"Error running search_wikipedia: {e}"


search_wikipedia = Tool(
    name="search_wikipedia",
    description="Search Wikipedia for information on a given query.",
    function=run_search_wikipedia,
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term for finding relevant Wikipedia articles.",
            },
        },
        "required": ["query"],
    },
)
