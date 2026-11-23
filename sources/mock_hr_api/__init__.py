"""Mock HR record API: a stand-in for Workday or Salesforce.

Unlike the docs mock it pushes nothing: a reader must *ask* what changed
(``GET /employees?updated_since=...``). And it never reports deletes: a deleted
record simply stops appearing. The only way to notice is to compare two full
listings, which is why the connector (S2a) runs one regularly.
"""
