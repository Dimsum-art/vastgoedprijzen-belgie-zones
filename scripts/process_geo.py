"""Download and process Statbel statistical sector boundaries into TopoJSON."""

import json
import sys
from pathlib import Path

import geopandas as gpd
import topojson as tp

SHAPEFILE_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/"
    "Statistische%20sectoren/"
    "sh_statbel_statistical_sectors_31370_20250101.shp.zip"
)

DATA_DIR = Path(__file__).parent.parent / "data"
SHP_ZIP = DATA_DIR / "sectors_shp.zip"
SHP_DIR = DATA_DIR / "sectors_shp"
OUTPUT = DATA_DIR / "sectors.topojson"

# Simplification tolerance in degrees (~0.0005° ≈ 50m at Belgian latitudes)
SIMPLIFY_TOLERANCE = 0.0005
TOPOJSON_QUANTIZATION = 1e5


def download_shapefile() -> None:
    """Download sector boundaries if not already present."""
    if SHP_ZIP.exists():
        print(f"Shapefile already downloaded: {SHP_ZIP}")
        return

    import requests

    print("Downloading sector boundaries...")
    resp = requests.get(SHAPEFILE_URL, timeout=300)
    resp.raise_for_status()
    SHP_ZIP.write_bytes(resp.content)
    print(f"Saved to {SHP_ZIP} ({SHP_ZIP.stat().st_size / 1e6:.1f} MB)")


def extract_shapefile() -> Path:
    """Extract shapefile zip and return path to .shp file."""
    import zipfile

    if not SHP_DIR.exists():
        print("Extracting shapefile...")
        with zipfile.ZipFile(SHP_ZIP) as zf:
            zf.extractall(SHP_DIR)

    # Find the .shp file (may be nested)
    shp_files = list(SHP_DIR.rglob("*.shp"))
    if not shp_files:
        raise FileNotFoundError(f"No .shp file found in {SHP_DIR}")
    # Pick the one that's actually a file (not a directory named .shp)
    for shp in shp_files:
        if shp.is_file() and shp.stat().st_size > 0:
            return shp
    raise FileNotFoundError(f"No valid .shp file in {SHP_DIR}")


def process_shapefile(shp_path: Path) -> None:
    """Load, reproject, simplify, and convert to TopoJSON."""
    print(f"Loading shapefile: {shp_path}")
    gdf = gpd.read_file(shp_path)
    print(f"  Loaded {len(gdf)} sectors, CRS: {gdf.crs}")

    # Reproject to WGS84
    print("Reprojecting to EPSG:4326 (WGS84)...")
    gdf = gdf.to_crs(epsg=4326)

    # Select and rename columns
    col_map = {
        "CS01012025": "sector_code",
        "T_SEC_NL": "sector_name",
        "CNIS5_2025": "nis5",
        "T_MUN_NL": "municipality_nl",
        "T_MUN_FR": "municipality_fr",
    }
    gdf = gdf.rename(columns=col_map)[list(col_map.values()) + ["geometry"]]

    # Drop sectors with null geometry
    before = len(gdf)
    gdf = gdf[gdf.geometry.notna()].copy()
    if len(gdf) < before:
        print(f"  Dropped {before - len(gdf)} sectors with null geometry")

    print(f"  {len(gdf)} sectors with geometry")

    # Simplify geometries
    print(f"Simplifying geometries (tolerance={SIMPLIFY_TOLERANCE})...")
    gdf["geometry"] = gdf["geometry"].simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)

    # Convert to TopoJSON
    print("Converting to TopoJSON...")
    topo = tp.Topology(gdf, toposimplify=0, topoquantize=TOPOJSON_QUANTIZATION)
    topo_dict = topo.to_dict()

    # Write output
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(topo_dict, f)

    size_mb = OUTPUT.stat().st_size / 1e6
    print(f"Written to {OUTPUT} ({size_mb:.1f} MB)")

    if size_mb > 15:
        print(f"WARNING: File is {size_mb:.1f} MB (target < 15 MB)")
    else:
        print("File size OK")

    return gdf


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    download_shapefile()
    shp_path = extract_shapefile()
    gdf = process_shapefile(shp_path)

    # Print stats
    print(f"\nStats:")
    print(f"  Total sectors: {len(gdf)}")
    print(f"  Unique municipalities: {gdf['nis5'].nunique()}")
    print(f"  Sample codes: {gdf['sector_code'].head(5).tolist()}")
