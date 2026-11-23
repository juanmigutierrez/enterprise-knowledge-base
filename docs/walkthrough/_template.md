# S<nn> · <Layer / step name>

> Architecture: `docs/architecture.md` (box <n>). Plan: `docs/plan.md` §4.2. Option card: `docs/option-evaluation.md` <x.y> (if any).

## 1. In one paragraph
What this step does and why the system needs it.

## 2. Where it sits in the flow
```
<previous step>  →  [ this step ]  →  <next step>
input: <shape>        output: <shape>
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/...` | |
| `tests/...` | |

## 4. How it works
Step-by-step explanation of the main code path, with short code excerpts where they help.

## 5. Options
Which option was built now, which come later, and how they are switched (`config/*.yaml`).

## 6. What the tests prove
One line per important test, especially failure cases and gates.

## 7. Run it yourself
```powershell
uv run ekb demo <step>
```
What you should see, and what to change to see something different.

## 8. Decisions and limits
What was simplified for the POC, and what production does differently.

## 9. Check your understanding
1.
2.
3.

(Answers at the bottom of the file, collapsed.)
