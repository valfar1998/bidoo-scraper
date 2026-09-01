"""Test end-to-end: dry-run pipeline e haircut regressivo."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from catalog_store import upsert_many
from dry_run import is_dry_run
from haircut_model import append_sale_sample, regression_haircut_adjustment, risk_coefficients_from_data
from inventory import (
    _load_calibration,
    _save_calibration,
    _update_category_calibration,
    category_haircut_adjustment,
    category_risk_coefficients,
    ensure_inventory_schema,
)
from listing import SourceListing
from telegram_notifier import send_telegram_message


class DryRunPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_is_dry_run_flag(self) -> None:
        os.environ["DRY_RUN"] = "true"
        self.assertTrue(is_dry_run())
        os.environ["DRY_RUN"] = "false"
        self.assertFalse(is_dry_run())

    def test_telegram_skipped_in_dry_run(self) -> None:
        os.environ["DRY_RUN"] = "true"
        with patch("telegram_notifier.requests.post") as post:
            send_telegram_message("token", "123", "ciao test")
            post.assert_not_called()

    def test_catalog_upsert_skipped_in_dry_run(self) -> None:
        os.environ["DRY_RUN"] = "true"
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DATABASE_PATH"] = os.path.join(tmp, "test.db")
            listing = SourceListing(
                source="prezzishock",
                listing_id="lot-1",
                title="Test lot",
                url="https://example.com/lot-1",
                current_price_eur=12.0,
            )
            count = upsert_many([listing])
            self.assertEqual(count, 1)
            from catalog_store import listings_closing_within

            listings_closing_within("prezzishock", 1)
            from database import connect, ensure_db

            ensure_db()
            with connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM catalog_listings"
                ).fetchone()
            self.assertEqual(int(row["n"]), 0)

    @patch("smart_polling.fetch_source")
    @patch("smart_polling.open_fetcher")
    def test_discovery_dry_run(self, mock_fetcher: MagicMock, mock_source: MagicMock) -> None:
        os.environ["DRY_RUN"] = "true"
        listing = SourceListing(
            source="prezzishock",
            listing_id="dry-1",
            title="Dry run lot",
            url="https://example.com/dry-1",
            current_price_eur=20.0,
        )
        mock_fetcher.return_value.__enter__.return_value = object()
        mock_source.return_value = [listing]
        from smart_polling import run_discovery

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DATABASE_PATH"] = os.path.join(tmp, "dry.db")
            count = run_discovery("prezzishock")
            self.assertEqual(count, 1)


class HaircutRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_regression_increases_haircut_on_overestimate(self) -> None:
        data: dict = {"n": 0, "err_sum": 0.0, "haircut_adj": 0.0, "samples": []}
        for est, actual in [(80, 40), (70, 35), (90, 45)]:
            data = append_sale_sample(data, estimated=est, actual=actual)
        adj = regression_haircut_adjustment(data)
        self.assertGreater(adj, 0.02)

    def test_risk_coefficients_penalize_overoptimism(self) -> None:
        data: dict = {"n": 0, "err_sum": 0.0, "haircut_adj": 0.0, "samples": []}
        for est, actual in [(100, 50), (80, 30), (120, 55)]:
            data = append_sale_sample(data, estimated=est, actual=actual)
        risk = risk_coefficients_from_data(data)
        self.assertGreater(risk.haircut_adj, 0.0)
        self.assertLess(risk.bid_discount, 1.0)
        self.assertGreater(risk.roi_penalty_pct, 0.0)

    def test_category_calibration_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DATABASE_PATH"] = os.path.join(tmp, "calib.db")
            ensure_inventory_schema()
            for est, actual in [(60, 30), (50, 20), (70, 25)]:
                _update_category_calibration("moda", est, actual)
            adj = category_haircut_adjustment("moda")
            self.assertGreater(adj, 0.0)
            risk = category_risk_coefficients("moda")
            self.assertLess(risk.bid_discount, 1.0)
            stored = _load_calibration("moda")
            self.assertGreaterEqual(len(stored.get("samples") or []), 3)


if __name__ == "__main__":
    unittest.main()
