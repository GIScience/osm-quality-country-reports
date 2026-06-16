import requests
import json
from shapely.geometry import mapping, shape
import shlex
from pathlib import Path
from shapely import make_valid


# -------------
# Funktions to handle (failed) api requests
# -------------


def handle_request_error(e, attempt, max_attempts):
    """
    Returns:
        retry (bool)
        sleep_seconds (int)
    Raises:
        Exception if no retry should happen
    """

    if attempt < max_attempts:
        return True, 2 * attempt

    else:
        if isinstance(e, requests.HTTPError):
            response = getattr(e, "response", None)

            status = getattr(response, "status_code", None)

            raise type(e)(
                f"{type(e).__name__} | status={status}"
            ) from e

        # any requests-related error
        if isinstance(e, requests.RequestException):
            raise type(e)(
                f"RequestException {type(e).__name__}: {e}"
            ) from e

        # completely different error
        raise type(e)(
            f"{type(e).__name__}: {e}"
        ) from e


def default_target_dir(geom_id, sqr_raw=None, hex_raw=None, ADM0_raw=None, ADM1_raw=None):
    gid = geom_id.lower()
    if "sqr" in gid:
        return sqr_raw, "sqr"
    if "hex" in gid or "hdx" in gid:
        return hex_raw, "hex"
    if "adm1" in gid:
        return ADM1_raw, "ADM1"
    if "adm0" in gid:
        return ADM0_raw, "ADM0"
    
    raise ValueError(f"Could not determine unit for ID: {geom_id}")


def build_api_request_jobs(
        gdf,
        indicators,
        relevant,
        topic,
        default_target_dir,
        sqr_raw=None,
        hex_raw=None,
        ADM0_raw=None,
        ADM1_raw=None):
    """
    Generic job builder for GRID, ADM1, ADM0.

    Args:
        gdf: GeoDataFrame with geometries and IDs
        indicators: list of indicators
        relevant: list of relevant attributes
        topic: topic string
        default_target_dir: function(row, *output_paths) ->
            returns target folder for each row and unit label for logging
    Returns:
        List[dict]: list of job dicts
    """

    jobs = []
    for _, row in gdf.iterrows():

        geom = row.geometry
        geom_id = row["id"]
        geom_json = mapping(geom)

        # define output folder and unit for grid/square calculation
        target_dir, unit_label = default_target_dir(
            geom_id,
            sqr_raw=sqr_raw,
            hex_raw=hex_raw,
            ADM0_raw=ADM0_raw,
            ADM1_raw=ADM1_raw)

        for ind in indicators:
            if ind == "attribute-completeness":
                for attr in relevant:
                    p = target_dir / f"{topic}__{ind}__{attr}__{geom_id}.json"
                    jobs.append(
                        {
                            "path": p,
                            "geometry": geom_json,
                            "indicator": ind,
                            "attribute": attr,
                            "unit": unit_label,
                            "geom_id": geom_id,
                        }
                    )
            else:
                p = target_dir / f"{topic}__{ind}__{geom_id}.json"
                jobs.append(
                    {
                        "path": p,
                        "geometry": geom_json,
                        "indicator": ind,
                        "attribute": None,
                        "unit": unit_label,
                        "geom_id": geom_id,
                    }
                )
    return jobs


def check_failed_files(
        country, failed_file, completed_count,
        raw_root, unit, context, retry=False):
    """
    Checks if there are failed files and saves retry file,
    raises error if some requests failed.
    """
    retry_file = raw_root / "failed_requests.json"
    # check if there is a retry file
    if retry:
        if failed_file:
            raise RuntimeError(
                f"[{country}] Retry failed AGAIN for {len(failed_file)} {unit}"
                f" requests. Check {raw_root / 'failed_requests.json'} "
                f"for details.")
        else:
            context.log.info(
                f"[{country}] RETRY SUCCESSFUL: all {completed_count} "
                f"{unit} requests completed successfully"
            )
            # remove retry file
            retry_file.unlink()

    elif retry is False and failed_file:
        # load failed file if it exists, otherwise start with empty list
        if retry_file.exists():
            try:
                with open(retry_file) as f:
                    existing_failed = json.load(f)
            except json.JSONDecodeError:
                existing_failed = []
        else:
            existing_failed = []

        existing_failed.extend(failed_file)

        with open(retry_file, "w") as f:
            json.dump(existing_failed, f, indent=2)

        context.log.error(
            f"[{country}] Completed {completed_count}/"
            f"{len(failed_file) + completed_count}"
            f" {unit} requests ({len(failed_file)} failed)")
        return True

    else:
        context.log.info(
            f"[{country}] DONE: all {completed_count} out of "
            f"{completed_count} {unit} requests completed successfully"
        )
        return False

def get_retry_jobs_from_curls(raw_root):
    """
    Scans the failed_requests folder and reconstructs jobs from the stored metadata.
    This assumes you stored a small JSON metadata header in the .sh file 
    OR you just parse the curl. (Simpler: check if folder has files).
    """
    fail_dir = Path(raw_root) / "failed_requests"
    if not fail_dir.exists():
        return []
    
    # In this implementation, we return the file paths to be re-run
    return list(fail_dir.glob("*.sh"))


def generate_curl_command(url, headers, payload):
    """Generates a shell-ready curl command string for manual debugging."""
    header_args = " ".join([f"-H {shlex.quote(f'{k}: {v}')}" for k, v in headers.items()])
    data_arg = f"-d {shlex.quote(json.dumps(payload))}"
    return f"curl -X POST {shlex.quote(url)} {header_args} {data_arg}"

def manage_failed_request_file(raw_root, job, success=False):
    # Determine folder based on unit: e.g., failed_requests_ADM1 or failed_requests_squares
    unit_label = job.get("unit")

    if not unit_label:
        unit_label = "unknown"   

    fail_dir = Path(raw_root) / f"failed_requests_{unit_label}"
    
    # Sanitize IDs for filenames
    safe_id = str(job['geom_id']).replace(":", "_").replace("/", "_").replace("\\", "_")
    file_name = f"fail_{job['indicator']}_{safe_id}.sh"
    file_path = fail_dir / file_name

    if success:
        if file_path.exists():
            try:
                file_path.unlink()
                # Clean up empty folder to prevent "False Retry Mode" next time
                if not any(fail_dir.iterdir()):
                    fail_dir.rmdir()
            except OSError:
                pass 
    else:
        fail_dir.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w") as f:
            f.write("#!/bin/bash\n")
            f.write(f"{job.get('curl_command', '# No curl')}\n")
        file_path.chmod(0o755)


def ensure_valid_geometry(geometry, logger=None):
    """
    Validates and repairs Shapely geometries or GeoJSON dicts.
    """
    if geometry is None:
        raise ValueError("Geometry is None")

    # Convert GeoJSON dict → shapely
    if isinstance(geometry, dict):
        geometry = shape(geometry)

    # Validate and repair
    if not geometry.is_valid:
        if logger:
            logger.info("Geometry is invalid; attempting to fix with make_valid...")
        geometry = make_valid(geometry)

    if geometry.is_empty:
        raise ValueError("Geometry became empty after validation/repair")
    
    return geometry