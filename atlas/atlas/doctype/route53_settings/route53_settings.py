"""Route53 Settings — AWS Route 53 credentials, twin of DigitalOcean Settings.

Pure storage: the secret is read via `atlas.atlas.secrets.get_secret` by
`Route53DnsProvider`. No controller logic.
"""

from __future__ import annotations

from frappe.model.document import Document


class Route53Settings(Document):
	pass
