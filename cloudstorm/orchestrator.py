"""
Orchestration logic for cloudstorm.

Handles the full pipeline: create infrastructure, wait for readiness,
install tools, and run tests across regions in parallel.
"""
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
import json

from .aws.cleanup import cleanup_region as _cleanup_region
from .aws.ec2 import create_instances, ensure_iam_role, terminate_instances
from .aws.ssm import wait_for_ssm_ready
from .aws.vpc import get_or_create_security_group, setup_vpc
from .constants import CONFIG_DIR
from .tools import install_tool
from .utils.display import print_error, print_info, print_success, print_warning
from .utils.parallel import run_parallel_regions


def setup_regions(
    region_list: list[str],
    instances: int,
    instance_type: str,
    tool: str,
    profile: str | None = None,
) -> dict[str, list[str]]:
    """
    Set up infrastructure across regions.

    Full pipeline per region (all regions in parallel):
    1. Create VPC + security group
    2. Create EC2 instances
    3. Wait for SSM agent (PingStatus == Online)
    4. Install tool
    Failed instances are terminated at each step.

    Args:
        region_list: List of AWS regions.
        instances: Number of instances per region.
        instance_type: EC2 instance type.
        tool: Load testing tool name.
        profile: AWS profile name.

    Returns:
        Dict mapping region to list of ready instance IDs.
    """
    if not region_list:
        print_error("No regions specified")
        return {}

    # IAM is global - create once before parallel region setup
    print_info("\nSetting up IAM role...")
    iam_info = ensure_iam_role(region_list[0], profile)
    if not iam_info:
        print_error("Failed to setup IAM role for SSM")
        return {}

    iam_profile_arn = iam_info["profile_arn"]

    def _setup_region(region):
        def _rollback():
            print_warning(f"{region}: rolling back resources...")
            _cleanup_region(region, profile)

        # 1. Create VPC + security group
        print_info(f"{region}: creating VPC...")
        vpc_info = setup_vpc(region, profile)
        if "error" in vpc_info:
            print_error(f"{region}: failed to setup VPC: {vpc_info['error']}")
            return None

        vpc_id = vpc_info["vpc_id"]
        subnet_id = vpc_info["subnet_id"]
        sg_id = get_or_create_security_group(region, vpc_id, profile)
        if not sg_id:
            print_error(f"{region}: failed to create security group")
            _rollback()
            return None

        # 2. Create instances
        ids = create_instances(
            region=region,
            count=instances,
            instance_type=instance_type,
            subnet_id=subnet_id,
            security_group_id=sg_id,
            profile=profile,
            iam_profile_arn=iam_profile_arn,
        )
        if not ids:
            print_error(f"{region}: failed to create instances")
            _rollback()
            return None
        print_success(f"{region}: created {len(ids)} instances")

        # 3. Wait for SSM agent
        ssm_ready = wait_for_ssm_ready(region, ids, profile=profile)
        if not ssm_ready:
            print_error(f"{region}: no instances became SSM-ready")
            _rollback()
            return None

        # 4. Install tool - check per-instance, terminate failures
        print_info(f"{region}: installing {tool}...")
        install_result = install_tool(tool, region, ssm_ready, profile)

        installed = []
        for instance_id in ssm_ready:
            instance_result = install_result.get("results", {}).get(instance_id, {})
            if instance_result.get("status") == "Success":
                installed.append(instance_id)
            else:
                print_error(
                    f"{region}: {instance_id} -- {tool} install failed "
                    f"(status={instance_result.get('status', 'Unknown')}) - terminating instance"
                )
                _print_install_output(region, instance_id, instance_result)
                terminate_instances(region, [instance_id], profile)

        if not installed:
            print_error(f"{region}: {tool} install failed on all instances")
            _rollback()
            return None

        print_success(f"{region}: {tool} installed on {len(installed)}/{len(ssm_ready)} instances")
        return installed

    results = run_parallel_regions(_setup_region, region_list)
    final = {r: ids for r, ids in results.items() if ids}

    _save_state(final, tool, instance_type)

    return final


def add_instances(
    region_list: list[str],
    instances: int,
    instance_type: str,
    tool: str,
    profile: str | None = None,
) -> dict[str, list[str]]:
    """
    Add instances to existing infrastructure.

    Reuses existing VPC and security group in each region.
    Creates new EC2 instances, waits for SSM, installs tool,
    and merges into existing state.

    Args:
        region_list: List of AWS regions.
        instances: Number of instances to add per region.
        instance_type: EC2 instance type.
        tool: Load testing tool name.
        profile: AWS profile name.

    Returns:
        Dict mapping region to list of NEW ready instance IDs.
    """
    if not region_list:
        print_error("No regions specified")
        return {}

    # Ensure IAM role exists
    print_info("\nEnsuring IAM role...")
    iam_info = ensure_iam_role(region_list[0], profile)
    if not iam_info:
        print_error("Failed to setup IAM role for SSM")
        return {}

    iam_profile_arn = iam_info["profile_arn"]

    def _add_to_region(region):
        # 1. Reuse existing VPC + security group
        print_info(f"{region}: finding existing VPC...")
        vpc_info = setup_vpc(region, profile)
        if "error" in vpc_info:
            print_error(f"{region}: failed to find/create VPC: {vpc_info['error']}")
            return None

        vpc_id = vpc_info["vpc_id"]
        subnet_id = vpc_info["subnet_id"]
        sg_id = get_or_create_security_group(region, vpc_id, profile)
        if not sg_id:
            print_error(f"{region}: failed to find/create security group")
            return None

        # 2. Create new instances
        ids = create_instances(
            region=region,
            count=instances,
            instance_type=instance_type,
            subnet_id=subnet_id,
            security_group_id=sg_id,
            profile=profile,
            iam_profile_arn=iam_profile_arn,
        )
        if not ids:
            print_error(f"{region}: failed to create instances")
            return None
        print_success(f"{region}: created {len(ids)} new instances")

        # 3. Wait for SSM
        ssm_ready = wait_for_ssm_ready(region, ids, profile=profile)
        if not ssm_ready:
            print_error(f"{region}: no new instances became SSM-ready")
            return None

        # 4. Install tool
        print_info(f"{region}: installing {tool} on new instances...")
        install_result = install_tool(tool, region, ssm_ready, profile)

        installed = []
        for instance_id in ssm_ready:
            instance_result = install_result.get("results", {}).get(instance_id, {})
            if instance_result.get("status") == "Success":
                installed.append(instance_id)
            else:
                print_error(
                    f"{region}: {instance_id} - {tool} install failed - terminating"
                )
                _print_install_output(region, instance_id, instance_result)
                terminate_instances(region, [instance_id], profile)

        if not installed:
            print_error(f"{region}: {tool} install failed on all new instances")
            return None

        print_success(f"{region}: {tool} installed on {len(installed)} new instances")
        return installed

    results = run_parallel_regions(_add_to_region, region_list)
    new_instances = {r: ids for r, ids in results.items() if ids}

    # Merge into existing state
    if new_instances:
        state_file = CONFIG_DIR / "state.json"
        existing_state = {}
        if state_file.exists():
            try:
                with open(state_file) as f:
                    existing_state = json.load(f)
            except Exception:
                pass

        existing_regions = existing_state.get("regions", {})
        for region, ids in new_instances.items():
            if region in existing_regions:
                existing_regions[region].extend(ids)
            else:
                existing_regions[region] = ids

        existing_state["tool"] = tool
        existing_state["regions"] = existing_regions
        existing_state["instance_type"] = instance_type
        _save_state_raw(existing_state)

    return new_instances


def _print_install_output(region: str, instance_id: str, instance_result: dict, max_chars: int = 600):
    """Print the tail of a failed install's stderr/stdout (the head is usually dnf noise)."""
    for label, key in (("stderr", "error"), ("stdout", "output")):
        content = (instance_result.get(key) or "").strip()
        if content:
            if len(content) > max_chars:
                content = "..." + content[-max_chars:]
            print_error(f"{region}: {instance_id} {label}:\n{content}")


def _save_state(regions: dict, tool: str, instance_type: str):
    """Save state to disk."""
    if not regions:
        return
    state = {
        "tool": tool,
        "regions": dict(regions.items()),
        "instance_type": instance_type,
    }
    _save_state_raw(state)


def _save_state_raw(state: dict):
    """Write raw state dict to disk atomically with file locking."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        state_file = CONFIG_DIR / "state.json"
        lock_file = state_file.with_suffix(".lock")
        # Lock on a dedicated lock file, then write atomically via temp+rename
        with open(lock_file, "w") as lf:
            if HAS_FCNTL:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            tmp_file = state_file.with_suffix(".tmp")
            with open(tmp_file, "w") as f:
                json.dump(state, f, indent=2)
            tmp_file.replace(state_file)
            if HAS_FCNTL:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        print_warning(f"Could not save state file: {e}. "
                     f"You may need to specify --tool on the run command.")


def load_and_update_state(updater):
    """Read state.json, apply updater function, write back atomically."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        state_file = CONFIG_DIR / "state.json"
        lock_file = state_file.with_suffix(".lock")
        with open(lock_file, "w") as lf:
            if HAS_FCNTL:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            state = {}
            if state_file.exists():
                with open(state_file) as f:
                    state = json.load(f)
            updater(state)
            tmp_file = state_file.with_suffix(".tmp")
            with open(tmp_file, "w") as f:
                json.dump(state, f, indent=2)
            tmp_file.replace(state_file)
            if HAS_FCNTL:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
