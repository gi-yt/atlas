"""Root Domain DocType — one wildcard zone == one region.

A `Root Domain` row (`blr1.frappe.dev`) owns the regional wildcard cert
(`*.blr1.frappe.dev`) that fronts the proxy fleet in `region`. The controller is
a thin orchestrator: `issue_certificate()` locates (or creates) the domain's
single `TLS Certificate` and delegates issuance to it. The cert→proxy push lives
on `TLS Certificate` ([tls_certificate.py](../tls_certificate/tls_certificate.py)).
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

IMMUTABLE_AFTER_INSERT = ("domain", "region")


class RootDomain(Document):
	def validate(self) -> None:
		self._validate_immutability()

	def _validate_immutability(self) -> None:
		if self.is_new():
			return
		original = self.get_doc_before_save()
		if not original:
			return
		for field in IMMUTABLE_AFTER_INSERT:
			if getattr(self, field) != getattr(original, field):
				frappe.throw(f"{field} is immutable after insert")

	@property
	def common_name(self) -> str:
		"""The wildcard the cert certifies: `*.<domain>`."""
		return f"*.{self.domain}"

	@frappe.whitelist()
	def issue_certificate(self) -> str:
		"""Issue / Renew Certificate button. Find or create this domain's single
		TLS Certificate, then run its issue flow. Returns the cert's name."""
		cert = self._get_or_create_certificate()
		cert.issue()
		return cert.name

	def _get_or_create_certificate(self):
		existing = frappe.db.get_value("TLS Certificate", {"root_domain": self.name}, "name")
		if existing:
			return frappe.get_doc("TLS Certificate", existing)
		cert = frappe.get_doc(
			{
				"doctype": "TLS Certificate",
				"root_domain": self.name,
				"tls_provider": self.tls_provider,
				"status": "Pending",
			}
		).insert(ignore_permissions=True)
		return cert
