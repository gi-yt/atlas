import dataclasses

import frappe
from frappe.model.document import Document


class DigitalOceanSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_token: DF.Password
		default_image: DF.Link
		default_size: DF.Link
		region: DF.Data
		ssh_key_id: DF.Data | None
	# end: auto-generated types

	@frappe.whitelist()
	def test_connection(self) -> dict:
		"""Ping DigitalOcean using the DigitalOcean provider's authenticate()."""
		from atlas.atlas import providers

		result = providers.for_provider_type("DigitalOcean").authenticate()
		return dataclasses.asdict(result)
