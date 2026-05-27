import unittest

import frappe

from atlas.atlas.api import workspace


class TestBootstrapStatus(unittest.TestCase):
	def test_returns_all_four_counts(self) -> None:
		status = workspace.bootstrap_status()
		self.assertEqual(
			set(status.keys()),
			{"providers", "servers", "images", "virtual_machines"},
		)

	def test_counts_match_frappe_db_count(self) -> None:
		status = workspace.bootstrap_status()
		self.assertEqual(status["providers"], frappe.db.count("Server Provider"))
		self.assertEqual(status["servers"], frappe.db.count("Server"))
		self.assertEqual(status["images"], frappe.db.count("Virtual Machine Image"))
		self.assertEqual(status["virtual_machines"], frappe.db.count("Virtual Machine"))

	def test_counts_are_integers(self) -> None:
		for value in workspace.bootstrap_status().values():
			self.assertIsInstance(value, int)
			self.assertGreaterEqual(value, 0)
