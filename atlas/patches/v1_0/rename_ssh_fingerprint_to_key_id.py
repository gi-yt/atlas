"""Rename `Atlas Settings.ssh_fingerprint` → `ssh_key_id`.

The field was misnamed. It never held a cryptographic fingerprint: it holds
the vendor's *handle* for the uploaded SSH key, which Atlas passes straight
through to the provider as `SshKey.vendor_id` (for DigitalOcean that is the
key's id or its fingerprint — either is accepted). The DocType JSON in this
commit ships the new fieldname; this carries the stored value across.

`Atlas Settings` is a Single, so its value lives in the `tabSingles` row
keyed by (`doctype`, `field`) — we rename the `field`, not a column.

Idempotent: re-running once the value is already under the new name is a
no-op. If both names somehow exist (an earlier half-run), keep the legacy
value only when the new one is empty, then drop the legacy row.
"""

import frappe


def execute() -> None:
	has_old = _singles_has_field("ssh_fingerprint")
	has_new = _singles_has_field("ssh_key_id")

	if not has_old:
		return  # nothing to carry over (fresh site, or already migrated)

	if not has_new:
		frappe.db.sql(
			"""UPDATE `tabSingles`
			SET field = 'ssh_key_id'
			WHERE doctype = 'Atlas Settings' AND field = 'ssh_fingerprint'"""
		)
		return

	# Both rows exist: prefer the new value, fall back to the legacy one, then
	# remove the legacy row so the stale name doesn't linger.
	frappe.db.sql(
		"""UPDATE `tabSingles` AS new_row
		JOIN `tabSingles` AS old_row
		  ON old_row.doctype = 'Atlas Settings' AND old_row.field = 'ssh_fingerprint'
		SET new_row.value = old_row.value
		WHERE new_row.doctype = 'Atlas Settings' AND new_row.field = 'ssh_key_id'
		  AND (new_row.value IS NULL OR new_row.value = '')
		  AND old_row.value IS NOT NULL AND old_row.value != ''"""
	)
	frappe.db.sql(
		"""DELETE FROM `tabSingles`
		WHERE doctype = 'Atlas Settings' AND field = 'ssh_fingerprint'"""
	)


def _singles_has_field(field: str) -> bool:
	return bool(
		frappe.db.sql(
			"""SELECT 1 FROM `tabSingles`
			WHERE doctype = 'Atlas Settings' AND field = %s LIMIT 1""",
			field,
		)
	)
