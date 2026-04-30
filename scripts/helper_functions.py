#!/usr/bin/env python3
"""
fetch_geoboundary.py

Fetches *all* available ADM boundaries for a given country code via the geoBoundaries API.

Usage:
    python scripts/fetch_geoboundary.py RWA
"""

import argparse
import os
import sys
import requests
import yaml
import geopandas as gpd
import tempfile
import subprocess
import os
import json
import numpy as np
from shapely.geometry import Polygon, MultiPolygon
from shapely.validation import make_valid
from minio.error import S3Error
from minio import Minio
from hdx.api.configuration import Configuration
from hdx.data.dataset import Dataset
from hdx.data.hdxobject import HDXError
from datetime import datetime, timezone

ASSET_CONFIG_YAML_PATH = os.path.join(os.getcwd(), "configs", "assets_config.yaml")
with open(ASSET_CONFIG_YAML_PATH) as _fp:
    _asset_config = yaml.safe_load(_fp)



def download_from_geoboundaries(list_url, country, level_val, url_val, out_dir):
    print(f"Fetching available levels from {list_url}")
    try:
        resp = requests.get(list_url)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"ERROR fetching boundary list: {e}", file=sys.stderr)
        sys.exit(1)
    entries = resp.json()
    if not isinstance(entries, list) or not entries:
        print(f"No boundaries found for country '{country}'", file=sys.stderr)
        sys.exit(1)

    any_failures = False

    # 2) Iterate over every entry and download
    for entry in entries:
        level = entry.get(level_val)  # e.g. "ADM0", "ADM1", ...
        url = entry.get(url_val)
        if not level or not url:
            print(f"Skipping malformed entry: {entry}", file=sys.stderr)
            any_failures = True
            continue

        out_path = os.path.join(out_dir, f"boundary_{level}.geojson")

        print(f"[{level}] Downloading from {url}")
        try:
            download = requests.get(url)
            download.raise_for_status()
        except requests.RequestException as e:
            print(f"[{level}] ERROR downloading: {e}", file=sys.stderr)
            any_failures = True
            continue

        with open(out_path, "wb") as fp:
            fp.write(download.content)
        print(f"[{level}] Saved to {out_path}")

    if any_failures:
        print("One or more boundaries failed to download.", file=sys.stderr)
        sys.exit(1)
    else:
        print("All available boundaries downloaded successfully.")


def download_from_github(list_url, country, level_val, url_val, out_dir):
    print(f"Fetching available levels from {list_url}")
    try:
        resp = requests.get(list_url)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"ERROR fetching boundary list: {e}", file=sys.stderr)
        sys.exit(1)
    entries = resp.json()
    if not isinstance(entries, list) or not entries:
        print(f"No boundaries found for country '{country}'", file=sys.stderr)
        sys.exit(1)

    any_failures = False

    # 2) Iterate over every entry and download
    for entry in entries:
        level = entry.get(level_val)  # e.g. "ADM0", "ADM1", ...
        dl_url = f"https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/refs/heads/main/releaseData/gbOpen/{country}/{level}/geoBoundaries-{country}-{level}.geojson?download=true"

        if not level or not dl_url:
            print(f"Skipping malformed entry: {entry}", file=sys.stderr)
            any_failures = True
            continue

        out_path = os.path.join(out_dir, f"boundary_{level}.geojson")
        # skip if already present
        if os.path.exists(out_path):
            print(f"[{level}] already exists; skipping")
            continue

        print(f"[{level}] Downloading from {dl_url}")
        try:
            download = requests.get(dl_url)
            download.raise_for_status()
        except requests.RequestException as e:
            print(f"[{level}] ERROR downloading: {e}", file=sys.stderr)
            any_failures = True
            continue

        with open(out_path, "wb") as fp:
            fp.write(download.content)
        print(f"[{level}] Saved to {out_path}")

    if any_failures:
        print("One or more boundaries failed to download.", file=sys.stderr)
        sys.exit(1)
    else:
        print("All available boundaries downloaded successfully.")


# ------ functions for s3 upload -------

def upload_folder(country, context, client: Minio, bucket: str, in_dir: str, dest_prefix: str):
    """
    Upload files in in_dir to S3, but:
    - Detect GeoPackage families (.gpkg, .gpkg-wal, .gpkg-shm)
    - Upload ONLY the .gpkg file (skip auxiliary WAL/SHM files)
    - Upload all other files normally
    """

    # --- Detect all real GPKG files ---
    all_files = [
        f for f in os.listdir(in_dir)
        if os.path.isfile(os.path.join(in_dir, f))
    ]

    gpkg_main_files = [f for f in all_files if f.lower().endswith(".gpkg")]

    for root, dirs, files in os.walk(in_dir):
        for filename in files:
            if filename.endswith(".DS_Store"):
                continue

            # --- Skip GPKG auxiliary files ---
            skip_aux = False
            for gpkg in gpkg_main_files:
                base = os.path.splitext(gpkg)[0]
                if (
                    filename.startswith(base)
                    and (filename.endswith("-wal") or filename.endswith("-shm"))
                ):
                    skip_aux = True
                    break

            if skip_aux:
                context.info(f"[{country}] Skipping auxiliary GPKG file: {filename}")
                continue

            # Build upload paths
            local_path = os.path.join(root, filename)
            rel_path = os.path.relpath(local_path, in_dir)
            object_path = os.path.join(dest_prefix, rel_path).replace("\\", "/")

            # Upload file
            try:
                client.fput_object(
                    bucket_name=bucket,
                    object_name=object_path,
                    file_path=local_path,
                )
                context.info(f"[{country}] Uploaded {local_path} → {bucket}/{object_path}")
            except S3Error as err:
                context.error(f"[{country}] Error uploading {local_path}: {err}")


def upload_to_s3(country: str, paths: dict, context, config_file="configs/s3_config.yaml"):
    """Uploads output files returned by build_outputs_asset to S3."""

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    client = Minio(
        endpoint=config["endpoint"],
        access_key=config["access_key"],
        secret_key=config["secret_key"],
        secure=config.get("secure", True),
    )

    bucket = config["bucket"]
    if not client.bucket_exists(bucket):
        raise RuntimeError(f"Bucket does not exist: {bucket}")

    # Get the output folder (derive from first path)
    first_path = list(paths.values())[0]
    output_folder = os.path.dirname(first_path)

    # Build zip files by topic and file type
    import zipfile
    files_by_topic = {"gpkg": {}, "csv": {}}
    for key, fpath in paths.items():
        if not os.path.exists(fpath):
            continue
        fname = os.path.basename(fpath)
        ext = os.path.splitext(fname)[1].lower()
        if ext in [".gpkg", ".csv"]:
            parts = fname.split("_")
            if len(parts) >= 2:
                topic = parts[1]
                file_type = "gpkg" if ext == ".gpkg" else "csv"
                if topic not in files_by_topic[file_type]:
                    files_by_topic[file_type][topic] = []
                files_by_topic[file_type][topic].append(fname)

    zip_paths = []
    for file_type, topics in files_by_topic.items():
        for topic, files in topics.items():
            zip_name = f"{country}_{topic}_{file_type}.zip"
            zip_path = os.path.join(output_folder, zip_name)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname in files:
                    fpath = os.path.join(output_folder, fname)
                    if os.path.exists(fpath):
                        zf.write(fpath, fname)
            zip_paths.append((zip_name, zip_path))
            context.info(f"[{country}] Created zip: {zip_name}")

    # Upload zip files to S3
    for zip_name, zip_path in zip_paths:
        object_name = f"oqapi_hdx/downloads/{country.upper()}/{zip_name}"
        try:
            client.fput_object(bucket_name=bucket, object_name=object_name, file_path=zip_path)
            context.info(f"[{country}] Uploaded: {object_name}")
        except S3Error as e:
            raise RuntimeError(f"Failed to upload {zip_name}: {e}")

    # Upload other files: figures, pmtiles, parquet
    for key, fpath in paths.items():
        if not os.path.exists(fpath):
            continue
        fname = os.path.basename(fpath)
        ext = os.path.splitext(fname)[1].lower()

        if key == "figures_gzip_path":
            # Upload all .json.gz files from figures directory
            for fig in os.listdir(fpath):
                fig_path = os.path.join(fpath, fig)
                if not os.path.isfile(fig_path):
                    continue
                object_name = f"oqapi_hdx/figures/{country.upper()}/{fig}"
                try:
                    client.fput_object(bucket_name=bucket, object_name=object_name, file_path=fig_path)
                    context.info(f"[{country}] Uploaded: {object_name}")
                except S3Error as e:
                    raise RuntimeError(f"Failed to upload {fig}: {e}")
        elif ext in [".pmtiles", ".parquet"]:
            object_name = f"oqapi_hdx/downloads/{country.upper()}/{fname}"
            try:
                client.fput_object(bucket_name=bucket, object_name=object_name, file_path=fpath)
                context.info(f"[{country}] Uploaded: {object_name}")
            except S3Error as e:
                raise RuntimeError(f"Failed to upload {fname}: {e}")


def upload_stats_to_s3(country: str, file_path: str, log, config_file="configs/s3_config.yaml"):
    """Uploads a single OSM stats file to S3."""

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    client = Minio(
        endpoint=config["endpoint"],
        access_key=config["access_key"],
        secret_key=config["secret_key"],
        secure=config.get("secure", True),
    )

    bucket = config["bucket"]

    if not client.bucket_exists(bucket):
        raise RuntimeError(f"Bucket does not exist: {bucket}")

    fname = os.path.basename(file_path)

    object_name = f"oqapi_hdx/osm_stats/{country.upper()}/{fname}"

    try:
        client.fput_object(
            bucket_name=bucket,
            object_name=object_name,
            file_path=file_path,
            content_type="application/gzip",
        )
        log.info(f"[{country}] Uploaded: {object_name}")

    except S3Error as e:
        raise RuntimeError(f"Failed to upload {fname}: {e}")


# --------function for hdx upload ---------
def generate_links(country_code: str, local_folder: str, context):
    """Return list of (filename, public_download_url) for zip files in the folder."""
    
    links = []
    for fname in os.listdir(local_folder):
        if fname.endswith(".DS_Store"):
            continue
        if fname.lower().endswith(".zip"):
            url = (
                f"https://hot.storage.heigit.org/heigit-hdx-public/oqapi_hdx/downloads/"
                f"{country_code}/{fname}"
            )
            links.append((fname, url))
    context.log.info(f"Links generated for: {links}")
    return links
    
    return links


def create_country_dataset(country_code: str, country_name: str, links, hdx_config, context): 
    dataset_name = f"{country_name} OSM Data quality"
    title = f"{country_name} - OSM Data quality"

    dataset = Dataset()
    dataset["name"] = dataset_name.lower().replace(" ", "-")
    dataset["title"] = title
    dataset["owner_org"] = hdx_config["hdx"]["owner_org"]
    dataset["groups"] = [{"name": hdx_config["hdx"]["owner_org"]}]
    dataset["private"] = hdx_config["hdx"].get("private", True) # final wieder ändern wenn alles online gehen darf
    dataset.set_expected_update_frequency(
        hdx_config["hdx"].get("data_update_frequency", "Every six months")
    )
    dataset["license_id"] = "cc-by-sa"
    dataset["dataset_source"] = "HeiGIT"
    dataset["maintainer"] = hdx_config["hdx"].get("maintainer", "Milena Schnitzler")
    dataset["maintainer_email"] = hdx_config["hdx"].get("maintainer_email", "milena.schnitzler@heigit.org")
    dataset["methodology"] = " Quality analysis of OSM data unsing the ohsome dashboard."
    dataset["notes"] = f"This dataset provides insights into the data quality of [OpenStreetMap](https://www.openstreetmap.org/) (OSM) data in {country_name}."
    dataset["notes"] += " It has been created using the OSM data quality analysis of [ohsome](https://dashboard.ohsome.org/).\n\n"
    dataset["notes"] += " Different indicators are used to asses the data quality depending on the selected topic, for further information regarding the calculation of the quality indicators see the [Github](https://github.com/GIScience/ohsome-quality-api) repository."
    dataset["notes"] += " The OSM data quality analysis is available for different topics either as a CSV or as a Geopackage file."
    dataset["notes"] += " The quality analysis is available in three units: admin level 0, admin level 1 and hexagons."
    dataset["notes"] += " Each zip file contains all three units for the selected topic."
    dataset["notes"] += " The unit of analysis is defined by [geoboundaries](https://www.geoboundaries.org/) country borders.\n\n"

    dataset["notes"] += "Attributes of the CSV/ Geopackage file:\n\n"
    dataset["notes"] += "- **[unit]_id**: Unit of quality analysis.\n\n"
    dataset["notes"] += "- **ADM0_name**: Name of the country.\n\n"
    dataset["notes"] += "- **ADM0_iso**: ISO3 country code.\n\n"
    dataset["notes"] += "- **result_value_[indicator]**: Calculated result of OSM data quality for the respective indicator. Ranges between 0 and 1.\n\n" # Was sagt Wert von 1 genau aus?

    dataset["notes"] += " Different indicators are available for each topic. Check out the [Topic Catalog](https://dashboard.ohsome.org/en/) to see which indicators are relevant for which topic.\n\n"
    dataset["notes"] += "This dataset is one of many [HeiGIT exports on HDX](https://data.humdata.org/organization/heidelberg-institute-for-geoinformation-technology). See the [HeiGIT](https://heigit.org/) website for more information.\n\n"
    dataset["notes"] += "We are looking forward to hearing about your use-case! Feel free to reach out to us and tell us about your research at [communications@heigit.org](mailto:communications@heigit.org) – we would be happy to amplify your work.\n\n"

    tags = hdx_config["hdx"].get("tags", [])
    if tags:
        dataset.add_tags(tags)

    try:
        dataset.add_country_location(country_code)
    except HDXError as e:
        print(f"Warning: {e}")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    dataset["dataset_date"] = f"[{today} TO {today}]"

    for fname, url in links:
        try:
            if "_gpkg.zip" in fname:
                fmt = "zipped geopackage"
            elif "_csv.zip" in fname:
                fmt = "zipped csv"
            else:
                fmt = "zip"
            resource = {
                "name": fname,
                "description": f"{fname} for {country_name}",
                "format": fmt,
                "url": url,
            }
            dataset.add_update_resource(resource)
            context.log.info(f"Resource added: {resource['name']} ({fmt})")
        except Exception as e:
            context.log.error(f"Error while trying to add {fname}: {e}")

    hdx_country_url = dataset.create_in_hdx()
    context.log.info(f"Data set created in hdx under the following url [{hdx_country_url}]")
    return hdx_country_url


def upload_to_hdx(
    country_code: str,
    config_file: str,
    countries_config: str,
    context
):
    """Main entrypoint: upload all risk assessment files for a country to HDX."""

    with open(config_file, "r") as f:
        hdx_config = yaml.safe_load(f)

    # get ful country name
    with open(countries_config, "r") as f:
        countries = yaml.safe_load(f)
    try:
        hdx_country = countries[country_code]["slug"]
        country_name = hdx_country.replace("-", " ").title()
    except KeyError:
        raise ValueError(
            f"Country code '{country_code}' not found in {countries_config}"
        )

    local_folder = os.path.join("data", country_code, "Output")

    if not os.path.isdir(local_folder):
        raise FileNotFoundError(f"Folder not found: {local_folder}")

    links = generate_links(country_code, local_folder, context)

    Configuration.create(
        hdx_site=hdx_config["hdx"]["site"],
        user_agent="HDXDataSeriesScript",
        hdx_key=hdx_config["hdx"]["api_key"],
        hdx_url=hdx_config["hdx"]["url"],
    )

    return create_country_dataset(country_code, country_name, links, hdx_config, context), links


def filter_small_islands(
    geom,
    min_area_sq_deg: float = 0.01,
    min_area_ratio: float = None
):
    """
    Remove small islands from a MultiPolygon geometry.
    
    Parameters
    ----------
    geom : Geometry
        The geometry to filter.
    min_area_sq_deg : float
        Minimum area threshold in square degrees. Polygons below this area
        will be removed. Default 0.01 sq deg (~1.1km x 1.1km at equator).
        Set to None to disable absolute threshold.
    min_area_ratio : float, optional
        Minimum area as ratio of the largest polygon (e.g., 0.01 = keep only
        islands at least 1% of the largest island's area). If set, this is
        combined with min_area_sq_deg using OR logic.
    """
    if geom.is_empty:
        return geom

    if isinstance(geom, Polygon):
        return geom

    if isinstance(geom, MultiPolygon):
        polygons = list(geom.geoms)
        
        if len(polygons) <= 1:
            return geom
        
        areas = [p.area for p in polygons]
        max_area = max(areas)
        
        if min_area_ratio is not None:
            min_area_for_ratio = max_area * min_area_ratio
            effective_min = min_area_sq_deg if min_area_sq_deg else 0
            threshold = max(effective_min, min_area_for_ratio)
        else:
            threshold = min_area_sq_deg
        
        filtered = [p for p, area in zip(polygons, areas) if area >= threshold]
        
        if not filtered:
            return MultiPolygon([max(polygons, key=lambda p: p.area)])
        
        if len(filtered) == 1:
            return filtered[0]
        
        return MultiPolygon(filtered)

    return geom


def simplify_geometries(path: str, min_island_area: float = 0.01):
    """
    Remove interior geometries from country boundary like lakes or rivers
    and filter out small islands that cause API issues.
    
    Parameters
    ----------
    path : str
        Path to the GeoJSON file.
    min_island_area : float
        Minimum area threshold in square degrees for keeping islands.
        Default 0.01 sq deg. Set to None to disable island filtering.
    """
    import os
    os.environ["OGR_GEOJSON_MAX_OBJ_SIZE"] = "0"

    gdf = gpd.read_file(path)

    gdf["geometry"] = gdf.geometry.apply(make_valid)

    if min_island_area is not None:
        gdf["geometry"] = gdf.geometry.apply(lambda g: filter_small_islands(g, min_island_area))

    def strip_holes(geom):
        try:
            if geom.is_empty:
                return geom

            if isinstance(geom, Polygon):
                return Polygon(geom.exterior)

            if isinstance(geom, MultiPolygon):
                cleaned = []

                for poly in geom.geoms:
                    is_inside = False

                    # only use polygons that are not within another geometry like lakes or islands in lakes
                    for other in geom.geoms:
                        if poly == other:
                            continue

                        outer_shell = Polygon(other.exterior)

                        if poly.within(outer_shell):
                            is_inside = True
                            break

                    if not is_inside:
                        cleaned.append(Polygon(poly.exterior))

                return MultiPolygon(cleaned)

            raise TypeError(f"Geometry from type {type(geom)} is not supported.")

        except TypeError as e:
            print(f"The geometry is not a Polygon or Multipolygon {e}")

    gdf["geometry"] = gdf.geometry.apply(strip_holes)

    gdf.to_file(path, driver="GeoJSON")


# Count vertices safely
def count_vertices(geom):
    if geom.geom_type == "Polygon":
        return len(geom.exterior.coords)
    elif geom.geom_type == "MultiPolygon":
        return sum(len(poly.exterior.coords) for poly in geom.geoms)
    else:
        return 0


def geojson_to_multilayer_pmtiles(
    layers: dict,
    pmtiles_path: str,
    minzoom: int = 1,
    maxzoom: int = 14,
):

    with tempfile.TemporaryDirectory() as tmpdir:

        layer_args = []

        for layer_name, geojson_path in layers.items():

            # ---- CHECK FEATURE COUNT ----
            try:
                gdf = gpd.read_file(geojson_path)
                
            except Exception as e:
                print(f"Skipping layer '{layer_name}' (cannot read GeoJSON): {e}")
                continue

            if len(gdf) == 0:
                print(f"Skipping layer '{layer_name}' (0 features)")
                continue

            if gdf.crs is None:
                print(f"Warning: {geojson_path} has no CRS, skipping")
                continue

            if gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(4326)

            layer_args.extend([
                "-L", f"{layer_name}:{geojson_path}"
            ])

        if not layer_args:
            print("No valid layers found for PMTiles generation")
            return  # Skip PMTiles creation if no valid layers
            
        # Remove existing output file if it exists
        if os.path.exists(pmtiles_path):
            os.remove(pmtiles_path)

        tippecanoe_cmd = [
            "tippecanoe",
            "-o", pmtiles_path,
            "--force",
            "-Z", str(minzoom),
            "-z", str(maxzoom),
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            *layer_args
        ]

        try:
            subprocess.run(tippecanoe_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print(f"tippecanoe failed with exit code {e.returncode}")
            print(f"stdout: {e.stdout}")
            print(f"stderr: {e.stderr}")
            raise

def get_dynamic_resolutions(gdf, _asset_config):
    """
    Determines grid parameters using an accurate equal-area projection 
    calculation before checking config overrides.
    """
    # 1. Calculate accurate area using Mollweide projection (Units: Meters)
    # We use a copy so we don't accidentally modify the original GDF's CRS
    area_m2 = gdf.to_crs("ESRI:54009").area.sum() 
    area_km2 = area_m2 / 1_000_000

    # 2. Define smart defaults based on area (same thresholds as before)
    if area_km2 < 50_000:
        smart_sq, smart_h3 = 0.05, 6
    elif area_km2 < 500_000:
        smart_sq, smart_h3 = 0.1, 5
    elif area_km2 < 5_000_000:
        smart_sq, smart_h3 = 0.3, 4
    else:
        smart_sq, smart_h3 = 0.8, 3

    # 3. Extract overrides from the 'grids' config block
    conf_sq = _asset_config.get("square", {}).get("resolution_deg")
    conf_h3 = _asset_config.get("h3", {}).get("zoom_level")

    return {
        "square": conf_sq if conf_sq is not None else smart_sq,
        "h3": conf_h3 if conf_h3 is not None else smart_h3
    }
