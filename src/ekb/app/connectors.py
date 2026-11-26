"""Box 2 wired to box 3 for the inspector and ``ekb demo ingest``: all six connectors.

The connectors use the injection points introduced in S2 (``sink``,
``store_original``, ``known``), here pointed at one change log. They are still
chosen by hand (choosing them per source in config comes later). Since S17 they
also run as their own service, ``ekb connect`` (:mod:`ekb.app.services`), once
the change log is on Kafka, DynamoDB and S3.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from ekb.app.changelog import ChangeLog
from ekb.connect.docs_api import DocsApi
from ekb.connect.hr_api import HrApi
from ekb.connect.hr_db import CdcReader
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.options.hr_api_delta import HrApiDeltaConnector
from ekb.connect.options.hr_api_full_listing import HrApiFullListingConnector
from ekb.connect.options.hr_db_cdc import HrDbCdcConnector
from ekb.connect.options.identity_sync import IdentitySyncConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.port import Connector


@dataclass
class Connectors:
    """The six connectors, split by how often they run."""

    webhook: DocsWebhookConnector
    frequent: list[Connector]  # every round: webhook queue, HR delta, CDC, identity
    listings: list[Connector]  # every few rounds: the full listings that catch deletes
    people: PersonMap


def build_connectors(
    docs: httpx.Client, hr: httpx.Client, idp: httpx.Client, db_path: Path, changelog: ChangeLog
) -> Connectors:
    originals = changelog.store_original
    people = PersonMap(load_people(IdentityApi(idp), HrApi(hr)))
    sink = changelog.publish
    webhook = DocsWebhookConnector(
        DocsApi(docs), sink, originals, people, known=changelog.accepted("hr_docs")
    )
    return Connectors(
        webhook=webhook,
        frequent=[
            webhook,
            HrApiDeltaConnector(HrApi(hr), sink, originals, people),
            HrDbCdcConnector(CdcReader(db_path), sink, originals, people),
            IdentitySyncConnector(IdentityApi(idp), sink, originals, people),
        ],
        listings=[
            DocsFullListingConnector(
                DocsApi(docs),
                sink,
                originals,
                people,
                known=changelog.accepted("hr_docs"),
            ),
            HrApiFullListingConnector(
                HrApi(hr), sink, originals, people, known=changelog.accepted("hr_api")
            ),
        ],
        people=people,
    )
