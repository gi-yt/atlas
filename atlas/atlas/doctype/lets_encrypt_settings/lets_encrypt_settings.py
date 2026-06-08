"""Lets Encrypt Settings — ACME account config for the Let's Encrypt issuer.

Pure storage read by `LetsEncryptProvider` (ACME directory, account email, ToS
agreement). The DocType name drops the apostrophe in "Let's Encrypt" so its
scrubbed module path is a legal Python identifier (`lets_encrypt_settings`); the
provider's Select value keeps the apostrophe since that is data, not a module.
"""

from __future__ import annotations

from frappe.model.document import Document


class LetsEncryptSettings(Document):
	pass
