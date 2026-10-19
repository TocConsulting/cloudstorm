"""
Load testing tool registry.

Plugin architecture - each tool is a subfolder with:
- __init__.py: TOOL_INFO, install(), run_test(), parse_output()
- install.sh: Standalone installation script
- run.sh: Standalone test execution script

To add a new tool:
1. Create a folder: tools/mytool/
2. Add install.sh and run.sh scripts
3. Add __init__.py with the required interface
4. Register in SUPPORTED_TOOLS below
"""
from typing import Any

from ..constants import SUPPORTED_TOOLS
from . import artillery, jmeter, k6, locust

_TOOL_MODULES = {
    "locust": locust,
    "jmeter": jmeter,
    "artillery": artillery,
    "k6": k6,
}


def get_tool_module(tool: str):
    """
    Get a tool module by name.

    Args:
        tool: Tool name.

    Returns:
        Tool module.

    Raises:
        ValueError: If tool is not supported.
    """
    if tool not in _TOOL_MODULES:
        raise ValueError(f"Unknown tool: {tool}. Supported: {SUPPORTED_TOOLS}")

    return _TOOL_MODULES[tool]


def get_tool_info(tool: str) -> dict[str, Any]:
    """Get information about a tool."""
    module = get_tool_module(tool)
    return module.TOOL_INFO


def get_all_tools() -> dict[str, dict[str, Any]]:
    """Get info for all supported tools."""
    return {tool: get_tool_info(tool) for tool in SUPPORTED_TOOLS}


def install_tool(
    tool: str,
    region: str,
    instance_ids: list[str],
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Install a tool on instances.

    Args:
        tool: Tool name.
        region: AWS region.
        instance_ids: List of instance IDs.
        profile: AWS profile.

    Returns:
        Installation result.
    """
    module = get_tool_module(tool)
    return module.install(region, instance_ids, profile)


def run_test(
    tool: str,
    region: str,
    instance_ids: list[str],
    target_url: str,
    users: int,
    spawn_rate: int,
    duration: str,
    endpoints: str = "/",
    profile: str | None = None,
    progress_callback=None,
    script_content: str | None = None,
) -> dict[str, Any]:
    """
    Run a load test.

    Args:
        tool: Tool name.
        region: AWS region.
        instance_ids: List of instance IDs.
        target_url: Target URL.
        users: Number of users.
        spawn_rate: Users per second.
        duration: Test duration.
        endpoints: Comma-separated endpoints to test.
        profile: AWS profile.
        progress_callback: Optional callback for live progress updates.
        script_content: Optional custom test script content.

    Returns:
        Test result.
    """
    module = get_tool_module(tool)
    return module.run_test(
        region=region,
        instance_ids=instance_ids,
        target_url=target_url,
        users=users,
        spawn_rate=spawn_rate,
        duration=duration,
        endpoints=endpoints,
        profile=profile,
        progress_callback=progress_callback,
        script_content=script_content,
    )
