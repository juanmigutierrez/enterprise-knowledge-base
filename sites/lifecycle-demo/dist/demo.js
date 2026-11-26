import { boxes, cases } from "./cases.js";

const el = id => document.getElementById(id);
const storageKey = "ekb.lifecycle.case.v1";
let current = 0;
try {
    const saved = Number(localStorage.getItem(storageKey));
    if (Number.isInteger(saved) && saved >= 0 && saved < cases.length) current = saved;
} catch { /* The tour also works with browser storage disabled. */ }
let architecture = false;

const links = cases.map((item, index) => {
    const button = document.createElement("button");
    button.textContent = (index + 1) + ". " + item.title;
    button.onclick = () => select(index);
    el("cases").append(button);
    return button;
});

function select(index) {
    current = index;
    try { localStorage.setItem(storageKey, String(index)); } catch { /* Session-only progress. */ }
    render();
}
function zoom(expanded) {
    el("figure").classList.toggle("zoomed", expanded);
    el("zoom").textContent = expanded ? "Fit image" : "Zoom in";
    el("zoom").setAttribute("aria-pressed", String(expanded));
}
function render() {
    const item = cases[current];
    links.forEach((button, index) => {
        if (index === current) button.setAttribute("aria-current", "step");
        else button.removeAttribute("aria-current");
    });
    el("progress").textContent = "Case " + (current + 1) + " of " + cases.length;
    el("title").textContent = item.title;
    el("question").textContent = item.question;
    el("description").textContent = architecture ? item.explanation : item.expected;
    el("image").src = architecture ? "./local-compose.png" : "./" + item.shot;
    el("image").alt = architecture ? "Local architecture with the components for " + item.title + " highlighted" : "Recorded inspector comparison for " + item.title;
    el("overlay").replaceChildren();
    if (architecture) {
        for (const node of item.nodes) {
            const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
            ["x","y","width","height"].forEach((key,i) => rect.setAttribute(key, boxes[node][i]));
            el("overlay").append(rect);
        }
    }
    el("result").setAttribute("aria-pressed", String(!architecture));
    el("architecture").setAttribute("aria-pressed", String(architecture));
    el("previous").disabled = current === 0;
    el("next").textContent = current === cases.length - 1 ? "Restart walkthrough" : "Next case";
    zoom(false);
}
el("result").onclick = () => { architecture = false; render(); };
el("architecture").onclick = () => { architecture = true; render(); };
el("zoom").onclick = () => zoom(!el("figure").classList.contains("zoomed"));
el("previous").onclick = () => select(current - 1);
el("next").onclick = () => select((current + 1) % cases.length);
render();
