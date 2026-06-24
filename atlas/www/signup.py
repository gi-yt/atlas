"""`/signup` — the public on-ramp page (spec/14-self-serve.md, Contract C).

The one guest-reachable form in Atlas: email + subdomain. It posts to
`atlas.atlas.api.signup.request_site` (a guest-allowed whitelisted method), which
holds the intent as a `Site Request` and emails a verification link. This page
only renders the form and the domain suffix so the user sees the FQDN they're
claiming (`acme.<domain>`); no site/VM work happens here (it waits for the
verification click — see `verify.py`).
"""

import frappe

from atlas.atlas.placement import active_root_domain

no_cache = 1


def get_context(context):
	"""Hand the form the domain suffix (so it can preview `<label>.<domain>`). A
	logged-in user skips signup — bounce them off the public form."""
	if frappe.session.user != "Guest":
		frappe.local.flags.redirect_location = "/app"
		raise frappe.Redirect
	# Best-effort: if no Root Domain is configured the suffix preview is blank
	# (the API still fails loud on submit). Don't 500 the public page over it.
	try:
		context.domain = active_root_domain().domain
	except frappe.ValidationError:
		context.domain = ""
	return context
