from typing import Any
from chartgen_cli.tools import ci, bash, web_search, chart_visualization, generate_ppt, memory, domain_data, generate_html

TOOL_SCHEMAS = [ci.SCHEMA, bash.SCHEMA, web_search.SCHEMA, chart_visualization.SCHEMA, generate_ppt.SCHEMA, memory.SCHEMA, domain_data.SCHEMA, generate_html.SCHEMA]


def get_thinking(name: str, args: dict[str, Any]) -> str:
    if name == "ci":
        return ci.thinking(args)
    if name == "bash_executor":
        return bash.thinking(args)
    if name == "web_search":
        return web_search.thinking(args)
    if name == "chart_visualization":
        return chart_visualization.thinking(args)
    if name == "generate_ppt":
        return generate_ppt.thinking(args)
    if name == "memory_retrieve":
        return memory.thinking(args)
    if name == "domain_data":
        return domain_data.thinking(args)
    if name == "generate_html":
        return generate_html.thinking(args)
    return name


def dispatch(name: str, args: dict[str, Any]) -> str:
    if name == "ci":
        return ci.run(args.get("code", ""))
    if name == "bash_executor":
        return bash.run(args.get("command", ""))
    if name == "web_search":
        return web_search.run(args.get("query", ""))
    if name == "chart_visualization":
        return chart_visualization.run(
            data=args.get("data", ""),
            user_chat=args.get("user_chat", ""),
        )
    if name == "generate_ppt":
        return generate_ppt.run(
            query=args.get("query", ""),
            report_markdown=args.get("report_markdown", ""),
        )
    if name == "memory_retrieve":
        return memory.run(args.get("current_query", ""))
    if name == "domain_data":
        return domain_data.run(args.get("domains", []), args.get("download_images", False))
    if name == "generate_html":
        return generate_html.run(
            html_file=args.get("html_file", ""),
            output_name=args.get("output_name", ""),
        )
    available = "ci, bash_executor, web_search, chart_visualization, generate_ppt, memory_retrieve, domain_data, generate_html"
    return f"[error] tool '{name}' does not exist. You must only call tools from this list: {available}. Call the correct tool now."
