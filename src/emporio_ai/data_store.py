"""Loading, typing and validation of the six operational CSV snapshots.

The CSVs are treated as a read-only snapshot. Validation never mutates the source
files; issues are collected in a ``DataQualityReport`` and surfaced in the docs/UI.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import DATASET_FILES

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "products": {"product_id", "price_brl", "name", "category_id", "description", "stock_quantity", "status"},
    "categories": {"category_id", "name"},
    "promotions": {"promotion_id", "product_id", "discount_percent", "description", "is_active"},
    "orders": {"order_id", "customer_id", "order_date", "status", "total_brl", "payment_method"},
    "order_items": {"order_id", "quantity", "product_id"},
    "customers": {"customer_id", "email"},
}

SELLABLE_STATUS = "active"


class DataLoadError(RuntimeError):
    """Raised when a dataset is missing or structurally unusable."""


@dataclass
class DataQualityReport:
    row_counts: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.issues.append(message)
        logger.info("data-quality: %s", message)


def _find_file(data_dir: Path, dataset: str) -> Path:
    for name in DATASET_FILES[dataset]:
        path = data_dir / name
        if path.is_file():
            return path
    raise DataLoadError(f"Dataset '{dataset}' not found in {data_dir}")


class DataStore:
    """In-memory, typed copy of the CSV snapshot with integrity checks."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.report = DataQualityReport()
        self.tables: dict[str, pd.DataFrame] = {}
        for dataset in DATASET_FILES:
            path = _find_file(self.data_dir, dataset)
            try:
                frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
            except Exception as exc:  # pragma: no cover - surfaced to caller
                raise DataLoadError(f"Could not read {path.name}: {exc}") from exc
            missing = REQUIRED_COLUMNS[dataset] - set(frame.columns)
            if missing:
                raise DataLoadError(f"{path.name} is missing columns: {sorted(missing)}")
            self.tables[dataset] = frame
        self._coerce_types()
        self._validate()

    # ------------------------------------------------------------------ typing
    def _to_int(self, table: str, column: str) -> None:
        frame = self.tables[table]
        values = pd.to_numeric(frame[column].str.strip(), errors="coerce")
        bad = int(values.isna().sum())
        if bad:
            self.report.add(f"{table}.{column}: {bad} non-numeric value(s) dropped")
        frame = frame[values.notna()].copy()
        frame[column] = values[values.notna()].astype(int)
        self.tables[table] = frame

    def _to_float(self, table: str, column: str) -> None:
        frame = self.tables[table]
        values = pd.to_numeric(frame[column].str.strip(), errors="coerce")
        bad = int(values.isna().sum())
        if bad:
            self.report.add(f"{table}.{column}: {bad} non-numeric value(s) dropped")
        frame = frame[values.notna()].copy()
        frame[column] = values[values.notna()].astype(float).round(2)
        self.tables[table] = frame

    def _coerce_types(self) -> None:
        for table, column in [("products", "product_id"), ("products", "category_id"),
                              ("products", "stock_quantity"), ("categories", "category_id"),
                              ("promotions", "promotion_id"), ("promotions", "product_id"),
                              ("orders", "order_id"), ("orders", "customer_id"),
                              ("order_items", "order_id"), ("order_items", "product_id"),
                              ("order_items", "quantity"), ("customers", "customer_id")]:
            self._to_int(table, column)
        for table, column in [("products", "price_brl"), ("promotions", "discount_percent"),
                              ("orders", "total_brl")]:
            self._to_float(table, column)
        promos = self.tables["promotions"]
        promos["is_active"] = promos["is_active"].str.strip().str.lower().isin({"1", "true", "yes", "sim"})
        for table in ("products", "orders"):
            self.tables[table]["status"] = self.tables[table]["status"].str.strip().str.lower()
        customers = self.tables["customers"]
        customers["email_norm"] = customers["email"].str.strip().str.lower()

    # -------------------------------------------------------------- validation
    def _validate(self) -> None:
        products, promos = self.tables["products"], self.tables["promotions"]
        for table, key in [("products", "product_id"), ("categories", "category_id"),
                           ("promotions", "promotion_id"), ("orders", "order_id"),
                           ("customers", "customer_id")]:
            dupes = int(self.tables[table][key].duplicated().sum())
            if dupes:
                self.report.add(f"{table}: {dupes} duplicated {key} value(s); first occurrence kept")
                self.tables[table] = self.tables[table].drop_duplicates(key, keep="first")

        if (products["price_brl"] <= 0).any():
            self.report.add("products: non-positive prices found; those products are not sellable")
        negative = products["stock_quantity"] < 0
        if negative.any():
            self.report.add(f"products: {int(negative.sum())} negative stock value(s) treated as 0")
            products.loc[negative, "stock_quantity"] = 0

        out_of_range = (promos["discount_percent"] <= 0) | (promos["discount_percent"] >= 100)
        if out_of_range.any():
            self.report.add(f"promotions: {int(out_of_range.sum())} discount(s) outside (0, 100) ignored")
            self.tables["promotions"] = promos[~out_of_range].copy()

        known_products = set(products["product_id"])
        known_categories = set(self.tables["categories"]["category_id"])
        for table in ("promotions", "order_items"):
            orphan = ~self.tables[table]["product_id"].isin(known_products)
            if orphan.any():
                self.report.add(f"{table}: {int(orphan.sum())} row(s) reference unknown products")
        orphan_cat = ~products["category_id"].isin(known_categories)
        if orphan_cat.any():
            self.report.add(f"products: {int(orphan_cat.sum())} product(s) with unknown category")
        empty_categories = known_categories - set(products["category_id"])
        if empty_categories:
            names = self.tables["categories"].set_index("category_id").loc[sorted(empty_categories), "name"]
            self.report.add("categories without any product in the snapshot: " + ", ".join(names))

        active_promos = self.tables["promotions"][self.tables["promotions"]["is_active"]]
        multi = active_promos["product_id"].value_counts()
        if (multi > 1).any():
            self.report.add("promotions: products with more than one active promotion - only the "
                            "largest discount is applied (promotions are not cumulative)")
        self.report.add("promotions.csv has no start/end dates: validity is taken from is_active only")

        flagged = products[(products["status"] != SELLABLE_STATUS) & (products["stock_quantity"] > 0)]
        if len(flagged):
            self.report.add(f"products: {len(flagged)} non-active product(s) still show stock; "
                            "they are never offered as available")

        orders, items = self.tables["orders"], self.tables["order_items"]
        orders_without_items = set(orders["order_id"]) - set(items["order_id"])
        if orders_without_items:
            self.report.add(f"orders: {len(orders_without_items)} order(s) without items")
        priced = items.merge(products[["product_id", "price_brl"]], on="product_id", how="left")
        priced["line"] = priced["price_brl"] * priced["quantity"]
        recomputed = priced.groupby("order_id")["line"].sum()
        mismatched = [int(oid) for oid, total in orders.set_index("order_id")["total_brl"].items()
                      if oid in recomputed and abs(recomputed[oid] - total) > 0.05]
        if mismatched:
            self.report.add(f"orders: recorded total differs from current list prices for order(s) "
                            f"{mismatched}; the recorded total is reported as-is")
        self.report.row_counts = {name: len(frame) for name, frame in self.tables.items()}

    # ---------------------------------------------------------------- helpers
    def table(self, name: str) -> pd.DataFrame:
        """Return a copy so callers can never mutate the in-memory snapshot."""
        return self.tables[name].copy()

    @staticmethod
    def parse_specs(raw: str) -> dict[str, str]:
        try:
            value = json.loads(raw) if raw else {}
            return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
        except (json.JSONDecodeError, AttributeError):
            return {}
