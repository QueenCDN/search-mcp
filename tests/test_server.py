"""server: MCP protocol integration, error containment, memory hygiene."""

import asyncio

import pytest
from fastmcp import Client

import server


def run(coro):
    return asyncio.run(coro)


class TestProtocol:
    def test_all_twelve_tools_listed(self):
        async def check():
            async with Client(server.mcp) as client:
                tools = {t.name for t in await client.list_tools()}
                expected = {
                    "search_web", "search_news", "fetch_page",
                    "current_time", "current_date", "weather",
                    "currency_rate", "wikipedia_search",
                    "unit_converter", "calculator",
                    "generate_uuid", "random_generator",
                }
                assert tools == expected

        run(check())

    def test_calculator_via_protocol(self):
        async def check():
            async with Client(server.mcp) as client:
                result = await client.call_tool("calculator", {"expression": "2^10"})
                assert "1024" in result.content[0].text

        run(check())

    def test_unit_converter_via_protocol(self):
        async def check():
            async with Client(server.mcp) as client:
                result = await client.call_tool(
                    "unit_converter",
                    {"value": 5, "from_unit": "kg", "to_unit": "lb"},
                )
                assert "11.02" in result.content[0].text

        run(check())

    def test_current_time_via_protocol(self):
        async def check():
            async with Client(server.mcp) as client:
                result = await client.call_tool("current_time", {"location": "UTC"})
                assert "Timezone: UTC" in result.content[0].text

        run(check())


class TestErrorContainment:
    def test_tool_exception_becomes_message(self, monkeypatch):
        def explode(query):
            raise RuntimeError("catastrophic failure")

        monkeypatch.setattr(server.search_module, "search_web", explode)

        async def check():
            async with Client(server.mcp) as client:
                result = await client.call_tool("search_web", {"query": "boom"})
                text = result.content[0].text
                assert "unexpected error" in text
                assert "catastrophic failure" in text

        run(check())

    def test_bad_input_returns_message_not_crash(self):
        async def check():
            async with Client(server.mcp) as client:
                result = await client.call_tool("fetch_page", {"url": "not-a-real-url"})
                text = result.content[0].text
                assert "Could not extract" in text or "Error" in text

        run(check())


class TestStats:
    def test_stats_recorded_per_call(self):
        from utils import STATS

        before = STATS.snapshot()["total_calls"]

        async def check():
            async with Client(server.mcp) as client:
                await client.call_tool("calculator", {"expression": "1+1"})

        run(check())
        after = STATS.snapshot()["total_calls"]
        assert after == before + 1


class TestMemoryHygiene:
    def test_cache_stays_bounded_under_load(self):
        """Simulates heavy usage; the cache must respect its memory cap."""
        from cache import cache

        for i in range(500):
            cache.set(f"load-test-{i}", "y" * 10_000)
        stats = cache.stats()
        from utils import CACHE_MAX_ENTRIES, CACHE_MAX_MEMORY_MB

        assert stats["entries"] <= CACHE_MAX_ENTRIES
        assert stats["memory_bytes"] <= CACHE_MAX_MEMORY_MB * 1024 * 1024
        cache.clear()
