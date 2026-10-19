"""
CLI for cloudstorm - Distributed load testing across AWS regions.

Usage:
    cloudstorm auth                    Check AWS credentials
    cloudstorm create [OPTIONS]        Create infrastructure
    cloudstorm run [OPTIONS] URL       Run load test
    cloudstorm status                  View existing infrastructure
    cloudstorm cleanup [OPTIONS]       Cleanup resources
    cloudstorm report [OPTIONS] FILE   Generate reports
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import click
from rich.live import Live

from . import __version__
from .aws.cleanup import cleanup_all, cleanup_region, get_all_instances
from .aws.credentials import check_credentials, get_account_id, get_available_regions
from .constants import APP_NAME, EC2_INSTANCE_INFO, EC2_PRICING, SUPPORTED_TOOLS, TOOL_USERS_PER_GIB
from .orchestrator import add_instances, load_and_update_state, setup_regions
from .reports import (
    generate_csv_report as _generate_csv_report,
)
from .reports import (
    generate_html_report as _generate_html_report,
)
from .tools import get_all_tools
from .tools import run_test as run_tool_test
from .tools.base import parse_duration as _parse_duration
from .utils.display import (
    confirm,
    console,
    print_banner,
    print_error,
    print_info,
    print_key_value,
    print_success,
    print_table,
    print_warning,
    render_live_dashboard,
    set_quiet,
)
from .utils.parallel import run_parallel_regions


@click.group()
@click.version_option(version=__version__, prog_name=APP_NAME)
@click.option("--quiet", "-q", is_flag=True, help="Suppress banner and reduce output")
@click.pass_context
def cli(ctx, quiet):
    """cloudstorm - Distributed load testing across AWS regions."""
    ctx.ensure_object(dict)
    ctx.obj["quiet"] = quiet
    if quiet:
        set_quiet(True)


@cli.command()
@click.option("--profile", "-p", help="AWS profile name")
def auth(profile: str | None):
    """Check AWS credentials and permissions."""
    print_banner()

    print_info("Checking AWS credentials...")

    if not check_credentials(profile):
        print_error("AWS credentials not configured or invalid")
        print_info("Configure with: aws configure --profile <profile>")
        sys.exit(1)

    account_id = get_account_id(profile)
    if account_id:
        print_success(f"Authenticated to AWS account: {account_id}")
    else:
        print_warning("Could not retrieve account ID")

    # List available regions
    regions = get_available_regions(profile)
    print_info(f"Available regions: {len(regions)}")


@cli.command()
@click.option("--profile", "-p", help="AWS profile name")
@click.option("--regions", "-r", required=True, help="AWS regions (comma-separated)")
@click.option("--instances", "-n", default=1, type=click.IntRange(min=1), help="Instances per region")
@click.option("--type", "-t", "instance_type", default="t3.medium", help="EC2 instance type")
@click.option("--tool", required=True, type=click.Choice(SUPPORTED_TOOLS), help="Load testing tool")
@click.option("--auto-cleanup", is_flag=True, help="Auto cleanup after test")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def create(
    profile: str | None,
    regions: str,
    instances: int,
    instance_type: str,
    tool: str,
    auto_cleanup: bool,
    yes: bool,
):
    """Create load testing infrastructure."""
    print_banner()

    region_list = [r.strip() for r in regions.split(",")]

    # Validate regions
    available = get_available_regions(profile)
    for region in region_list:
        if region not in available:
            print_error(f"Invalid region: {region}")
            sys.exit(1)

    # Show cost estimate
    total_instances = len(region_list) * instances
    hourly_cost = EC2_PRICING.get(instance_type, 0.05) * total_instances

    print_info("Infrastructure plan:")
    print_key_value("Regions", ", ".join(region_list))
    print_key_value("Instances per region", str(instances))
    print_key_value("Total instances", str(total_instances))
    print_key_value("Instance type", instance_type)
    print_key_value("Tool", tool)
    print_key_value("Estimated cost", f"${hourly_cost:.3f}/hour")
    print_key_value("Auto cleanup", "Yes" if auto_cleanup else "No")

    if not yes:
        if not confirm("Create infrastructure?"):
            print_info("Cancelled")
            sys.exit(0)

    # Check credentials
    if not check_credentials(profile):
        print_error("AWS credentials not configured")
        sys.exit(1)

    ready_instances = setup_regions(
        region_list=region_list,
        instances=instances,
        instance_type=instance_type,
        tool=tool,
        profile=profile,
    )

    if not ready_instances:
        print_error("No instances ready in any region")
        sys.exit(1)

    # Summary
    total_ready = sum(len(ids) for ids in ready_instances.values())
    print_success(f"\nInfrastructure ready: {total_ready} instances across {len(ready_instances)} regions")

    print_info("\nNext step:")
    print_info("  cloudstorm run https://your-target.com --users 100 --duration 5m")

    # Save auto_cleanup preference to state (always write, so False resets a prior True)
    load_and_update_state(lambda s: s.__setitem__("auto_cleanup", auto_cleanup))
    if auto_cleanup:
        print_info("\nAuto-cleanup is enabled. Resources will be cleaned up after your test.")


@cli.command()
@click.option("--profile", "-p", help="AWS profile name")
@click.option("--regions", "-r", help="AWS regions (comma-separated, default: from existing state)")
@click.option("--instances", "-n", default=1, type=click.IntRange(min=1), help="Instances to add per region")
@click.option("--type", "-t", "instance_type", default="t3.medium", help="EC2 instance type")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def add(
    profile: str | None,
    regions: str | None,
    instances: int,
    instance_type: str,
    yes: bool,
):
    """Add instances to existing infrastructure."""
    print_banner()

    # Load state to get tool and existing regions
    state_file = Path.home() / ".cloudstorm" / "state.json"
    if not state_file.exists():
        print_error("No existing infrastructure found. Run 'cloudstorm create' first.")
        sys.exit(1)

    try:
        with open(state_file) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print_error(f"Corrupted state file: {e}. Run 'cloudstorm create' again.")
        sys.exit(1)

    tool = state.get("tool", "locust")

    if regions:
        region_list = [r.strip() for r in regions.split(",")]
    else:
        region_list = list(state.get("regions", {}).keys())

    if not region_list:
        print_error("No regions specified and none found in state")
        sys.exit(1)

    # Validate regions
    available = get_available_regions(profile)
    for region in region_list:
        if region not in available:
            print_error(f"Invalid region: {region}")
            sys.exit(1)

    total_new = len(region_list) * instances
    existing_total = sum(len(ids) for ids in state.get("regions", {}).values())
    hourly_cost = EC2_PRICING.get(instance_type, 0.05) * total_new

    print_info("Adding instances:")
    print_key_value("Regions", ", ".join(region_list))
    print_key_value("New instances per region", str(instances))
    print_key_value("Total new instances", str(total_new))
    print_key_value("Existing instances", str(existing_total))
    print_key_value("Instance type", instance_type)
    print_key_value("Tool", tool)
    print_key_value("Additional cost", f"${hourly_cost:.3f}/hour")

    if not yes:
        if not confirm("Add instances?"):
            print_info("Cancelled")
            sys.exit(0)

    if not check_credentials(profile):
        print_error("AWS credentials not configured")
        sys.exit(1)

    new_instances = add_instances(
        region_list=region_list,
        instances=instances,
        instance_type=instance_type,
        tool=tool,
        profile=profile,
    )

    if not new_instances:
        print_error("No new instances ready")
        sys.exit(1)

    total_added = sum(len(ids) for ids in new_instances.values())
    print_success(f"\nAdded {total_added} instances across {len(new_instances)} regions")

    # Show new total
    try:
        with open(state_file) as f:
            updated_state = json.load(f)
        new_total = sum(len(ids) for ids in updated_state.get("regions", {}).values())
        print_info(f"Total instances now: {new_total}")
    except Exception:
        pass


@cli.command()
@click.argument("target_url")
@click.option("--profile", "-p", help="AWS profile name")
@click.option("--regions", "-r", help="AWS regions (comma-separated, default: all with instances)")
@click.option("--users", "-u", default=100, type=click.IntRange(min=1), help="Number of concurrent users")
@click.option("--spawn-rate", "-s", default=10, type=click.IntRange(min=1), help="Users spawned per second")
@click.option("--duration", "-d", default="5m", help="Test duration (e.g., 60s, 5m, 1h)")
@click.option("--tool", type=click.Choice(SUPPORTED_TOOLS), help="Load testing tool (auto-detected if not set)")
@click.option("--endpoints", "-e", default="/", help="Endpoints to test (comma-separated, e.g., '/,/api,/login')")
@click.option("--output", "-o", help="Output file for results (JSON)")
@click.option("--script", type=click.Path(exists=True), help="Custom test script (locustfile.py, script.js, test.jmx, config.yml)")
@click.option("--threshold", multiple=True, help='Pass/fail threshold (e.g., "p95 < 500", "error_rate < 5")')
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def run(
    target_url: str,
    profile: str | None,
    regions: str | None,
    users: int,
    spawn_rate: int,
    duration: str,
    tool: str | None,
    endpoints: str,
    output: str | None,
    script: str | None,
    threshold: tuple,
    yes: bool,
):
    """Run load test against TARGET_URL."""
    print_banner()

    # Validate URL
    parsed_url = urlparse(target_url)
    if parsed_url.scheme not in ("http", "https"):
        print_error(f"Invalid URL scheme: {parsed_url.scheme or 'none'}. Must be http:// or https://")
        sys.exit(1)
    if not parsed_url.hostname:
        print_error("Invalid URL: no hostname found")
        sys.exit(1)

    # Validate duration format
    try:
        _parse_duration(duration)
    except (ValueError, IndexError):
        print_error(f"Invalid duration format: {duration}. Use format like '60s', '5m', '1h'")
        sys.exit(1)

    # Validate custom script if provided
    script_content = None
    if script:
        script_path = Path(script)
        script_size = script_path.stat().st_size
        if script_size > 48000:
            print_error(f"Script file is {script_size} bytes, maximum is 48000 bytes")
            sys.exit(1)
        script_content = script_path.read_text()

        # Validate extension matches tool (if tool is known at this point)
        ext = script_path.suffix.lower()
        tool_ext_map = {
            "locust": [".py"], "k6": [".js"], "jmeter": [".jmx"],
            "artillery": [".yml", ".yaml", ".json"],
        }
        if tool and ext not in tool_ext_map.get(tool, []):
            expected = ", ".join(tool_ext_map.get(tool, []))
            print_error(f"Script file {script_path.name} has extension {ext}, expected {expected} for {tool}")
            sys.exit(1)

        if endpoints != "/":
            print_warning("--endpoints is ignored when using --script")

    # Validate endpoints (strict whitelist to prevent code injection in generated scripts)
    for ep in endpoints.split(","):
        ep = ep.strip()
        if not ep.startswith("/"):
            print_error(f"Invalid endpoint: {ep}. Endpoints must start with /")
            sys.exit(1)
        if not re.match(r'^[a-zA-Z0-9/_\-\.~%?&=+:@]+$', ep):
            print_error(f"Invalid endpoint: {ep}. Only alphanumeric and URL-safe characters allowed.")
            sys.exit(1)

    # Check credentials before scanning regions
    if not check_credentials(profile):
        print_error("AWS credentials not configured. Use -p <profile> or set AWS_PROFILE environment variable.")
        sys.exit(1)

    # Get existing instances
    all_instance_info = get_all_instances(profile)

    if not all_instance_info:
        print_error("No cloudstorm instances found. Run 'cloudstorm create' first.")
        sys.exit(1)

    # Filter by regions if specified
    if regions:
        region_list = [r.strip() for r in regions.split(",")]
        all_instance_info = {r: ids for r, ids in all_instance_info.items() if r in region_list}

    if not all_instance_info:
        print_error("No instances found in specified regions")
        sys.exit(1)

    # Extract instance ID strings from instance info dicts
    instance_ids_by_region = {
        region: [i["instance_id"] for i in instances]
        for region, instances in all_instance_info.items()
    }

    # Auto-detect tool from state if not specified
    if not tool:
        state_file = Path.home() / ".cloudstorm" / "state.json"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
                tool = state.get("tool", "locust")
                print_info(f"Using tool from last create: {tool}")
            except Exception:
                tool = "locust"
                print_info(f"Using default tool: {tool}")
        else:
            tool = "locust"
            print_info(f"Using default tool: {tool}")

    total_instances = sum(len(ids) for ids in instance_ids_by_region.values())

    # Calculate per-region user/spawn distribution (divide globally across ALL instances)
    users_per_region = {}
    spawn_per_region = {}
    for region, ids in instance_ids_by_region.items():
        region_share = len(ids) / total_instances
        users_per_region[region] = max(1, round(users * region_share))
        spawn_per_region[region] = max(1, round(spawn_rate * region_share))

    # Safety check: warn if users per instance may exceed memory limits
    users_per_instance_approx = users // total_instances if total_instances > 0 else users
    sample_instance = next(iter(all_instance_info.values()))[0]
    instance_type = sample_instance.get("instance_type", "")
    info = EC2_INSTANCE_INFO.get(instance_type)
    if info and tool in TOOL_USERS_PER_GIB:
        max_safe = int(info["memory_gib"] * TOOL_USERS_PER_GIB[tool])
        if users_per_instance_approx > max_safe:
            print_warning(f"WARNING: ~{users_per_instance_approx} users/instance may exceed safe limit "
                         f"(~{max_safe}) for {tool} on {instance_type} ({info['memory_gib']} GiB RAM)")
            print_warning("Risk of OOM kill. Consider adding instances or using a larger instance type.")

    print_info("Test configuration:")
    print_key_value("Target URL", target_url)
    print_key_value("Tool", tool)
    if script:
        print_key_value("Script", script)
    print_key_value("Regions", ", ".join(instance_ids_by_region.keys()))
    print_key_value("Total instances", str(total_instances))
    print_key_value("Total users", str(users))
    for region, ids in instance_ids_by_region.items():
        print_key_value(f"  {region}", f"{users_per_region[region]} users on {len(ids)} instances")
    print_key_value("Spawn rate", f"{spawn_rate}/sec")
    print_key_value("Duration", duration)
    if not script:
        print_key_value("Endpoints", endpoints)

    if not yes:
        if not confirm("Start load test?"):
            print_info("Cancelled")
            sys.exit(0)

    # Run tests (parallel across regions) with live dashboard
    print_info("\nStarting load test...")
    start_time = datetime.now(timezone.utc)
    duration_seconds = _parse_duration(duration)

    # Shared progress state (thread-safe via GIL for simple dict updates)
    live_progress = {r: {"requests": 0, "errors": 0, "instances": len(ids)}
                     for r, ids in instance_ids_by_region.items()}

    def _progress_callback(region, elapsed, data):
        live_progress[region] = {
            **live_progress[region],
            "requests": data.get("requests", 0),
            "errors": data.get("errors", 0),
        }

    # Capture tool in a local for the closure
    selected_tool = tool
    from .utils import display as _display_mod
    is_quiet = _display_mod._quiet

    def _run_region(region):
        return run_tool_test(
            tool=selected_tool,
            region=region,
            instance_ids=instance_ids_by_region[region],
            target_url=target_url,
            users=users_per_region[region],
            spawn_rate=spawn_per_region[region],
            duration=duration,
            endpoints=endpoints,
            profile=profile,
            progress_callback=_progress_callback if not is_quiet else None,
            script_content=script_content,
        )

    region_list = list(instance_ids_by_region.keys())

    if not is_quiet:
        with ThreadPoolExecutor(max_workers=len(region_list)) as executor:
            futures = {executor.submit(_run_region, r): r for r in region_list}

            with Live(render_live_dashboard(0, duration_seconds, live_progress, total_instances),
                      console=console, refresh_per_second=1) as live:
                while not all(f.done() for f in futures):
                    elapsed = int((datetime.now(timezone.utc) - start_time).total_seconds())
                    live.update(render_live_dashboard(elapsed, duration_seconds, live_progress, total_instances))
                    time.sleep(1)

                # Final update
                elapsed = int((datetime.now(timezone.utc) - start_time).total_seconds())
                live.update(render_live_dashboard(elapsed, duration_seconds, live_progress, total_instances))

            results = {}
            for future in futures:
                region = futures[future]
                try:
                    results[region] = future.result()
                except Exception as e:
                    print_error(f"Failed to process region {region}: {e}")
                    results[region] = None
    else:
        results = run_parallel_regions(_run_region, region_list)

    end_time = datetime.now(timezone.utc)

    # Aggregate results
    aggregated = _aggregate_all_results(results)
    aggregated["meta"] = {
        "tool": tool,
        "target_url": target_url,
        "start_time": start_time.isoformat() + "Z",
        "end_time": end_time.isoformat() + "Z",
        "duration_seconds": (end_time - start_time).total_seconds(),
        "regions": list(instance_ids_by_region.keys()),
        "total_instances": total_instances,
        "config": {
            "users": users,
            "spawn_rate": spawn_rate,
            "duration": duration,
            "endpoints": endpoints,
            "script": script or None,
        },
    }

    # Print summary
    _print_results_summary(aggregated)

    no_results = aggregated.get("summary", {}).get("total_requests", 0) == 0
    if no_results:
        print_error("No requests were recorded in any region -- the test did not run successfully")

    # Evaluate thresholds if provided
    threshold_failed = False
    if threshold:
        try:
            evaluations = _evaluate_thresholds(threshold, aggregated)
            rows = []
            for ev in evaluations:
                status = "PASS" if ev["passed"] else "FAIL"
                rows.append([
                    ev["threshold"],
                    status,
                    f"{ev['operator']} {ev['expected']}{ev['unit']}",
                    f"{ev['actual']}{ev['unit']}",
                ])
            print_info("")
            print_table(["Threshold", "Status", "Expected", "Actual"], rows, title="Thresholds")

            all_passed = all(ev["passed"] for ev in evaluations)
            if all_passed:
                print_success(f"All {len(evaluations)} thresholds passed")
            else:
                failed_count = sum(1 for ev in evaluations if not ev["passed"])
                print_error(f"FAILED: {failed_count}/{len(evaluations)} thresholds breached")
                threshold_failed = True

            aggregated["thresholds"] = evaluations
        except ValueError as e:
            print_error(str(e))
            sys.exit(1)

    # Save results - always generate both JSON and HTML
    # Build default filename from tool and target
    if output:
        json_file = output
    else:
        target_host = urlparse(target_url).hostname or "unknown"
        target_host = target_host.replace(".", "-")
        json_file = f"results/{tool}-{target_host}-{users}u.json"

    try:
        os.makedirs(os.path.dirname(json_file) or ".", exist_ok=True)
        with open(json_file, "w") as f:
            json.dump(aggregated, f, indent=2)
        print_success(f"\nResults saved to {json_file}")

        html_file = json_file.rsplit(".", 1)[0] + ".html"
        html_content = _generate_html_report(aggregated)
        with open(html_file, "w") as f:
            f.write(html_content)
        print_success(f"HTML report saved to {html_file}")
    except OSError as e:
        print_error(f"Failed to save results: {e}")
        print_info("Results JSON (stdout fallback):")
        print(json.dumps(aggregated, indent=2))

    # Auto-cleanup if enabled
    state_file = Path.home() / ".cloudstorm" / "state.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
            if state.get("auto_cleanup"):
                print_info("\nAuto-cleanup enabled - cleaning up resources...")
                cleanup_all(profile=profile)
                state_file.unlink()
        except Exception as e:
            print_warning(f"Auto-cleanup failed: {e}")
            print_warning("Run 'cloudstorm cleanup --all -y' to clean up manually.")

    # Exit with code 1 if thresholds failed or nothing ran (after cleanup, so resources are cleaned)
    if threshold_failed or no_results:
        sys.exit(1)


@cli.command()
@click.option("--profile", "-p", help="AWS profile name")
def status(profile: str | None):
    """View existing cloudstorm infrastructure."""
    print_banner()

    if not check_credentials(profile):
        print_error("AWS credentials not configured. Use -p <profile> or set AWS_PROFILE environment variable.")
        sys.exit(1)

    print_info("Scanning for cloudstorm instances...")

    all_instances = get_all_instances(profile)

    if not all_instances:
        print_info("No cloudstorm instances found")
        return

    total = 0
    rows = []

    for region, instances in all_instances.items():
        for instance in instances:
            rows.append([
                instance.get("instance_id", ""),
                region,
                instance.get("instance_type", ""),
                instance.get("state", ""),
                instance.get("public_ip", "N/A"),
            ])
            total += 1

    print_table(
        ["Instance ID", "Region", "Type", "State", "Public IP"],
        rows,
    )

    print_info(f"\nTotal instances: {total}")

    # Show state info if available
    state_file = Path.home() / ".cloudstorm" / "state.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
            print_key_value("Tool", state.get("tool", "unknown"))
            print_key_value("Instance type", state.get("instance_type", "unknown"))
            print_key_value("Auto cleanup", "Yes" if state.get("auto_cleanup") else "No")
        except Exception:
            pass


@cli.command()
@click.option("--profile", "-p", help="AWS profile name")
@click.option("--regions", "-r", help="AWS regions (comma-separated)")
@click.option("--all", "cleanup_all_flag", is_flag=True, help="Cleanup all regions")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def cleanup(
    profile: str | None,
    regions: str | None,
    cleanup_all_flag: bool,
    yes: bool,
):
    """Cleanup cloudstorm resources."""
    print_banner()

    if not check_credentials(profile):
        print_error("AWS credentials not configured. Use -p <profile> or set AWS_PROFILE environment variable.")
        sys.exit(1)

    if regions:
        region_list = [r.strip() for r in regions.split(",")]
    elif cleanup_all_flag:
        region_list = None  # Will scan all regions
    else:
        print_error("Specify --regions or --all")
        sys.exit(1)

    # Show what will be cleaned up
    print_info("Scanning for resources to cleanup...")

    all_instances = get_all_instances(profile)

    if region_list:
        all_instances = {r: ids for r, ids in all_instances.items() if r in region_list}

    if not all_instances:
        print_info("No cloudstorm resources found")
        return

    total = sum(len(ids) for ids in all_instances.values())
    print_info(f"Found {total} instances across {len(all_instances)} regions")

    if not yes:
        if not confirm("Cleanup these resources?"):
            print_info("Cancelled")
            sys.exit(0)

    # Cleanup
    if region_list:
        for region in region_list:
            cleanup_region(region, profile)
    else:
        cleanup_all(profile=profile)

    print_success("Cleanup complete")

    # Update state file atomically
    state_file = Path.home() / ".cloudstorm" / "state.json"
    if state_file.exists():
        try:
            if region_list:
                # Partial cleanup: remove only cleaned regions
                def _remove_regions(s):
                    for r in region_list:
                        s.get("regions", {}).pop(r, None)
                load_and_update_state(_remove_regions)
                # Delete if no regions left
                if state_file.exists():
                    with open(state_file) as f:
                        s = json.load(f)
                    if not s.get("regions"):
                        state_file.unlink()
            else:
                state_file.unlink()
        except Exception:
            pass
        # Clean up lock file
        lock_file = state_file.with_suffix(".lock")
        if lock_file.exists():
            try:
                lock_file.unlink()
            except Exception:
                pass


@cli.command()
@click.argument("input_file", type=click.Path(exists=True))
@click.option("--format", "-f", "output_format", type=click.Choice(["json", "csv", "html"]), default="html")
@click.option("--output", "-o", help="Output file")
def report(input_file: str, output_format: str, output: str | None):
    """Generate report from results file."""
    print_banner()

    # Load results
    try:
        with open(input_file) as f:
            results = json.load(f)
    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON in {input_file}: {e}")
        sys.exit(1)

    # Generate report
    if output_format == "json":
        report_content = json.dumps(results, indent=2)
        ext = "json"
    elif output_format == "csv":
        report_content = _generate_csv_report(results)
        ext = "csv"
    else:
        report_content = _generate_html_report(results)
        ext = "html"

    # Output
    if output:
        output_file = output
    else:
        output_file = input_file.rsplit(".", 1)[0] + f".{ext}"

    try:
        with open(output_file, "w") as f:
            f.write(report_content)
        print_success(f"Report saved to {output_file}")
    except OSError as e:
        print_error(f"Failed to write report: {e}")
        sys.exit(1)


@cli.command()
def tools():
    """List available load testing tools."""
    print_banner()

    all_tools = get_all_tools()

    rows = []
    for name, info in all_tools.items():
        rows.append([
            name,
            info.get("name", ""),
            info.get("language", ""),
            info.get("website", ""),
        ])

    print_table(
        ["ID", "Name", "Language", "Website"],
        rows,
    )


def _parse_threshold(threshold_str: str):
    """Parse 'p95 < 500' or 'p95<500' into ('p95', '<', 500.0)."""
    match = re.match(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)?)\s*$', threshold_str.strip())
    if not match:
        raise ValueError(f"Invalid threshold: '{threshold_str}'. Use format: 'metric < value'")
    metric, operator, value = match.groups()
    valid_metrics = {"p90", "p95", "p99", "avg", "median", "min", "max", "error_rate", "rps", "total_requests"}
    if metric not in valid_metrics:
        raise ValueError(f"Unknown metric: '{metric}'. Valid: {', '.join(sorted(valid_metrics))}")
    return metric, operator, float(value)


def _evaluate_thresholds(thresholds: tuple, results: dict):
    """Evaluate thresholds against results. Returns list of (threshold_str, passed, expected, actual)."""
    metric_map = {
        "p90": results.get("response_times", {}).get("p90_ms", 0),
        "p95": results.get("response_times", {}).get("p95_ms", 0),
        "p99": results.get("response_times", {}).get("p99_ms", 0),
        "avg": results.get("response_times", {}).get("avg_ms", 0),
        "median": results.get("response_times", {}).get("median_ms", 0),
        "min": results.get("response_times", {}).get("min_ms", 0),
        "max": results.get("response_times", {}).get("max_ms", 0),
        "error_rate": results.get("summary", {}).get("error_rate", 0) * 100,
        "rps": results.get("summary", {}).get("requests_per_second", 0),
        "total_requests": results.get("summary", {}).get("total_requests", 0),
    }

    ops = {
        "<": lambda a, b: a < b,
        ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b,
        ">=": lambda a, b: a >= b,
    }

    evaluations = []
    for t in thresholds:
        metric, operator, value = _parse_threshold(t)
        actual = metric_map[metric]
        passed = ops[operator](actual, value)
        unit = "%" if metric == "error_rate" else ("req/s" if metric == "rps" else ("" if metric == "total_requests" else "ms"))
        evaluations.append({
            "threshold": t,
            "passed": passed,
            "metric": metric,
            "operator": operator,
            "expected": value,
            "actual": round(actual, 2),
            "unit": unit,
        })

    return evaluations


def _aggregate_all_results(results: dict) -> dict:
    """Aggregate results from all regions.

    Note: Percentiles (p90, p95, p99) are computed as weighted averages across
    regions/instances, which is an approximation. Exact percentiles would require
    raw histograms (e.g., HDR histogram merge). The approximation is reasonable
    when load is distributed evenly across instances.
    """
    total_requests = 0
    successful = 0
    failed = 0
    rps = 0

    all_mins = []
    all_maxes = []
    response_times = []
    weights = []

    for _region, result in results.items():
        if result is None:
            continue
        parsed = result.get("parsed", {})
        summary = parsed.get("summary", {})
        times = parsed.get("response_times", {})

        reqs = summary.get("total_requests", 0)
        total_requests += reqs
        successful += summary.get("successful_requests", 0)
        failed += summary.get("failed_requests", 0)
        rps += summary.get("requests_per_second", 0)

        if reqs > 0:
            weights.append(reqs)
            response_times.append(times)

            if times.get("min_ms", 0) > 0:
                all_mins.append(times["min_ms"])
            if times.get("max_ms", 0) > 0:
                all_maxes.append(times["max_ms"])

    # Calculate weighted averages
    total_weight = sum(weights)

    def weighted_avg(key: str) -> float:
        if total_weight == 0:
            return 0
        return sum(
            times.get(key, 0) * w
            for times, w in zip(response_times, weights, strict=False)
        ) / total_weight

    # Merge per-endpoint stats from all regions
    merged_endpoints = {}
    for _region, result in results.items():
        if result is None:
            continue
        parsed = result.get("parsed", {})
        for name, ep in parsed.get("endpoints", {}).items():
            if name not in merged_endpoints:
                merged_endpoints[name] = {"requests": 0, "successful": 0, "failed": 0, "total_time": 0.0, "min": float('inf'), "max": 0.0}
            m = merged_endpoints[name]
            reqs = ep.get("requests", 0)
            m["requests"] += reqs
            m["successful"] += ep.get("successful", 0)
            m["failed"] += ep.get("failed", 0)
            m["total_time"] += ep.get("avg_ms", 0) * reqs
            ep_min = ep.get("min_ms", 0)
            ep_max = ep.get("max_ms", 0)
            if ep_min > 0:
                m["min"] = min(m["min"], ep_min)
            if ep_max > 0:
                m["max"] = max(m["max"], ep_max)

    all_endpoints = {}
    for name, m in merged_endpoints.items():
        all_endpoints[name] = {
            "requests": m["requests"],
            "successful": m["successful"],
            "failed": m["failed"],
            "avg_ms": m["total_time"] / m["requests"] if m["requests"] > 0 else 0,
            "min_ms": m["min"] if m["min"] != float('inf') else 0,
            "max_ms": m["max"],
        }

    return {
        "summary": {
            "total_requests": total_requests,
            "successful_requests": successful,
            "failed_requests": failed,
            "error_rate": failed / total_requests if total_requests > 0 else 0,
            "requests_per_second": rps,
        },
        "response_times": {
            "min_ms": min(all_mins) if all_mins else 0,
            "avg_ms": weighted_avg("avg_ms"),
            "max_ms": max(all_maxes) if all_maxes else 0,
            "median_ms": weighted_avg("median_ms"),
            "p90_ms": weighted_avg("p90_ms"),
            "p95_ms": weighted_avg("p95_ms"),
            "p99_ms": weighted_avg("p99_ms"),
        },
        "endpoints": all_endpoints,
        "by_region": {
            region: result.get("parsed", {})
            for region, result in results.items()
            if result is not None
        },
    }


def _print_results_summary(results: dict):
    """Print results summary to console."""
    summary = results.get("summary", {})
    times = results.get("response_times", {})

    print_info("\n" + "=" * 50)
    print_info("RESULTS SUMMARY")
    print_info("=" * 50)

    print_key_value("Total requests", f"{summary.get('total_requests', 0):,}")
    print_key_value("Successful", f"{summary.get('successful_requests', 0):,}")
    print_key_value("Failed", f"{summary.get('failed_requests', 0):,}")
    print_key_value("Error rate", f"{summary.get('error_rate', 0) * 100:.2f}%")
    print_key_value("Requests/sec", f"{summary.get('requests_per_second', 0):.2f}")

    print_info("\nResponse times (ms):")
    print_key_value("  Min", f"{times.get('min_ms', 0):.0f}")
    print_key_value("  Avg", f"{times.get('avg_ms', 0):.0f}")
    print_key_value("  Max", f"{times.get('max_ms', 0):.0f}")
    print_key_value("  Median", f"{times.get('median_ms', 0):.0f}")
    print_key_value("  p90", f"{times.get('p90_ms', 0):.0f}")
    print_key_value("  p95", f"{times.get('p95_ms', 0):.0f}")
    print_key_value("  p99", f"{times.get('p99_ms', 0):.0f}")

    endpoints = results.get("endpoints", {})
    if endpoints:
        ep_rows = []
        for ep_name in sorted(endpoints.keys()):
            ep = endpoints[ep_name]
            ep_rows.append([
                ep_name,
                f"{ep.get('requests', 0):,}",
                f"{ep.get('successful', 0):,}",
                f"{ep.get('failed', 0):,}",
                f"{ep.get('avg_ms', 0):.0f}",
                f"{ep.get('min_ms', 0):.0f}",
                f"{ep.get('max_ms', 0):.0f}",
            ])
        print_info("")
        print_table(
            ["Endpoint", "Requests", "Success", "Failed", "Avg(ms)", "Min(ms)", "Max(ms)"],
            ep_rows,
            title="Endpoints",
        )


def main():
    """Entry point."""
    try:
        cli()
    except KeyboardInterrupt:
        print_error("\nInterrupted by user. Resources may still be running!")
        print_warning("Run 'cloudstorm cleanup --all -y' to terminate all resources.")
        sys.exit(130)


if __name__ == "__main__":
    main()
