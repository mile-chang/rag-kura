"""Tool definitions for Ollama function calling.

Each tool is a plain Python function with a Google-style docstring.
The Ollama Python library auto-generates the JSON schema from these
docstrings, so no manual schema boilerplate is needed.

Runtime flow:
    1. ``TOOL_REGISTRY`` is passed to ``ollama.chat(tools=...)``
    2. If the model emits a ``tool_calls`` response, the backend
       looks up the function name in ``TOOL_MAP`` and executes it.
    3. The return value (a string) is fed back to the model as a
       ``role: tool`` message for the final answer.
"""

from datetime import datetime, timezone, timedelta
import urllib.parse

import httpx
from ddgs import DDGS

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_TW_TZ = timezone(timedelta(hours=8))

# Timeout for external HTTP calls (seconds).
_HTTP_TIMEOUT = 10.0


def get_current_time() -> str:
    """Get the current date and time in Taiwan (UTC+8).

    Returns:
        str: The current date and time formatted as 'YYYY-MM-DD HH:MM (weekday)'.
    """
    now = datetime.now(_TW_TZ)
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday = weekdays[now.weekday()]
    return f"{now.strftime('%Y-%m-%d %H:%M')} ({weekday})"


def get_weather(city: str) -> str:
    """Get the current weather for a given city.

    Args:
        city: The name of the city to look up weather for.
              Can be in any language (e.g. '竹北', 'Tokyo', 'New York').

    Returns:
        str: A summary of the current weather including temperature,
             humidity, wind, and conditions.
    """
    try:
        safe_city = urllib.parse.quote(city)
        resp = httpx.get(
            f"https://wttr.in/{safe_city}",
            params={"format": "j1"},
            timeout=_HTTP_TIMEOUT,
            # wttr.in requires a User-Agent or it returns HTML
            headers={"User-Agent": "curl/8.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Error fetching weather for '{city}': {e}"

    current = data.get("current_condition", [{}])[0]
    area_info = data.get("nearest_area", [{}])[0]

    area_name = area_info.get("areaName", [{}])[0].get("value", city)
    country = area_info.get("country", [{}])[0].get("value", "")
    weather_desc = current.get("weatherDesc", [{}])[0].get("value", "N/A")
    temp_c = current.get("temp_C", "N/A")
    feels_like = current.get("FeelsLikeC", "N/A")
    humidity = current.get("humidity", "N/A")
    wind_kmph = current.get("windspeedKmph", "N/A")
    wind_dir = current.get("winddir16Point", "N/A")
    uv_index = current.get("uvIndex", "N/A")
    obs_time = current.get("localObsDateTime", "N/A")

    return (
        f"Location: {area_name}, {country}\n"
        f"Observed at: {obs_time}\n"
        f"Condition: {weather_desc}\n"
        f"Temperature: {temp_c}°C (feels like {feels_like}°C)\n"
        f"Humidity: {humidity}%\n"
        f"Wind: {wind_kmph} km/h {wind_dir}\n"
        f"UV Index: {uv_index}"
    )


def web_search(query: str) -> str:
    """Search the web for up-to-date information using DuckDuckGo.

    Use this tool when the user asks about recent events, news, prices,
    product comparisons, restaurant recommendations, or any topic that
    requires current information beyond the model's training data.

    Args:
        query: The search query string. Can be in any language.

    Returns:
        str: A formatted list of search results, each containing
             the title, URL, and a brief snippet.
    """
    # Automatically append the current year to improve result freshness
    # when the query doesn't already contain a recent year reference.
    current_year = str(datetime.now(_TW_TZ).year)
    prev_year = str(int(current_year) - 1)
    if current_year not in query and prev_year not in query:
        query = f"{query} {current_year}"

    try:
        results = DDGS().text(query, max_results=8)
    except Exception as e:
        return f"Error performing web search for '{query}': {e}"

    if not results:
        return f"No search results found for '{query}'."

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("href", "")
        body = r.get("body", "No description")
        lines.append(f"[{i}] {title}\n    URL: {url}\n    {body}")

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# List of tool functions passed to ``ollama.chat(tools=...)``.
# The library inspects each function's signature + docstring to build
# the JSON-schema tool definition automatically.
TOOL_FUNCTIONS: list = [
    get_current_time,
    get_weather,
    web_search,
]

# Name → callable mapping used by the tool-execution loop.
TOOL_MAP: dict[str, callable] = {fn.__name__: fn for fn in TOOL_FUNCTIONS}
