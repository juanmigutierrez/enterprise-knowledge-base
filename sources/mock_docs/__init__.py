"""Mock docs service: a stand-in for SharePoint or Confluence.

It holds HR pages with permissions and records every change in a change feed,
in its own format. Turning that format into the product's ``ChangeEvent`` is the
connector's job (T1, plan step S2), which is why nothing here uses ``ekb``.
"""
