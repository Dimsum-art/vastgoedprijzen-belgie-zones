"""Process Statbel sector-level real estate data into price-per-m2 JSON."""

import json
from pathlib import Path

import pandas as pd
import requests

STATBEL_SECTOR_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/"
    "Immo%20sector/TF_IMMO_SECTOR.xlsx"
)

# Estimated average living space (m2) per Statbel property type.
# Sources: Statbel housing quality survey, EPC certificates averages.
AVG_SIZE_M2: dict[str, float] = {
    "B001": 110.0,  # Semi-detached/terraced houses (2-3 facades)
    "B002": 150.0,  # Detached houses (4+ facades)
    "B015": 80.0,   # Apartments
    # B00A (all houses) skipped — overlaps B001+B002
}

RECENT_YEARS = range(2019, 2024)  # 2019-2023

REQUIRED_COLUMNS = {
    "CD_STAT_SECTOR", "CD_YEAR", "CD_TYPE",
    "MS_P50 (MEDIAN_PRICE)", "MS_TRANSACTIONS",
}

DATA_DIR = Path(__file__).parent.parent / "data"
XLSX_PATH = DATA_DIR / "TF_IMMO_SECTOR.xlsx"
OUTPUT_PATH = DATA_DIR / "prices.json"


def validate_columns(df: pd.DataFrame) -> None:
    """Raise if expected Statbel columns are missing."""
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"Statbel XLSX schema changed — missing columns: {missing}. "
            f"Available: {sorted(df.columns)}"
        )


def download_price_data() -> None:
    """Download Statbel sector price XLSX if not present."""
    if XLSX_PATH.exists():
        print(f"Price data already downloaded: {XLSX_PATH}")
        return

    print("Downloading Statbel sector price data...")
    resp = requests.get(STATBEL_SECTOR_URL, timeout=120)
    resp.raise_for_status()
    XLSX_PATH.write_bytes(resp.content)
    print(f"Saved to {XLSX_PATH}")


def compute_sector_prices(df: pd.DataFrame) -> dict[str, int]:
    """Compute price per m2 of living space for each sector.

    Uses median total prices divided by estimated average property size,
    weighted by transaction count across property types and recent years.
    """
    mask = (
        df["CD_YEAR"].isin(RECENT_YEARS)
        & df["CD_TYPE"].isin(AVG_SIZE_M2.keys())
        & df["MS_P50 (MEDIAN_PRICE)"].notna()
        & (df["MS_P50 (MEDIAN_PRICE)"] > 0)
        & df["MS_TRANSACTIONS"].notna()
        & (df["MS_TRANSACTIONS"] >= 1)
        & ~df["CD_STAT_SECTOR"].str.contains("UNKNOWN")
    )
    recent = df[mask].copy()
    if recent.empty:
        return {}

    # Price per m2 of living space
    recent["price_m2"] = recent["CD_TYPE"].map(AVG_SIZE_M2)
    recent["price_m2"] = recent["MS_P50 (MEDIAN_PRICE)"] / recent["price_m2"]

    # Weighted average per sector
    result: dict[str, int] = {}
    for sector, group in recent.groupby("CD_STAT_SECTOR"):
        total_txns = group["MS_TRANSACTIONS"].sum()
        if total_txns > 0:
            weighted_price = (
                (group["price_m2"] * group["MS_TRANSACTIONS"]).sum() / total_txns
            )
            result[sector] = round(weighted_price)

    return result


def compute_municipality_averages(sector_prices: dict[str, int]) -> dict[str, int]:
    """Compute municipality averages from sectors that have data.

    NIS5 = first 5 chars of sector code.
    """
    muni_totals: dict[str, list[int]] = {}
    for code, price in sector_prices.items():
        nis5 = code[:5]
        muni_totals.setdefault(nis5, []).append(price)

    return {
        nis5: round(sum(vals) / len(vals))
        for nis5, vals in muni_totals.items()
    }


def fill_gaps(
    sector_prices: dict[str, int],
    all_sector_codes: list[str],
) -> dict[str, int]:
    """Fill missing sectors: municipality avg -> province avg -> national avg."""
    result = dict(sector_prices)

    # Municipality averages from sectors with data
    muni_avg = compute_municipality_averages(sector_prices)

    # Province averages (first 2 digits of NIS5)
    province_totals: dict[str, list[int]] = {}
    for code, price in sector_prices.items():
        province = code[:2]
        province_totals.setdefault(province, []).append(price)
    province_avg = {
        prov: round(sum(vals) / len(vals))
        for prov, vals in province_totals.items()
    }

    # National average
    all_vals = list(sector_prices.values())
    national_avg = round(sum(all_vals) / len(all_vals)) if all_vals else 2500

    muni_filled = 0
    prov_filled = 0
    nat_filled = 0

    for code in all_sector_codes:
        if code in result:
            continue
        nis5 = code[:5]
        province = code[:2]

        if nis5 in muni_avg:
            result[code] = muni_avg[nis5]
            muni_filled += 1
        elif province in province_avg:
            result[code] = province_avg[province]
            prov_filled += 1
        else:
            result[code] = national_avg
            nat_filled += 1

    print(f"  Filled from municipality avg: {muni_filled}")
    print(f"  Filled from province avg: {prov_filled}")
    print(f"  Filled from national avg: {nat_filled}")
    return result


def print_stats(prices: dict[str, int], total_sectors: int) -> None:
    """Print distribution statistics."""
    vals = sorted(prices.values())
    n = len(vals)
    print(f"\n=== Price Distribution ===")
    print(f"  Total sectors with price: {n} / {total_sectors} ({100*n/total_sectors:.1f}%)")
    print(f"  Range: {vals[0]} - {vals[-1]} EUR/m2")
    print(f"  Median: {vals[n//2]} EUR/m2")
    print(f"  Mean: {round(sum(vals)/n)} EUR/m2")

    blue = sum(1 for v in vals if v < 2000)
    orange = sum(1 for v in vals if 2000 <= v < 2800)
    green = sum(1 for v in vals if 2800 <= v < 4000)
    red = sum(1 for v in vals if v >= 4000)
    print(f"\n=== Color Categories ===")
    print(f"  Blue   (< 2000): {blue:5d} ({100*blue/n:.1f}%)")
    print(f"  Orange (2000-2800): {orange:5d} ({100*orange/n:.1f}%)")
    print(f"  Green  (2800-4000): {green:5d} ({100*green/n:.1f}%)")
    print(f"  Red    (> 4000): {red:5d} ({100*red/n:.1f}%)")


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    download_price_data()

    # Load sector codes from TopoJSON
    topojson_path = DATA_DIR / "sectors.topojson"
    if topojson_path.exists():
        with open(topojson_path) as f:
            topo = json.load(f)
        obj_name = list(topo["objects"].keys())[0]
        all_sector_codes = [
            feat["properties"]["sector_code"]
            for feat in topo["objects"][obj_name]["geometries"]
            if feat["properties"].get("sector_code")
        ]
        print(f"Loaded {len(all_sector_codes)} sector codes from TopoJSON")
    else:
        print("WARNING: sectors.topojson not found — run process_geo.py first")
        all_sector_codes = []

    # Compute sector prices
    print("\nProcessing price data...")
    df = pd.read_excel(XLSX_PATH)
    validate_columns(df)
    sector_prices = compute_sector_prices(df)
    print(f"  Direct sector prices: {len(sector_prices)}")

    # Fill gaps
    if all_sector_codes:
        sector_prices = fill_gaps(sector_prices, all_sector_codes)

    # Write output
    with open(OUTPUT_PATH, "w") as f:
        json.dump(sector_prices, f, sort_keys=True)
    print(f"\nWritten to {OUTPUT_PATH}")

    if all_sector_codes:
        print_stats(sector_prices, len(all_sector_codes))
