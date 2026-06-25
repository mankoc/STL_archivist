from dataiku.llm.agent_tools import BaseAgentTool
import logging
import dataiku
from ddgs import DDGS
import json

SUPPORTED_BACKENDS = ["auto", "google", "bing", "yahoo", "duckduckgo"]

# Hard caps to guarantee bounded work / return time.
# DDGS_TIMEOUT bounds a single HTTP request to one backend — the only timeout
# the ddgs library exposes — so keep it tight to keep return times short.
DDGS_TIMEOUT = 20                      # per-request timeout (seconds) for the ddgs client
MAX_RESULTS_CAP = 50                  # upper bound on results to fetch (caps pagination)


def run_search(search_terms, max_results, backend):
    """Core search call. Returns the result dict."""
    with DDGS(timeout=DDGS_TIMEOUT) as ddgs:
        results = list(ddgs.text(search_terms, max_results=max_results, backend=backend))
    if results:
        res_txt = [
            {"title": r["title"], "body": r["body"], "url": r["href"]}
            for r in results
        ]
        return {"output": json.dumps(res_txt)}
    return {"output": "[]"}


class InternetSearchTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": "Do a web search and return results.",
            "inputSchema": {
                "title": "Input for web search",
                "type": "object",
                "properties": {
                    "search_terms": {
                        "type": "string",
                        "description": "Search terms to look up"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": f"Maximum number of results to return (clamped to {MAX_RESULTS_CAP}).",
                        "default": 20
                    },
                    "backend": {
                        "type": "string",
                        "description": f"Search engine backend to use. Supported values: {', '.join(SUPPORTED_BACKENDS)}. Defaults to 'auto' which tries all backends.",
                        "enum": SUPPORTED_BACKENDS,
                        "default": "auto"
                    }
                },
                "required": ["search_terms"]
            }
        }

    def invoke(self, input, trace):
        self.logger.info(input)
        args = input["input"]
        search_terms = args["search_terms"]
        max_results = args.get("max_results", 20)
        backend = args.get("backend", "auto")

        if backend not in SUPPORTED_BACKENDS:
            self.logger.warning(f"Unsupported backend '{backend}', falling back to 'auto'")
            backend = "auto"

        # Clamp max_results so pagination can't run unbounded.
        try:
            max_results = int(max_results)
        except (TypeError, ValueError):
            max_results = 20
        max_results = max(1, min(max_results, MAX_RESULTS_CAP))

        # Each backend request is bounded by DDGS_TIMEOUT (the only timeout the
        # ddgs library exposes); return whatever it produces.
        try:
            return run_search(search_terms, max_results, backend)
        except Exception as e:
            return {"output": f"SEARCH_ERROR: {type(e).__name__}: {str(e)[:300]}"}

    def load_sample_query(self, tool):
        return {"search_terms": "Dataiku", "max_results": 20, "backend": "auto"}
