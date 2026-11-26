# S19 · Running the system from a clean checkout

The handoff starts with the [README](../../README.md). Its acceptance check follows a source change all the way to an answer an HR assistant can cite. The [AI development record](../ai-sdlc.md) explains who built and reviewed each step and which conclusions the measurements support.

## Repeat the check

From a fresh clone, run these commands in the repository root.

```sh
uv sync --locked
uv run ekb demo lifecycle
```

The first command creates the project environment from the lockfile. The second starts synthetic sources, ingests their records and asks the assistant questions as different readers. The CLI owns the mock processes for this run. Its offline choices keep the acceptance check independent of model credentials and Docker. The [lifecycle walkthrough](S16-lifecycle.md) describes all eight source changes and the optional model-backed path.

## Read the result

1. **Publication.** A new mentoring policy acquires its readers at T8 before the assistant can cite it. The publication tests also inspect the interval between embedding and publication.
2. **Revocation.** Watch Alice's next answer after the source withdraws her access. The source recheck withholds the mentoring policy while the worker still has the previous access rules. The revocation tests cover a previously cached answer.
3. **Deletion.** The source recheck withholds the deleted travel policy immediately. The worker then purges indexed passages and invalidates the affected cache entries. The demo counts any subsequent citation of that page as a failure.

The CLI returns a failing exit code if it observes a leak, a post-deletion citation or a broken promise. That makes the same command usable by a reader at a terminal and by a scripted setup check. [CLI implementation](../../src/ekb/app/demos/lifecycle.py) · [Gate tests](../../tests/gates/)

## Verification record

On 26 November 2026, the setup check used a fresh Windows clone of local commit `d2c535c`, the code revision underlying this documentation. The clone had its own virtual environment and no workspace `.env` file. uv selected CPython 3.13.5 and installed 138 packages with `uv sync --locked` using the host's package cache. This exercises the tracked source tree and a fresh install on a machine with Git and uv already available.

The demo exited successfully with 16 of 16 promises held. The restricted salary page appeared only for Bob, the deleted travel page appeared zero times after deletion, and both revocations took effect on the next request before the worker ran. The final worker lag and dead-letter count were both zero.

| Check in the same clone | Observed result |
|---|---|
| Ruff format and lint | 560 files already formatted. Lint passed. |
| Strict mypy | 487 source files checked successfully. |
| Import Linter | All 11 layer and dependency contracts held. |
| Default pytest suite with coverage | 1,542 passed, 7 skipped, 71 deselected, 7 warnings. Coverage was 96.05%. |

The seven skips are restart-persistence contracts for in-memory stores and the in-memory bus. The default test selection excludes Docker integration, end-to-end and evaluation markers. The S17 and S18 walkthroughs record the separate infrastructure and evaluation runs.

## Check your understanding

1. What can a clean clone reveal that rerunning the demo in the developer checkout might miss?
2. Why inspect Alice's first request after revocation, before the worker runs?
3. Which experiment measures whether multilingual questions retrieve the right evidence?

<details>
<summary>Answers</summary>

1. It exposes dependencies on untracked files, developer secrets or packages left over in an existing environment.
2. That request exercises the interval when the source has withdrawn access and the index still grants it. Reusing a warm answer must respect the withdrawal too.
3. S18's retrieval comparison (card 4.5) measures Recall@5 against labeled evidence, including cross-language questions. The lifecycle demo measures how answers change as the sources change. [Evaluation walkthrough](S18-evaluation.md)

</details>
