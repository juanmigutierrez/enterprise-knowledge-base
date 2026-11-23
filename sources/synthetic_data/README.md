# Synthetic data

Everything here is invented for the POC: people, policies, numbers and amounts.
None of it describes a real company, person or contract. The four mocks read
these files and describe **one** company, so the files must agree with each
other (`tests/sources/test_cross_source.py` checks it).

| File | Read by | What it is |
|---|---|---|
| `directory.yaml` | identity provider (S1c), docs mock (S1) | 12 users and 6 groups. Page permissions may only name these |
| `hr_docs/*.md` | docs mock (S1) | 12 HR pages: Markdown with YAML front matter (`id`, `title`, `language`, `department`, `acl`) |
| `hr_api.yaml` | HR record API (S1b) | 12 employees and 5 teams; directory fields only, no pay data |
| `hr_db.yaml` | HR database (S1d) | `office_headcount` (4 offices, one closed) and `leave_balances` (12 rows for 2026) |

## How the sources link the same person

| System | Calls Alice | Link |
|---|---|---|
| Identity provider | `alice`, full name *Alice Rossi* | — |
| HR record API | `E-1001`, name *Alice Rossi* | 1-1 by full name with the identity provider (names are unique in both) |
| HR database | `employee_id = E-1001` | the HR API's ID |
| Docs | through her groups (`all-staff`, `it-employees`) | the identity provider's groups |

## The documents

| ID | Language | Who may read it | Role in the tests |
|---|---|---|---|
| HR-PL-01 | en | all-staff | Baseline lookup |
| HR-PL-02 | it | it-employees, hr-team | Italian, local audience |
| HR-PL-03 | it | it-employees, hr-team | Cross-lingual (English question, Italian answer) |
| HR-PL-04 | en | all-staff | The page the S1 demo edits (freshness) |
| HR-PL-05 | es | es-employees, hr-team | Spanish; the page the S1 demo changes permissions on |
| HR-PL-06 | en | all-staff | Replaces HR-PL-99 |
| HR-PL-07 | es | es-employees, hr-team | Multi-document comparison with HR-PL-03 |
| HR-PL-08 | it | it-employees, hr-team | Exact terms (amounts, codes) |
| HR-PL-09 | en | all-staff | Procedure / checklist structure |
| HR-PL-10 | en | all-staff | Long, general policy |
| **HR-CMP-01** | en | **hr-compensation only** | **Restricted**: must never reach anyone else (gate) |
| **HR-PL-99** | en | all-staff | **To be deleted** in a later step: must never be answered from afterwards (gate) |

## The people

| User | HR ID | Office | Team | Groups besides all-staff | Can read HR-CMP-01? |
|---|---|---|---|---|---|
| alice | E-1001 | Milan | Engineering | it-employees | no |
| bob | E-1002 | Milan | People (lead) | it-employees, hr-team, **hr-compensation** | **yes, the only one** |
| dana | E-1003 | Milan | People | it-employees, hr-team | no: HR, but not compensation |
| marco | E-1004 | Milan | Engineering (lead) | it-employees | no |
| giulia | E-1005 | Milan | Sales (lead) | it-employees | no |
| luca | E-1006 | Milan | Finance (lead) | it-employees | no |
| carla | E-1007 | Madrid | Sales | es-employees | no |
| javier | E-1008 | Madrid | Engineering | es-employees | no |
| lucia | E-1009 | Madrid | People | es-employees, hr-team | no |
| emma | E-1010 | Toronto | Product (lead) | ca-employees | no |
| noah | E-1011 | Toronto | Engineering | ca-employees | no |
| olivia | E-1012 | Toronto | Finance | ca-employees | no (she is the leaver in the S1b and S1c demos) |
