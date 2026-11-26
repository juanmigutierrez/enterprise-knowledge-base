export const boxes = {
  "docs": [
    74,
    328,
    446,
    190
  ],
  "hr": [
    74,
    653,
    446,
    191
  ],
  "identity": [
    74,
    1304,
    446,
    190
  ],
  "docsConnector": [
    616,
    328,
    411,
    190
  ],
  "hrConnector": [
    616,
    653,
    411,
    191
  ],
  "identityConnector": [
    616,
    1304,
    411,
    190
  ],
  "normalize": [
    1091,
    328,
    367,
    1166
  ],
  "registry": [
    1566,
    314,
    468,
    191
  ],
  "bus": [
    1566,
    620,
    468,
    191
  ],
  "originals": [
    1566,
    976,
    468,
    191
  ],
  "diff": [
    2204,
    309,
    750,
    143
  ],
  "parse": [
    2204,
    494,
    750,
    143
  ],
  "chunk": [
    2204,
    679,
    750,
    143
  ],
  "classify": [
    2204,
    864,
    750,
    144
  ],
  "entities": [
    2204,
    1050,
    750,
    143
  ],
  "embed": [
    2204,
    1235,
    750,
    142
  ],
  "publish": [
    2204,
    1420,
    750,
    143
  ],
  "people": [
    3156,
    555,
    735,
    176
  ],
  "search": [
    3156,
    993,
    735,
    176
  ],
  "permissions": [
    3156,
    1414,
    735,
    176
  ],
  "recheck": [
    3572,
    2110,
    384,
    141
  ]
};
export const cases = [
  {
    "title": "Add a page",
    "question": "When can Alice first cite a new policy?",
    "expected": "Compare Alice’s mentoring answer before and after publication. Once the worker publishes the page with its readers, her answer cites HR-PL-11. The page links to Lucía and the People team.",
    "explanation": "The docs connector records the original and emits a change. The worker turns the page into passages. T8 publishes them with their readers so Alice can cite the policy.",
    "nodes": [
      "docs",
      "docsConnector",
      "normalize",
      "registry",
      "bus",
      "originals",
      "diff",
      "parse",
      "chunk",
      "classify",
      "entities",
      "embed",
      "publish",
      "search",
      "permissions"
    ],
    "shot": "add-page.png"
  },
  {
    "title": "Edit the page",
    "question": "Does an answer change when its source is edited?",
    "expected": "Alice keeps access to HR-PL-11. After the worker runs, the policy says three meetings a month instead of two. The citation can stay the same. Look for the worker processing a new version of the same page.",
    "explanation": "The webhook delivers a new version. T2 detects the content change, the worker updates the passages, and the source recheck catches the version mismatch on an earlier answer.",
    "nodes": [
      "docs",
      "docsConnector",
      "normalize",
      "registry",
      "bus",
      "originals",
      "diff",
      "parse",
      "chunk",
      "classify",
      "entities",
      "embed",
      "publish",
      "search",
      "permissions",
      "recheck"
    ],
    "shot": "edit-page.png"
  },
  {
    "title": "Revoke on the page",
    "question": "What happens if the page owner withdraws Alice’s access?",
    "expected": "In Right after, Alice has already lost HR-PL-11, even though the worker has not updated the index. Lucía still has access through the HR team. The worker updates the readers without rebuilding the vectors.",
    "explanation": "Q4 checks the source and immediately withholds the page from Alice. On the write path, T2 routes the reader change straight to T8 to update access without re-embedding the text.",
    "nodes": [
      "docs",
      "docsConnector",
      "normalize",
      "registry",
      "bus",
      "diff",
      "publish",
      "permissions",
      "search",
      "recheck"
    ],
    "shot": "revoke-page.png"
  },
  {
    "title": "Revoke by group",
    "question": "Can someone lose access even when the page itself stays the same?",
    "expected": "Compare Lucía and Bob. Lucía loses the mentoring citation as soon as she leaves the HR group. Bob keeps it. The page’s chunks need no edits.",
    "explanation": "Q4 checks Lucía’s current membership on her next request. The identity connector then updates the stored group membership. The indexed policy text stays the same.",
    "nodes": [
      "identity",
      "identityConnector",
      "normalize",
      "bus",
      "permissions",
      "recheck"
    ],
    "shot": "revoke-group.png"
  },
  {
    "title": "A hire, in HR",
    "question": "Is a new HR record enough to link a name to a person?",
    "expected": "Check the links on HR-PL-11 under Now. Sara is mentioned on the page, but her HR record has no matching login yet. The links stay the same.",
    "explanation": "The HR connector sees Sara’s employee record. Entity resolution needs a matching identity account before the worker can link her to the mentoring policy.",
    "nodes": [
      "hr",
      "hrConnector",
      "normalize",
      "bus",
      "diff",
      "entities"
    ],
    "shot": "hire-record.png"
  },
  {
    "title": "A hire, her account",
    "question": "Will an existing page recognize Sara once her account exists?",
    "expected": "After the identity sync and full HR listing, person:sara appears in the links on HR-PL-11. Look for a re-link in the worker history. Nobody edited the mentoring policy.",
    "explanation": "The identity sync and HR full listing let T6 resolve Sara. The worker records her canonical ID and re-links the existing mentoring passages to her.",
    "nodes": [
      "identity",
      "identityConnector",
      "hr",
      "hrConnector",
      "normalize",
      "bus",
      "entities",
      "people",
      "search"
    ],
    "shot": "hire-account.png"
  },
  {
    "title": "A leaver",
    "question": "How does the system discover that someone has left?",
    "expected": "The next full HR listing detects Lucía’s missing record. Watch person:lucia disappear from the mentoring page’s links while the page itself remains available to its readers.",
    "explanation": "A full HR listing discovers Lucía’s missing record. The worker removes her entity and refreshes the links in passages that mention her.",
    "nodes": [
      "hr",
      "hrConnector",
      "normalize",
      "bus",
      "diff",
      "entities",
      "people",
      "search"
    ],
    "shot": "leaver.png"
  },
  {
    "title": "Delete a page",
    "question": "Can an old answer still cite a policy after its owner deletes it?",
    "expected": "In Alice’s hotel answer, HR-PL-99 disappears in Right after. Once the worker purges the old policy and its cached answers, the current travel policy HR-PL-06 can still be cited.",
    "explanation": "Q4 immediately withholds the deleted travel page. T2 routes the delete to purge its originals and searchable passages. The change also invalidates cached answers that used it.",
    "nodes": [
      "docs",
      "docsConnector",
      "normalize",
      "registry",
      "bus",
      "originals",
      "diff",
      "permissions",
      "search",
      "recheck"
    ],
    "shot": "delete-page.png"
  }
];
