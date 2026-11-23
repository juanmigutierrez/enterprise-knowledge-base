# S01c · Sources: the mock identity provider

> Architecture: `docs/architecture.md` (box 1, identity provider; Keycloak locally in the full design). Plan: `docs/plan.md` §4.2, S1c. Option card: none. Related decisions: D3 (identity sync, one canonical person), D8 (authorization: copied permissions are always slightly stale).

## 1. In one paragraph
The identity provider (Okta, Entra ID) holds no documents, but it decides who people are and which groups they are in, and page permissions are mostly written as groups. So **a group change is a permission change** for every page that group can read, without any page changing. S1c builds a stand-in that serves the 12 test users and 6 groups in a simplified SCIM shape and records joiners, leavers and membership changes in an event feed. Without this source, revoking someone's access by removing them from a group would never reach the knowledge base.

## 2. Where it sits in the flow
```
[ S1c · mock identity provider ]  →  S2a · identity sync (T1)  →  change log  →  T8 permission resolution (S11)
output: GET /scim/v2/Users, /scim/v2/Groups   (the current state)
        GET /events?since=<seq>               (membership and lifecycle changes)
```

## 3. What was built
| File | What it contains |
|---|---|
| `sources/mock_idp/models.py` | `ScimUser`, `ScimGroup`, SCIM list response, `IdentityEvent` |
| `sources/mock_idp/store.py` | Users, memberships, the event feed and its rules |
| `sources/mock_idp/app.py` | The HTTP API: SCIM paths plus Okta-style deactivation |
| `sources/mock_idp/__main__.py` | `python -m sources.mock_idp --port 8083` |
| `sources/synthetic_data/directory.yaml` | 12 users, 6 groups; **shared** with the docs mock |
| `sources/common/directory.py` | Loads and checks the directory (moved here from the docs mock so both can use it) |
| `src/ekb/app/demos/s1c.py` | `ekb demo s1c` |
| `tests/sources/mock_idp/` | Store and API tests |

## 4. How it works

**One directory, two mocks.** As decided for this step, the identity provider and the docs mock both load `directory.yaml`. The identity provider serves it; the docs mock uses it to refuse permissions that name unknown users or groups. The full name in each entry is the 1-1 link to the HR record API (S1b).

**SCIM, simplified.** SCIM is the standard identity providers use to publish users and groups. The mock keeps its vocabulary and paths (`/scim/v2/Users`, `userName`, `displayName`, `active`, `totalResults`, `Resources`) but not the full protocol (no filters, no PATCH operations).

**The feed starts empty.** Seed users are the state at start-up, not events. An identity sync saves the feed cursor before listing users and groups, then replays events from that cursor so it catches changes made during the listing. (The docs mock is different: its feed starts with one `page.created` per seed page.)

**Events for real changes only.** Removing someone who isn't a member, or adding someone who already is, is accepted and does nothing, as SCIM servers do:
```python
if user_id in group.members:
    group.members.remove(user_id)
    self._record(IdentityEventKind.MEMBER_REMOVED, user_id, group_id)
```

**Leavers are deactivated, not deleted** (decided for this step). The user stays listed with `active: false` **and keeps their memberships**. So permission resolution (S11) must treat an inactive user as having no access at all, whatever their groups say. That rule is a gate later on.

**Joiners** produce `user.created` and one `group.member_added` per group, so a reader that follows only membership events still sees everything.

## 5. Options
None: the mock is not a pipeline step. Later: the identity sync connector (S2a), permission resolution with group membership (S11), the "revoke by removing from a group" scenario (S16), and Keycloak as an optional Docker option (S17).

## 6. What the tests prove
- **Seed:** 12 users, 6 groups, all active; the feed starts empty; users and groups agree; only bob is in `hr-compensation`; `hr-team` is bob, dana and lucia.
- **Membership:** a removal is exactly one event and changes the user's groups; an addition likewise; no-op changes emit nothing.
- **Leavers:** deactivated users stay listed with their groups; deactivating twice is refused and records nothing.
- **Joiners:** `user.created` then one event per group, in order; a duplicate ID, a duplicate name (1-1 link) or an unknown group is refused as a whole, with nothing recorded.
- **Not found:** unknown users and groups give 404 in every operation.
- **Feed:** ordered, timestamps increasing, resumable, empty at the head.
- **API:** SCIM list shape and field names; 404 / 409 / 422 mapping; `/reset` restores the seed and empties the feed.

## 7. Run it yourself
```powershell
uv run ekb demo s1c
```
You should see the 6 groups and their members, then alice removed from `it-employees` (one event; her groups shrink; no document touched), the same removal again (no event), Olivia deactivated (still listed, still in her groups), and the two events.

By hand:
```powershell
uv run python -m sources.mock_idp         # then open http://127.0.0.1:8083/docs
uv run ekb demo s1c --url http://127.0.0.1:8083
```

## 8. Decisions and limits
- **One identity provider.** After the ShopFully / Flipp merger there may be two (D3); merging them into one canonical person is a production concern, not modelled here.
- **Runtime changes don't reach the docs mock.** A user created here at runtime is unknown to the docs mock, which loaded the directory at start-up. The real systems share the identity provider live.
- **No nested groups, no roles, no sign-in.** Groups are flat lists of users.
- **In memory, no authentication**, 127.0.0.1 only, synthetic people.

## 9. Check your understanding
1. Alice is removed from `it-employees`. No page changed. Why must the knowledge base still react, and which later step does it?
2. Olivia is deactivated but still a member of `all-staff`. What must permission resolution do with her, and why does the mock keep her memberships?
3. Why does the identity event feed start empty, while the docs feed starts with 12 `page.created` entries?

<details>
<summary>Answers</summary>

1. Because pages are shared with groups: every page readable only through `it-employees` is now off-limits to her. T8 / permission resolution (S11) combines page permissions with current group membership, fed by the identity sync (S2a).
2. Treat her as having no access at all, ignoring her groups. Real identity providers keep a deactivated user's memberships (so reactivation restores them), and the mock does the same so that this rule must be implemented and tested.
3. The sync saves a cursor, reads the current directory and replays events from the saved cursor. The docs mock records seed pages as created so its demo can show a complete feed; both lead to the same state.
</details>
