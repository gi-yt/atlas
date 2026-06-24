import dataclasses

import frappe
from frappe.model.document import Document


class ScalewaySettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		billing: DF.Literal["hourly", "monthly"]
		default_image: DF.Link
		default_size: DF.Link
		organization_id: DF.Data | None
		project_id: DF.Data
		secret_key: DF.Password
		ssh_key_id: DF.Data | None
		zone: DF.Data
	# end: auto-generated types

	@frappe.whitelist()
	def test_connection(self) -> dict:
		"""Ping Scaleway using the Scaleway provider's authenticate()."""
		from atlas.atlas import providers

		result = providers.for_provider_type("Scaleway").authenticate()
		return dataclasses.asdict(result)
