"""Tests for price computation logic."""

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.process_prices import (
    AVG_SIZE_M2,
    compute_municipality_averages,
    compute_sector_prices,
    fill_gaps,
)

DATA_DIR = Path(__file__).parent.parent / "data"


# --- Unit tests for compute_sector_prices ---

def make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal DataFrame matching Statbel sector XLSX columns."""
    cols = {
        "CD_STAT_SECTOR": [],
        "CD_YEAR": [],
        "CD_TYPE": [],
        "MS_P50 (MEDIAN_PRICE)": [],
        "MS_TRANSACTIONS": [],
    }
    for r in rows:
        for k in cols:
            cols[k].append(r.get(k))
    return pd.DataFrame(cols)


def test_single_sector_single_type():
    """One sector, one property type, one year -> price = median / avg size."""
    df = make_df([{
        "CD_STAT_SECTOR": "11001A00-",
        "CD_YEAR": 2022,
        "CD_TYPE": "B015",
        "MS_P50 (MEDIAN_PRICE)": 200_000,
        "MS_TRANSACTIONS": 50,
    }])
    result = compute_sector_prices(df)
    assert "11001A00-" in result
    assert result["11001A00-"] == round(200_000 / AVG_SIZE_M2["B015"])  # 2500


def test_weighted_average_across_types():
    """Two property types in same sector -> weighted by transaction count."""
    df = make_df([
        {
            "CD_STAT_SECTOR": "11001A00-",
            "CD_YEAR": 2022,
            "CD_TYPE": "B015",  # apartment, 80m2
            "MS_P50 (MEDIAN_PRICE)": 200_000,
            "MS_TRANSACTIONS": 80,
        },
        {
            "CD_STAT_SECTOR": "11001A00-",
            "CD_YEAR": 2022,
            "CD_TYPE": "B002",  # detached, 150m2
            "MS_P50 (MEDIAN_PRICE)": 450_000,
            "MS_TRANSACTIONS": 20,
        },
    ])
    result = compute_sector_prices(df)
    # Weighted: (200000/80 * 80 + 450000/150 * 20) / (80+20)
    apt_pm2 = 200_000 / 80
    house_pm2 = 450_000 / 150
    expected = round((apt_pm2 * 80 + house_pm2 * 20) / 100)
    assert result["11001A00-"] == expected


def test_excludes_unknown_sectors():
    """Sectors containing 'UNKNOWN' should be excluded."""
    df = make_df([{
        "CD_STAT_SECTOR": "UNKNOWN_SECTOR",
        "CD_YEAR": 2022,
        "CD_TYPE": "B015",
        "MS_P50 (MEDIAN_PRICE)": 200_000,
        "MS_TRANSACTIONS": 50,
    }])
    result = compute_sector_prices(df)
    assert len(result) == 0


def test_excludes_old_years():
    """Years outside RECENT_YEARS should be excluded."""
    df = make_df([{
        "CD_STAT_SECTOR": "11001A00-",
        "CD_YEAR": 2010,
        "CD_TYPE": "B015",
        "MS_P50 (MEDIAN_PRICE)": 200_000,
        "MS_TRANSACTIONS": 50,
    }])
    result = compute_sector_prices(df)
    assert len(result) == 0


def test_excludes_unknown_property_types():
    """Property types not in AVG_SIZE_M2 should be excluded."""
    df = make_df([{
        "CD_STAT_SECTOR": "11001A00-",
        "CD_YEAR": 2022,
        "CD_TYPE": "B00A",  # all houses, not in our map
        "MS_P50 (MEDIAN_PRICE)": 200_000,
        "MS_TRANSACTIONS": 50,
    }])
    result = compute_sector_prices(df)
    assert len(result) == 0


# --- Unit tests for compute_municipality_averages ---

def test_municipality_averages():
    """Sectors with same NIS5 prefix should be averaged."""
    sector_prices = {
        "11001A00-": 2000,
        "11001B00-": 3000,
        "11001C00-": 2500,
        "21001A00-": 4000,
    }
    muni = compute_municipality_averages(sector_prices)
    assert muni["11001"] == 2500  # (2000+3000+2500)/3
    assert muni["21001"] == 4000


# --- Unit tests for fill_gaps ---

def test_fill_from_municipality():
    """Missing sector should get municipality average."""
    sector_prices = {"11001A00-": 2000, "11001B00-": 3000}
    all_codes = ["11001A00-", "11001B00-", "11001C00-"]
    result = fill_gaps(sector_prices, all_codes)
    assert result["11001C00-"] == 2500  # avg of 2000, 3000


def test_fill_from_province():
    """Missing sector in municipality with no data -> province average."""
    sector_prices = {"11001A00-": 2000}
    # 11002 is same province (11) but different municipality
    all_codes = ["11001A00-", "11002A00-"]
    result = fill_gaps(sector_prices, all_codes)
    assert result["11002A00-"] == 2000  # only one sector in province


def test_fill_from_national():
    """Sector in province with no data at all -> national average."""
    sector_prices = {"11001A00-": 2000, "11001B00-": 3000}
    # Province 99 has no data
    all_codes = ["11001A00-", "11001B00-", "99001A00-"]
    result = fill_gaps(sector_prices, all_codes)
    assert result["99001A00-"] == 2500  # avg of 2000, 3000


def test_no_grey_zones():
    """Every sector code must have a price after fill_gaps."""
    sector_prices = {"11001A00-": 2000}
    all_codes = ["11001A00-", "11001B00-", "21001A00-", "99001A00-"]
    result = fill_gaps(sector_prices, all_codes)
    for code in all_codes:
        assert code in result, f"Missing price for {code}"
        assert result[code] > 0, f"Zero price for {code}"


# --- Integration test: real data files ---

@pytest.mark.skipif(
    not (DATA_DIR / "prices.json").exists(),
    reason="prices.json not generated yet",
)
def test_prices_json_format():
    """prices.json should have string keys and positive integer values."""
    with open(DATA_DIR / "prices.json") as f:
        prices = json.load(f)
    assert len(prices) > 0
    for code, price in prices.items():
        assert isinstance(code, str), f"Key {code!r} is not a string"
        assert isinstance(price, int), f"Price for {code} is not an int"
        assert price > 0, f"Price for {code} is <= 0"


@pytest.mark.skipif(
    not (DATA_DIR / "prices.json").exists() or not (DATA_DIR / "sectors.topojson").exists(),
    reason="data files not generated yet",
)
def test_all_sectors_have_prices():
    """Every sector in the TopoJSON must have a matching price -> no grey zones."""
    with open(DATA_DIR / "sectors.topojson") as f:
        topo = json.load(f)
    with open(DATA_DIR / "prices.json") as f:
        prices = json.load(f)

    obj_name = list(topo["objects"].keys())[0]
    missing = []
    for feat in topo["objects"][obj_name]["geometries"]:
        code = feat["properties"].get("sector_code")
        if code and code not in prices:
            missing.append(code)

    assert len(missing) == 0, (
        f"{len(missing)} sectors without price data (first 10): {missing[:10]}"
    )


@pytest.mark.skipif(
    not (DATA_DIR / "prices.json").exists(),
    reason="prices.json not generated yet",
)
def test_all_color_categories_present():
    """All 4 color categories should have at least some sectors."""
    with open(DATA_DIR / "prices.json") as f:
        prices = json.load(f)
    vals = list(prices.values())

    blue = sum(1 for v in vals if v < 2000)
    orange = sum(1 for v in vals if 2000 <= v < 2800)
    green = sum(1 for v in vals if 2800 <= v < 4000)
    red = sum(1 for v in vals if v >= 4000)

    assert blue > 0, "No blue zones (< 2000)"
    assert orange > 0, "No orange zones (2000-2800)"
    assert green > 0, "No green zones (2800-4000)"
    assert red > 0, "No red zones (> 4000)"
