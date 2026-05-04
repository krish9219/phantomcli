"""
PhantomCLI Agent Orchestrator
Breaks complex tasks into parallel sub-agents with a shared project folder.
Each agent owns specific files (no conflicts). Dependency waves handle ordering.
Max agents auto-determined from system hardware at setup.
"""
import os
import re
import json
import time
import uuid
import shutil
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, TYPE_CHECKING
from openai import OpenAI

from omnicli.memory import get_config, save_config
from omnicli.auth import get_api_key

if TYPE_CHECKING:
    from omnicli.research_phase import ResearchResult

log = logging.getLogger("omnicli.agents")


def _auto_cleanup_old_projects(base_dir: str, ttl_days: int = 14) -> None:
    """
    Best-effort TTL sweep over ~/phantom_projects/. Called before each new
    orchestration — keeps the dir from growing without bound.
    Runs synchronously but quickly (stat-only scan).
    """
    try:
        base = os.path.expanduser(base_dir)
        if not os.path.isdir(base):
            return
        cutoff = time.time() - (ttl_days * 86400)
        for name in os.listdir(base):
            entry = os.path.join(base, name)
            try:
                if os.path.getmtime(entry) >= cutoff:
                    continue
                if os.path.isdir(entry):
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    os.remove(entry)
                log.info("auto-cleaned stale project dir: %s", entry)
            except OSError as e:
                log.debug("auto-clean skipped %s: %s", entry, e)
    except Exception as e:
        log.debug("auto-clean pass failed: %s", e)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AgentTask:
    agent_id:       str
    name:           str
    role:           str
    task:           str
    assigned_files: list[str]
    depends_on:     list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    agent_id:      str
    status:        str = "queued"   # queued | running | done | failed
    output:        str = ""
    files_written: list[str] = field(default_factory=list)
    error:         str = ""
    start_time:    float = 0.0
    end_time:      float = 0.0

    @property
    def elapsed(self) -> float:
        if self.end_time and self.start_time:
            return round(self.end_time - self.start_time, 1)
        return 0.0


# ── Orchestrator ──────────────────────────────────────────────────────────────

class AgentOrchestrator:
    """
    1. Plans the task breakdown via AI (plan.json)
    2. Executes agents in dependency waves using threads
    3. Agents share a project folder; each owns specific files
    4. Returns a combined markdown output when all done
    """

    def __init__(
        self,
        prompt:          str,
        trust_level:     int = 3,
        project_dir:     Optional[str] = None,
        on_status:       Optional[Callable] = None,   # (agent_id, status, msg) → None
    ):
        self.prompt      = prompt
        self.trust_level = trust_level
        self.session_id  = uuid.uuid4().hex[:8]
        # Sweep old project dirs before starting a new run. TTL controlled by
        # config `phantom_projects_ttl_days` (default 14). Set to 0 to disable.
        try:
            ttl = int(get_config("phantom_projects_ttl_days", "14") or 14)
        except (TypeError, ValueError):
            ttl = 14
        # Use the persistent work_dir set during onboarding (or via /workdir).
        # On Windows this lands as 'C:\Users\X\PhantomProjects\project_xxx',
        # not the legacy '~/phantom_projects/...' which mixed slashes.
        work_dir = (get_config("work_dir", "") or "").strip() \
                   or os.path.expanduser(os.path.join("~", "PhantomProjects"))
        if ttl > 0:
            _auto_cleanup_old_projects(work_dir, ttl_days=ttl)
        self.project_dir = project_dir or os.path.join(
            work_dir, f"project_{self.session_id}"
        )
        # Normalise slashes so paths don't end up mixed (e.g. C:\X/y/z)
        self.project_dir = os.path.normpath(self.project_dir)
        self.plan_file   = os.path.join(self.project_dir, "phantom_plan.json")
        self.on_status   = on_status

        # ── Pick a free port for this build — no more hard-coding 8000 ──
        # Agents get the chosen port injected into their prompt so `app.py`
        # binds to THIS port, avoiding the "port 8000 already in use" trap
        # that left the user chasing stale Flask processes.
        try:
            from omnicli.free_port import pick_free_port
            self.port = pick_free_port()
        except Exception:
            self.port = 8000
        # Recompute max_agents from a live system probe rather than the stale
        # value cached on first boot (a Windows user with 64 GB RAM was being
        # capped at 2 agents because the old cache had ram=4.0).
        try:
            from omnicli.sysinfo import detect_system as _ds
            self.max_agents = min(int(_ds().get("max_agents", 3)), 4)
            save_config("max_agents", str(self.max_agents))
        except Exception:
            self.max_agents = min(int(get_config("max_agents", "3") or 3), 4)

        # Research-phase result — populated by AgentOrchestrator.research()
        # before plan()/execute(). If the orchestrator ran a research pass,
        # every agent gets a prompt pointing at `research.json` with REAL
        # current data. Otherwise agents fall back to LLM-hallucinated
        # seed data (and the demo banner in v3.0.5+ warns the user).
        self.research_result: Optional["ResearchResult"] = None

        self.tasks:   list[AgentTask]         = []
        self.results: dict[str, AgentResult] = {}
        self._lock    = threading.Lock()
        # Shared data contract emitted by the planner so frontend/backend agents
        # use identical field names. Without this, the UI invents fields the
        # backend doesn't emit and the page renders 'undefined'.
        self.shared_schema: dict = {}

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def should_spawn(prompt: str) -> bool:
        """Heuristic: does this task need multiple agents (multi-file project)?

        Claude-Code-style negation/info-query guards run FIRST — if the user
        clearly wants information (not a build) or explicitly rejects a
        project (not every prompt is a project), we short-circuit and
        return False. Only THEN do the positive build heuristics run.

        New in 4.0.9: an explicit role assignment ("You are a senior …")
        is honoured verbatim and never overlaid with a persona, regardless
        of keywords in the body. The user gave the model a system role for
        a reason; respect it.
        """
        # ── Explicit-role override ──────────────────────────────────────
        # Honour deliberate "You are a…" prompts. Body keywords (flask,
        # dashboard, build) inside the instructions of a role-prompt should
        # not override the role itself.
        head = (prompt or "").lstrip()[:300].lower()
        ROLE_STARTS = (
            "you are a", "you are an", "you're a", "you're an",
            "act as a", "act as an", "you act as",
            "your job is", "your role is", "your task is",
            "as a senior", "as an expert", "as a professional",
            "imagine you are", "imagine you're", "pretend you are",
        )
        if any(head.startswith(r) for r in ROLE_STARTS):
            return False

        p = (prompt or "").lower()

        # ── Negation — explicit opt-out phrases ─────────────────────────
        # "forget about the web app" / "don't make a project" / "just tell me"
        # must NOT trigger a build even though they contain build keywords.
        NEGATIONS = (
            "forget about", "forget the", "don't make", "do not make",
            "don't build", "do not build", "don't create", "do not create",
            "no project", "not a project", "no new project",
            "not an app", "no app", "no website",
            "just search", "just tell me", "just get me", "just find",
            "just show me", "just explain", "just answer",
            "only search", "only tell me", "only find",
            "without building", "without creating", "no need to build",
            "no need to create", "skip the project", "skip building",
        )
        if any(neg in p for neg in NEGATIONS):
            return False

        # ── Info-query verbs — classic "tell me / show me" requests ─────
        # These are info requests, not builds. Handle with single-agent +
        # web tools.
        INFO_QUERY_STARTS = (
            "what is", "what are", "what's", "whats",
            "who is", "who are", "who's",
            "when is", "when are", "when's", "when was",
            "where is", "where are", "where's",
            "why is", "why are", "why do", "why does", "why did",
            "how is", "how are", "how do i", "how do you",
            "how does", "how did",
            "tell me", "show me", "explain",
            "summarise", "summarize", "summary of",
            "compare", "what's the difference",
            "latest", "newest", "recent", "current",
            "find me", "find the", "find a", "find some",
            "search for", "search the",
            "get me the latest", "get me latest",
            "give me an overview", "give me a summary",
            "analyze", "analyse", "analysis of",
            "status of", "price of", "score",
        )
        if any(p.startswith(v) or (" " + v + " ") in (" " + p + " ")
               for v in INFO_QUERY_STARTS):
            # Still allow if there's an explicit BUILD verb elsewhere in the
            # message ("compare X and Y then build an app for it")
            BUILD_VERBS = ("build", "make", "create a new", "scaffold",
                            "generate a", "develop a", "implement a")
            if not any(b in p for b in BUILD_VERBS):
                return False

        # Strong single-word signals that almost always mean a multi-file project
        STRONG = (
            "flask", "django", "fastapi", "express", "react", "vue", "angular",
            "sqlite", "postgres", "mongodb", "mysql", "redis",
            "dashboard", "web app", "webapp", "full stack", "fullstack",
            "ml model", "neural network", "machine learning", "deep learning",
            "pipeline", "microservice", "rest api", "graphql",
            "docker", "kubernetes", "dockerfile",
            "android app", "ios app", "mobile app",
            "backend", "frontend", "full-stack",
        )
        # Weaker keywords that need context (long prompt OR multi-file mention)
        WEAK = (
            "create", "build", "develop", "make",
            "application", "project", "system", "api",
            "website", "service", "platform", "tool",
        )

        words = p.split()
        is_long    = len(words) > 10
        multi_file = any(w in p for w in ("files", "folder", "directory", "multiple", "several"))

        if any(kw in p for kw in STRONG):
            return True
        if any(kw in p for kw in WEAK) and (is_long or multi_file):
            return True
        return False

    def research(self, on_status: Optional[Callable[[str], None]] = None) -> Optional["ResearchResult"]:
        """Run the research phase — scrape live web data relevant to the
        directive's domain and persist it to `research.json` in the
        project dir. Agents will then seed their app with REAL data
        instead of LLM-hallucinated entries.

        Returns the ResearchResult (may have ok=False if scraping
        failed). Silently returns None if the directive has no detectable
        research domain (e.g. 'build me a todo list app')."""
        try:
            from omnicli.research_phase import detect_domain, run_research
        except Exception as e:
            log.debug("research_phase unavailable: %s", e)
            return None
        if not detect_domain(self.prompt):
            return None
        os.makedirs(self.project_dir, exist_ok=True)
        self._emit("research", "running", "🌐 Research phase — scraping live data…")
        try:
            result = run_research(
                directive=self.prompt,
                project_dir=self.project_dir,
                on_status=on_status or (lambda m: self._emit("research", "running", m)),
            )
            self.research_result = result
            self._emit("research", "done",
                       f"✓ Research: {len(result.sources)} sources · "
                       f"{'data captured' if result.ok else 'no usable data'}")
            return result
        except Exception as e:
            log.warning("research phase failed: %s", e)
            self._emit("research", "failed", f"research error: {e}")
            return None

    def improve_prompt(self) -> str:
        """Rewrite the raw user prompt into a clearer, fuller spec before
        planning. The router heuristic accepts very short asks, but agents
        execute much better when the goal is specific.

        Returns the improved prompt and updates self.prompt in place.
        On any failure, the original prompt is returned unchanged.
        """
        try:
            client = OpenAI(api_key=get_api_key(), base_url=get_config("main_url"))
            sys_os = get_config("sys_os", "Linux")
            from omnicli.memory import get_config as _gc
            work_dir = (_gc("work_dir", "") or "").strip() or os.path.expanduser("~/PhantomProjects")
            rewriter_prompt = (
                "You are a senior product manager AND tech lead. Rewrite the user's "
                "brief request into a clear, implementation-ready spec for a small "
                "dev team. The bar is 'production-visible on first run, even with no "
                "keys, no network, no configuration' — a hollow shell with three stub "
                "rows is a FAILURE, not a fallback. Before writing the spec, silently "
                "answer these seven feasibility questions and bake the answers INTO the spec:\n"
                "  1. DATA SOURCE (prefer KEYLESS): Rank sources in this order and pick the\n"
                "     FIRST that works — do not default to API-key services:\n"
                "       (a) KEYLESS public JSON endpoints / scrapeable HTML pages\n"
                "           (e.g. ESPNCricinfo match-center JSON, Cricbuzz /cbzios/cricket/,\n"
                "           Wikipedia REST, open.er-api.com, coingecko, yahoo finance HTML,\n"
                "           public GitHub REST, DuckDuckGo HTML). Use `requests` + a real\n"
                "           User-Agent. Wrap in try/except.\n"
                "       (b) Free-tier API that needs a key — only if (a) is not realistic.\n"
                "           Read the key from env; if missing, DO NOT fail — fall through\n"
                "           to (c).\n"
                "       (c) RICH seeded data file — MINIMUM 10 realistic rows per section\n"
                "           (not 2, not 'Sample 1', not Lorem Ipsum). Use plausible real\n"
                "           names, realistic numbers, varied values. The app must look\n"
                "           convincing on first run with zero network.\n"
                f"  2. RUNTIME LOCATION: DEFAULT to localhost (e.g. http://localhost:{self.port}).\n"
                "     The runner prints the localhost URL — that IS 'sharing the link'.\n"
                "     ONLY add tunneling (ngrok / cloudflared) if the user explicitly says\n"
                "     'public', 'over the internet', 'with my team', 'deploy', or\n"
                "     'accessible from anywhere'.\n"
                "  3. OS-CORRECT RUNNER: User is on '" + sys_os + "'. Emit run.bat on\n"
                "     Windows, run.sh on Linux/macOS. Absolute paths only.\n"
                "     DEFAULT Python web stack = Flask + bare `python app.py`. No uvicorn,\n"
                "     no ASGI, no async unless WebSockets are genuinely required.\n"
                "  4. LAYER DECOMPOSITION: data-source → fetcher → cache → API → UI →\n"
                "     runner. Flat layout: every .py at project root (fetcher.py,\n"
                "     models.py, cache.py, app.py). NO subpackages, NO relative imports.\n"
                "  5. VISUAL QUALITY BAR (this is the #1 thing that makes users say\n"
                "     'it doesn't work properly'): the UI must look like a 2026\n"
                "     production product, not a 2010 Bootstrap tutorial. This is\n"
                "     non-negotiable — plain Bootstrap card grids with default\n"
                "     styling FAIL. Bake in these DESIGN REQUIREMENTS:\n"
                "       • Palette: dark gradient bg (#0a0a0f→#13131e) with cyan/violet\n"
                "         accent (#00f2fe, #7c3aed, #43e97b). Light mode is an inverted\n"
                "         palette with the same accents.\n"
                "       • Typography: Outfit 800 for hero/headings, Inter 400-600 body,\n"
                "         JetBrains Mono for labels/metadata, 1.6 line-height, -0.02em\n"
                "         letter-spacing on h1/h2.\n"
                "       • Glassmorphism cards: semi-transparent background with\n"
                "         backdrop-filter: blur(12px), 1px border with 8% opacity,\n"
                "         rounded-2xl corners, padding 24-28px.\n"
                "       • Hero header: gradient text title, live 'last refreshed'\n"
                "         timestamp, dark/light toggle with localStorage persistence.\n"
                "       • Card grid: 8+ cards per section in CSS grid (responsive\n"
                "         1/2/3/4 cols), hover = lift-4px + cyan accent glow,\n"
                "         150-200ms ease-out transitions.\n"
                "       • Section dividers with '// yesterday' style mono labels in\n"
                "         --text-muted color — that Linear/Vercel/Cursor aesthetic.\n"
                "       • Micro-interactions: hover + active + focus-ring on every\n"
                "         interactive element, smooth scroll, reveal-on-scroll\n"
                "         intersection observer for card rows.\n"
                "       • Charts inline in each card (Chart.js via CDN): use the\n"
                "         cyan/violet accent palette — NOT the default Chart.js blue.\n"
                "         Line for time series, horizontal bar for rankings, doughnut\n"
                "         for distribution.\n"
                "       • FontAwesome 6 icons in card headings + toolbar.\n"
                "       • Ambient 'orbs' — blurred colored circles in fixed position\n"
                "         with very low opacity, pointer-events: none — classic\n"
                "         landing-page depth.\n"
                "       • NO 'No data' empty states — always render seeded rows.\n"
                "       • REFERENCE AESTHETIC: Linear app, Vercel dashboard, Cursor,\n"
                "         Claude artifacts, modern Stripe. The page should look like\n"
                "         it belongs on a landing page, not inside a Flask tutorial.\n"
                "         When in doubt, add more polish, not less.\n"
                "  6. FALLBACK RICHNESS + HONESTY ABOUT SEEDED DATA:\n"
                "     If the external source fails, the app must still render\n"
                "     the FULL UI with seeded data — 8+ entries per section,\n"
                "     varied and realistic. BUT:\n"
                "     * The seed data MUST be clearly watermarked. Include a\n"
                "       visible banner at the top of the page (yellow/orange\n"
                "       background, not subtle gray) that says:\n"
                "         ⚠ Demo data — live API unavailable. Scores and\n"
                "         matches shown are illustrative only.\n"
                "     * Every card must have a small 'DEMO' tag (mono font,\n"
                "       9px) so the user can tell at a glance that no row\n"
                "       reflects real live state.\n"
                "     * NEVER claim the data is 'live' or 'real-time' when it\n"
                "       is actually from seed_data.json. The hero timestamp\n"
                "       must read 'Demo snapshot — <date>' not 'Last\n"
                "       refreshed'.\n"
                "     * The model CANNOT know real current-season scores. Do\n"
                "       not invent matches that look like they happened\n"
                "       yesterday. If you must fabricate data, use names that\n"
                "       are obviously demo (Team A / Team B / Team C) OR\n"
                "       explicitly-dated historical matches.\n"
                "  7. ACCEPTANCE CHECKS: List 4-6 explicit checks — include 'homepage\n"
                "     returns HTTP 200 with non-empty body', 'every section renders at\n"
                "     least N cards', 'no literal Lorem Ipsum, no \"Sample X\", no\n"
                "     undefined strings in the HTML', 'charts render', 'toggle works'.\n\n"
                f"OS: {sys_os}\n"
                f"WORK DIR: {work_dir}\n"
                f"USER REQUEST: {self.prompt}\n\n"
                "Return ONLY the rewritten spec — no preamble, no markdown headers. "
                "Structure it as: (a) one-line goal, (b) concrete features as bullets, "
                "(c) DATA SOURCE & FALLBACK (keyless first, rich seed last — spell out "
                "MINIMUM rows), (d) RUNTIME & PUBLIC URL plan, (e) tech stack hint, "
                "(f) UI/UX notes with specific visual details (theme, grid shape, chart "
                "types), (g) ACCEPTANCE CHECKS. Keep it under 340 words. Surface the "
                "safeguards even if the user didn't ask — users judge by what they see."
            )
            resp = client.chat.completions.create(
                model=get_config("main_model"),
                messages=[{"role": "user", "content": rewriter_prompt}],
                max_tokens=900, temperature=0.3,
            )
            improved = (resp.choices[0].message.content or "").strip()
            if improved and len(improved) > len(self.prompt) // 2:
                self.original_prompt = self.prompt
                self.improved_prompt = improved
                self.prompt = improved
                return improved
        except Exception as e:
            log.debug("prompt rewrite failed: %s", e)
        self.original_prompt = self.prompt
        self.improved_prompt = ""
        return self.prompt

    def plan(self) -> list[AgentTask]:
        """Ask AI to break down the task. Writes plan.json. Returns task list."""
        client = OpenAI(api_key=get_api_key(), base_url=get_config("main_url"))
        os.makedirs(self.project_dir, exist_ok=True)

        sys_os     = get_config("sys_os",     "Linux")
        owner_name = get_config("owner_name", "the user")

        planning_prompt = f"""
You are a senior software architect. Break this task into {self.max_agents} parallel subtasks
for separate AI developer agents, each working on DIFFERENT files.

TASK: {self.prompt}
PROJECT DIR: {self.project_dir}
OS: {sys_os}

Return ONLY a valid JSON OBJECT (no markdown, no explanation) with TWO top-level keys:

{{
  "shared_schema": {{
    "_comment": "The exact JSON shape every backend endpoint returns and every frontend template consumes. Use REAL field names with REAL example values so agents pick them up verbatim. Without this, the frontend invents field names the backend doesn't emit and the UI shows 'undefined'.",
    "example_response": {{
      "yesterday": [
        {{ "team1": "MI", "team2": "CSK", "venue": "Wankhede", "score": "MI 187/4 (20) beat CSK 165/9 (20)",
           "top_scorer": "Rohit Sharma 78(45)", "best_bowling": "Bumrah 3/28",
           "best_batting": "Rohit Sharma 78(45)", "analysis": "Tactical bowling in death overs sealed it." }}
      ],
      "today":     [ {{ "team1": "RCB", "team2": "KKR", "venue": "Chinnaswamy", "start_time_ist": "19:30",
                        "live_score": "RCB 145/3 (15)", "analysis": "..." }} ],
      "upcoming":  [ {{ "team1": "GT", "team2": "DC", "venue": "Narendra Modi Stadium", "start_time_ist": "19:30 IST tomorrow",
                        "preview": "..." }} ]
    }}
  }},
  "agents": [
    {{
      "name": "Backend Agent",
      "role": "Backend Developer",
      "task": "specific detailed description — REFERENCE shared_schema field names exactly",
      "assigned_files": ["{self.project_dir}/app.py", "{self.project_dir}/models.py"],
      "depends_on": []
    }},
    {{
      "name": "Frontend Agent",
      "role": "Frontend Developer",
      "task": "specific detailed description — RENDER shared_schema field names exactly (no invented fields)",
      "assigned_files": ["{self.project_dir}/templates/index.html", "{self.project_dir}/static/style.css"],
      "depends_on": ["Backend Agent"]
    }}
  ]
}}

Rules:
- No two agents share the same file path
- Use ABSOLUTE paths with prefix {self.project_dir}/
- depends_on uses agent NAME strings
- Max {self.max_agents} agents total
- Keep tasks minimal — only create files that are truly needed
- STATIC ASSETS: If ANY HTML template will reference CSS (`<link href="/static/x.css">`)
  or JS (`<script src="/static/x.js">`), the matching `static/x.css` / `static/x.js`
  files MUST be in the SAME agent's assigned_files. Flask/FastAPI/Express do NOT
  auto-generate static assets — a missing file = broken page.
- ENTRY POINT: Exactly one agent must own the runnable entry file (app.py / main.py /
  server.js). That agent also owns requirements.txt or package.json if needed.
- DEFAULT WEB FRAMEWORK: Use **Flask** for any Python web app unless the spec
  explicitly demands FastAPI/async/WebSockets. Reasons: (a) Flask runs with a
  bare `python app.py`, no ASGI server needed; (b) sync-only code is easier to
  generate correctly in one shot; (c) no `from ..models import` packaging traps.
  Only choose FastAPI when realtime/WebSockets/async-IO is genuinely required.
- FLAT MODULE LAYOUT (CRITICAL — fixes the #1 import-error trap): All Python
  modules live as TOP-LEVEL files in `{self.project_dir}/`. NEVER create
  subpackages with `__init__.py` and NEVER use relative imports
  (`from ..models`, `from .fetcher`). Concretely:
    * fetcher.py at project root, imported as `from fetcher import get_data`
    * models.py at project root, imported as `from models import Match`
    * sample_data.json at project root, loaded by `Path(__file__).parent / "sample_data.json"`
  The ONLY allowed subdirectories are `templates/` (Jinja HTML) and `static/`
  (CSS/JS/images) — these are framework conventions, not Python packages.
- DATA LAYER: If the spec mentions an external API, scraping, or seeded data,
  one agent MUST own a `fetcher.py` + `sample_data.json` pair (BOTH at project
  root — see FLAT MODULE LAYOUT above). The fetcher tries a KEYLESS public
  source FIRST (scrape HTML / hit a public JSON endpoint with a real User-Agent,
  wrapped in try/except), THEN a keyed API if env var is set, THEN falls back
  to the JSON seed. The JSON seed is NOT a placeholder — it must contain at
  least 10 realistic rows per top-level section with plausible real names,
  varied numbers, and no Lorem/"Sample X" text. If the spec is domain-specific
  (cricket, stocks, weather, news, crypto), use domain-real values (real team
  names, real tickers, real city names).
  Do NOT bundle the data fetcher into the Backend Agent — give it its own task.
- RICH UI: The frontend agent's task MUST explicitly require (a) Bootstrap 5
  or Tailwind via CDN, (b) hero/header with title + refresh timestamp,
  (c) a card grid (minimum 8 cards visible from seeded data), (d) at least
  one Chart.js or Plotly chart when numeric data exists, (e) a dark/light
  toggle, (f) hover/transition polish. "Shows data" is not the bar — it has
  to look like a real product, not a tutorial.
- OS-CORRECT RUNNER: The user is on '{sys_os}'. The entry-point agent must produce
  a runner the user can double-click / paste:
    * Windows  → `run.bat` invoking `python` with absolute paths
    * Linux/macOS → `run.sh` (chmod +x in instructions) with `#!/usr/bin/env bash`
  The runner must `cd` to `{self.project_dir}` (absolute) before launching.
- PUBLIC URL: DEFAULT behaviour is to print `http://localhost:<port>` from the
  runner — that link IS "shareable" because the user opens it on the same
  machine they ran the app on. ONLY add a tunneling step (ngrok / cloudflared)
  if the spec explicitly mentions 'publicly', 'over the internet', 'deploy',
  'accessible from anywhere', or 'with my team'. Tunneling without an auth
  token fails noisily — don't risk it on a default build.
- USE FRAMEWORK DEFAULTS for serving: do NOT spawn the app from a `subprocess`
  inside the runner just to call `uvicorn`. For Flask (the default), the runner
  is simply `python app.py` and `app.py` ends with `app.run(host="0.0.0.0",
  port={self.port}, debug=True)` — that prints `* Running on http://127.0.0.1:{self.port}`
  which Phantom auto-detects. For FastAPI use `uvicorn app:app --host 0.0.0.0
  --port {self.port}` directly. For Node use `node server.js`.
"""

        def _extract_json(raw: str):
            """Robust extraction — handles markdown fences, chatty preamble,
            and gpt-oss-style extra text. Tries (in order):
              1. Strict parse of the whole payload
              2. Parse content inside the LAST  ```json ... ``` fence
              3. Parse content inside ANY  ``` ... ``` fence
              4. Greedy object from first `{` to last `}`
              5. Greedy array  from first `[` to last `]`
              6. Progressive brace-balancing scan — walks the string and
                 finds the first balanced JSON object/array it can parse.
            Returns the parsed Python value or None.
            """
            s = raw.strip()
            # 1. direct
            try:
                return json.loads(s)
            except Exception:
                pass
            # 2/3. fenced code blocks — collect every triple-backtick body
            fence_matches = list(re.finditer(
                r"```(?:json|JSON)?\s*\n?([\s\S]*?)```", s,
            ))
            # Prefer the LAST fence (models often emit preamble, then JSON fence)
            for m in reversed(fence_matches):
                body = m.group(1).strip()
                try:
                    return json.loads(body)
                except Exception:
                    continue
            # 4. greedy object — but we try every possible end brace from the right
            first_obj = s.find("{")
            if first_obj != -1:
                for end in range(len(s), first_obj, -1):
                    if s[end - 1] != "}":
                        continue
                    try:
                        return json.loads(s[first_obj:end])
                    except Exception:
                        continue
            # 5. greedy array — same progressive shrink from right
            first_arr = s.find("[")
            if first_arr != -1:
                for end in range(len(s), first_arr, -1):
                    if s[end - 1] != "]":
                        continue
                    try:
                        return json.loads(s[first_arr:end])
                    except Exception:
                        continue
            # 6. brace-balancing scan — finds the first balanced object/array
            for i, c in enumerate(s):
                if c not in "{[":
                    continue
                stack = [c]
                in_str = False
                esc = False
                for j in range(i + 1, len(s)):
                    ch = s[j]
                    if esc:
                        esc = False
                        continue
                    if ch == "\\" and in_str:
                        esc = True
                        continue
                    if ch == '"':
                        in_str = not in_str
                        continue
                    if in_str:
                        continue
                    if ch in "{[":
                        stack.append(ch)
                    elif ch in "}]":
                        if not stack:
                            break
                        opener = stack.pop()
                        if (opener, ch) not in (("{", "}"), ("[", "]")):
                            break
                        if not stack:
                            try:
                                return json.loads(s[i:j + 1])
                            except Exception:
                                break
            return None

        def _attempt_plan(prompt_text: str, attempt: int) -> list[AgentTask] | None:
            try:
                resp = client.chat.completions.create(
                    model=get_config("main_model"),
                    messages=[{"role": "user", "content": prompt_text}],
                    max_tokens=2000, temperature=0.2,
                )
                raw = resp.choices[0].message.content or ""
                parsed = _extract_json(raw)
                plan_data = None
                if isinstance(parsed, dict) and "agents" in parsed:
                    self.shared_schema = parsed.get("shared_schema") or {}
                    plan_data = parsed["agents"]
                elif isinstance(parsed, list):
                    plan_data = parsed
                    self.shared_schema = {}
                if not plan_data:
                    log.warning("agents.plan attempt %d — parser could not extract "
                                "a task list. Raw head: %r", attempt, raw[:200])
                    return None
                tasks = []
                for i, item in enumerate(plan_data[:self.max_agents]):
                    t = AgentTask(
                        agent_id       = f"agent_{i+1}",
                        name           = item.get("name", f"Agent {i+1}"),
                        role           = item.get("role", "Developer"),
                        task           = item.get("task", ""),
                        assigned_files = item.get("assigned_files", []),
                        depends_on     = item.get("depends_on", []),
                    )
                    tasks.append(t)
                    self.results[t.agent_id] = AgentResult(agent_id=t.agent_id)

                # ── VALIDATION: plan must have real file assignments ──────
                # If the model returned a "plan" but every agent has no name,
                # no task, or no files, the build will produce 0 files. The
                # v3.0.4 failure mode: 2 agents with blank task + blank
                # assigned_files, running for 90s each, producing nothing.
                valid = sum(
                    1 for t in tasks
                    if t.name and t.name.strip()
                    and t.task and t.task.strip()
                    and t.assigned_files
                )
                if valid == 0 or valid < max(1, len(tasks) // 2):
                    log.warning(
                        "agents.plan attempt %d — %d/%d tasks valid "
                        "(need name+task+assigned_files). Raw head: %r",
                        attempt, valid, len(tasks), raw[:300],
                    )
                    # Wipe the tentatively-inserted results so the retry / fallback
                    # doesn't see zombie entries from a discarded plan.
                    for t in tasks:
                        self.results.pop(t.agent_id, None)
                    return None
                return tasks
            except (json.JSONDecodeError, Exception):
                return None

        # Note: this string contains literal `{` and `}` characters in the
        # text. Using .format() treated them as placeholders and crashed
        # (KeyError). Build it with plain concatenation + f-strings where
        # we ACTUALLY want a substitution, so curly braces stay literal.
        _max_ag = self.max_agents
        retry_prompt = planning_prompt + (
            "\n\nCRITICAL FORMAT INSTRUCTIONS (you failed this once already):\n"
            "  - Return the JSON object ONLY. No preamble like 'Here is the plan:'.\n"
            "  - No ```json``` fences. No markdown. No trailing commentary.\n"
            "  - Your entire response must start with `{` and end with `}`.\n"
            "  - EVERY agent in the plan MUST have: name (non-empty), role,\n"
            "    task (non-empty sentence describing what to build),\n"
            "    assigned_files (non-empty list of file paths).\n"
            "  - An agent with empty task or empty assigned_files is a bug —\n"
            "    the previous attempt emitted this and the build produced 0\n"
            "    files. Don't repeat that mistake.\n"
            f"  - Minimum 2 agents, maximum {_max_ag}. If you cannot fit a\n"
            f"    {_max_ag}-agent plan in the budget, emit fewer agents that\n"
            "    each still have all required fields."
        )

        tasks = _attempt_plan(planning_prompt, attempt=1) or _attempt_plan(retry_prompt, attempt=2)

        # ── FALLBACK: hardcoded default plan when the model can't produce one ──
        # Better to run a sensible default than show "Agent 1 / 0 files".
        if not tasks:
            log.warning("agents.plan exhausted retries — using hardcoded fallback")
            tasks = self._default_fallback_plan()

        if tasks:
            try:
                with open(self.plan_file, "w") as f:
                    json.dump({
                        "prompt": self.prompt,
                        "project_dir": self.project_dir,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "agents": [
                            {
                                "agent_id": t.agent_id,
                                "name": t.name,
                                "role": t.role,
                                "task": t.task,
                                "assigned_files": t.assigned_files,
                                "depends_on": t.depends_on,
                            }
                            for t in tasks
                        ],
                    }, f, indent=2)
            except Exception:
                pass
            self.tasks = tasks
            return tasks

        return []

    # Per-agent wall clock limit. Overridable via config `agent_timeout_s`.
    _DEFAULT_AGENT_TIMEOUT_S = 300

    def execute(self) -> str:
        """Execute all agents in dependency waves. Returns combined output."""
        if not self.tasks:
            return "Agent planning failed — no tasks generated."

        os.makedirs(self.project_dir, exist_ok=True)

        try:
            timeout_s = int(get_config("agent_timeout_s", str(self._DEFAULT_AGENT_TIMEOUT_S))
                            or self._DEFAULT_AGENT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout_s = self._DEFAULT_AGENT_TIMEOUT_S
        # A 0 or negative value would mean "join forever" — that's a silent
        # hang foot-gun, not a feature. Floor to 30s and cap at 1h.
        timeout_s = max(30, min(timeout_s, 3600))

        for wave in self._build_waves():
            thread_map: list[tuple[threading.Thread, AgentTask]] = []
            for task in wave:
                t = threading.Thread(target=self._run_agent, args=(task,), daemon=True)
                thread_map.append((t, task))
                t.start()

            for t, task in thread_map:
                t.join(timeout=timeout_s)
                if t.is_alive():
                    # Thread is still running past the deadline. We can't kill it
                    # cleanly in Python, but we can flag the result and surface
                    # the timeout to the user so they know the output is partial.
                    result = self.results.get(task.agent_id)
                    if result and result.status == "running":
                        result.status   = "failed"
                        result.error    = (
                            f"Timed out after {timeout_s}s — thread still alive, "
                            "output abandoned. Increase `agent_timeout_s` if your "
                            "tasks legitimately need more time."
                        )
                        result.end_time = time.time()
                        self._emit(task.agent_id, "timeout",
                                   f"{task.name} timed out after {timeout_s}s")
                        log.warning(
                            "agent %s (%s) exceeded %ds timeout — marked failed",
                            task.agent_id, task.name, timeout_s,
                        )

        # Post-build cross-module import audit. Catches the agent-coordination
        # bug where one agent imports a function name a sibling agent never
        # defined (e.g. app.py does `from fetcher import get_data` but
        # fetcher.py only exports `fetch_matches`). One focused fix-pass
        # delegated to the most-affected agent.
        try:
            self._reconcile_imports()
        except Exception as e:
            log.warning("import-reconciliation pass failed: %s", e)

        return self._build_output()

    def _reconcile_imports(self) -> None:
        """Walk every .py at project root, parse `from <local> import <names>`,
        verify each name actually exists as a top-level def/class/assignment in
        the target module. If mismatches found, ask one agent to fix them."""
        import ast as _ast

        # Collect top-level exports per local module
        py_files = [f for f in os.listdir(self.project_dir)
                    if f.endswith(".py") and os.path.isfile(os.path.join(self.project_dir, f))]
        if not py_files:
            return
        exports: dict[str, set[str]] = {}
        for fname in py_files:
            mod = fname[:-3]
            fpath = os.path.join(self.project_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    tree = _ast.parse(f.read(), filename=fname)
            except (OSError, SyntaxError):
                continue
            names: set[str] = set()
            for node in tree.body:
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, _ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, _ast.Name):
                            names.add(tgt.id)
                elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                    names.add(node.target.id)
                elif isinstance(node, _ast.ImportFrom):
                    for a in node.names:
                        names.add(a.asname or a.name)
                elif isinstance(node, _ast.Import):
                    for a in node.names:
                        names.add((a.asname or a.name).split(".")[0])
            exports[mod] = names

        # Find broken imports: caller_file → list of "missing X from Y"
        problems: dict[str, list[str]] = {}
        for fname in py_files:
            fpath = os.path.join(self.project_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    tree = _ast.parse(f.read(), filename=fname)
            except (OSError, SyntaxError):
                continue
            for node in _ast.walk(tree):
                if isinstance(node, _ast.ImportFrom) and node.module and node.level == 0:
                    target = node.module.split(".")[0]
                    if target not in exports:
                        continue
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        if alias.name not in exports[target]:
                            avail = ", ".join(sorted(exports[target])[:8]) or "(none)"
                            problems.setdefault(fname, []).append(
                                f"`from {target} import {alias.name}` — but {target}.py only exports: {avail}"
                            )
        if not problems:
            return

        # Pick the agent whose assigned files include the broken caller
        owner_task = None
        first_caller = next(iter(problems.keys()))
        caller_path = os.path.join(self.project_dir, first_caller)
        for t in self.tasks:
            if caller_path in (os.path.normpath(p) for p in t.assigned_files):
                owner_task = t
                break
        if not owner_task:
            owner_task = self.tasks[0]

        msg_lines = []
        for fname, issues in problems.items():
            msg_lines.append(f"  • {fname}:")
            for issue in issues:
                msg_lines.append(f"      - {issue}")

        fix_prompt = (
            f"You are the cleanup agent. The multi-agent build finished, but cross-module "
            f"imports don't resolve — the app crashes at startup with ImportError. "
            f"Fix these mismatches:\n\n"
            + "\n".join(msg_lines) +
            f"\n\nProject dir: {self.project_dir}\n"
            f"For EACH mismatch, decide which side to change and use write_file (full new "
            f"file content) or edit_file (targeted patch):\n"
            f"  • If the called name is reasonable → ADD/RENAME the export in the target module to match.\n"
            f"  • If the export is reasonable → CHANGE the import in the caller to use the existing name.\n"
            f"Default to fixing the EXPORT side (target module) — fewer downstream callers to update.\n"
            f"After your edits, every `from X import Y` in this project must resolve to a "
            f"real top-level name in X.py. Output nothing except tool calls."
        )
        try:
            from omnicli.engine import generate_response
            self._emit(owner_task.agent_id, "running",
                       f"Reconciling cross-module imports — {sum(len(v) for v in problems.values())} mismatch(es)")
            generate_response(fix_prompt, [], self.trust_level)
            self._emit(owner_task.agent_id, "done", "Imports reconciled")
        except Exception as e:
            log.warning("import reconciliation generate_response failed: %s", e)

    # ── Fallback plan when the model can't produce a valid one ───────────

    def _default_fallback_plan(self) -> list[AgentTask]:
        """Hardcoded 4-agent web-app layout used when plan() exhausts retries.
        Produces a working Flask project skeleton with keyless fetcher,
        seeded data, cache, backend, frontend, and a Windows runner."""
        pdir = self.project_dir
        tasks = [
            AgentTask(
                agent_id="agent_1",
                name="Fetcher Agent",
                role="Data Fetcher Developer",
                task=("Write fetcher.py that tries (1) a keyless public HTTP "
                      "JSON endpoint relevant to the user's domain with a real "
                      "User-Agent wrapped in try/except, (2) a keyed API read "
                      "from env (skip if missing), (3) falls back to loading "
                      "seed_data.json. Also produce seed_data.json with >= 10 "
                      "realistic entries per section — use plausible real names "
                      "and varied numbers, no Lorem Ipsum."),
                assigned_files=[
                    os.path.join(pdir, "fetcher.py"),
                    os.path.join(pdir, "seed_data.json"),
                ],
                depends_on=[],
            ),
            AgentTask(
                agent_id="agent_2",
                name="Backend Agent",
                role="Backend Developer",
                task=("Write app.py (Flask) that imports fetcher.get_data(), "
                      "renders templates/index.html with the data, and binds "
                      f"to port {self.port}. Also write models.py with "
                      "dataclasses + cache.py with a 5-minute in-memory TTL "
                      "cache. Also write requirements.txt with flask + "
                      "requests."),
                assigned_files=[
                    os.path.join(pdir, "app.py"),
                    os.path.join(pdir, "models.py"),
                    os.path.join(pdir, "cache.py"),
                    os.path.join(pdir, "requirements.txt"),
                ],
                depends_on=["Fetcher Agent"],
            ),
            AgentTask(
                agent_id="agent_3",
                name="Frontend Agent",
                role="Frontend Developer",
                task=("Write templates/index.html + static/style.css. Use "
                      "the dark-gradient palette (#0a0a0f → #13131e), cyan/"
                      "violet accents, glassmorphism cards, Outfit+Inter+"
                      "JetBrains-Mono fonts, ambient orbs, responsive CSS "
                      "grid, reveal-on-scroll, Chart.js inline visualizations. "
                      "No plain Bootstrap cards."),
                assigned_files=[
                    os.path.join(pdir, "templates", "index.html"),
                    os.path.join(pdir, "static", "style.css"),
                ],
                depends_on=["Backend Agent"],
            ),
            AgentTask(
                agent_id="agent_4",
                name="Runner Agent",
                role="Runner Script Developer",
                task=("Write run.bat (Windows) + run.sh (Linux/macOS). Each "
                      f"must cd into {pdir} with absolute paths and invoke "
                      f"python app.py. Print 'App listening at "
                      f"http://localhost:{self.port}' on first line."),
                assigned_files=[
                    os.path.join(pdir, "run.bat"),
                    os.path.join(pdir, "run.sh"),
                ],
                depends_on=["Backend Agent"],
            ),
        ]
        # Trim to max_agents and register the results slots
        tasks = tasks[: self.max_agents]
        for t in tasks:
            self.results[t.agent_id] = AgentResult(agent_id=t.agent_id)
        return tasks

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_waves(self) -> list[list[AgentTask]]:
        """Topological sort: tasks with no unmet deps go in the same wave."""
        name_to_id = {t.name: t.agent_id for t in self.tasks}
        id_to_task = {t.agent_id: t       for t in self.tasks}
        remaining  = set(t.agent_id for t in self.tasks)
        completed  = set()
        waves      = []

        for _ in range(len(self.tasks) + 1):
            if not remaining:
                break
            wave = [
                id_to_task[aid] for aid in remaining
                if {name_to_id.get(d, d) for d in id_to_task[aid].depends_on}.issubset(completed)
            ]
            if not wave:
                wave = [id_to_task[aid] for aid in remaining]   # break cycle
            for task in wave:
                remaining.discard(task.agent_id)
                completed.add(task.agent_id)
            waves.append(wave)

        return waves

    def _run_agent(self, task: AgentTask):
        result = self.results[task.agent_id]
        result.status     = "running"
        result.start_time = time.time()
        self._emit(task.agent_id, "running", f"{task.name} initialising…")

        try:
            from omnicli.engine import generate_response

            team_context = self._team_context(task)
            file_list    = "\n".join(f"  • {f}" for f in task.assigned_files)
            # Inject the data contract every agent must respect — the backend
            # emits these field names, the frontend renders them, the fetcher
            # produces them. Without this each agent invents its own field
            # names and the UI shows 'undefined'.
            schema_block = ""
            if self.shared_schema:
                schema_block = (
                    "\nSHARED DATA CONTRACT (MANDATORY — every agent must use these EXACT field names):\n"
                    "```json\n" + json.dumps(self.shared_schema, indent=2) + "\n```\n"
                    "• Backend endpoints MUST return objects with exactly these keys.\n"
                    "• Frontend templates MUST read exactly these keys (no invented fields like `match.title` or `match.summary`).\n"
                    "• Fetcher MUST emit exactly these keys (real-API output and sample-data fallback).\n"
                    "• If a key isn't in the schema, do NOT reference it — pick one that is.\n\n"
                )

            # ── Research block — real live data Phantom scraped before the build ──
            research_block = ""
            research_path = os.path.join(self.project_dir, "research.json")
            if self.research_result and self.research_result.ok and os.path.isfile(research_path):
                structured_preview = ""
                try:
                    structured_preview = json.dumps(self.research_result.structured, indent=2)[:2000]
                except Exception:
                    pass
                research_block = (
                    "\n🌐 LIVE RESEARCH DATA (Phantom scraped this from the web BEFORE the build):\n"
                    f"  File:   {research_path}\n"
                    f"  Domain: {self.research_result.domain}\n"
                    f"  Sources scraped: {[s['url'] for s in self.research_result.sources]}\n"
                    "  Structured preview:\n"
                    "```json\n" + structured_preview + "\n```\n"
                    "MANDATORY for anyone writing a fetcher or seed file:\n"
                    "  • Load research.json as the PRIMARY seed source. It contains REAL\n"
                    "    current data — not LLM-hallucinated fake matches / stale prices.\n"
                    "  • Do NOT invent alternative data that contradicts research.json.\n"
                    "  • If research.json lacks a field you need, fall through to fetching\n"
                    "    live from the API — but NEVER fabricate current-season data.\n"
                    "  • The 'demo data' banner still applies if both research.json\n"
                    "    and the live API are empty at runtime, but with research.json\n"
                    "    populated the banner should read 'Cached snapshot' not 'Demo'.\n\n"
                )

            agent_prompt = (
                f"You are {task.name} — a {task.role} working in a parallel dev team.\n\n"
                f"OVERALL PROJECT GOAL:\n{self.prompt}\n\n"
                f"YOUR SPECIFIC TASK:\n{task.task}\n\n"
                f"PROJECT DIRECTORY: {self.project_dir}\n"
                f"YOUR ASSIGNED FILES (write ONLY these using write_file):\n{file_list}\n\n"
                f"{schema_block}"
                f"{research_block}"
                f"{team_context}\n\n"
                "RULES:\n"
                "1. Use write_file for EVERY file in your assigned list — no exceptions.\n"
                "2. Write complete, production-ready code — no placeholders.\n"
                "3. Make your code integrate with the rest of the project.\n"
                "4. STATIC ASSETS: If you write an HTML template that links a stylesheet "
                "   (`<link href=\"{{{{ url_for('static', filename='style.css') }}}}\">` or "
                "   `<link href=\"/static/style.css\">`) or a script, the matching CSS/JS file "
                "   MUST be written too. Flask/FastAPI do NOT auto-generate static files — "
                "   a missing file means a broken page.\n"
                "5. REQUIREMENTS COMPLETENESS: If you own requirements.txt (or package.json), "
                "   scan your OWN Python/JS imports and include EVERY third-party package. "
                "   Common misses: flask-cors, flask-login, flask-sqlalchemy, python-dotenv, "
                "   requests, httpx, pydantic, uvicorn, gunicorn. An app that crashes on "
                "   `ModuleNotFoundError` = broken build. **NEVER list Python stdlib modules** "
                "   (pprint, json, os, sys, datetime, pathlib, re, collections, typing, asyncio, "
                "   subprocess, threading, etc.) — they are NOT on PyPI and `pip install pprint` "
                "   fails the entire install. Only list what you `import` from third-party packages.\n"
                "6. ROUTES: If your Flask/FastAPI app has an index route at `/`, it MUST "
                "   render the HTML template from the frontend agent. A frontend with no "
                "   route to serve it is a broken app.\n"
                "7. FLAT IMPORTS — NO RELATIVE IMPORTS, NO SUBPACKAGES. All Python modules "
                "   (fetcher.py, models.py, helpers.py, etc.) live at the PROJECT ROOT, not "
                "   inside `data/`, `core/`, `lib/`. Import them as `from fetcher import X`, "
                "   NEVER `from .fetcher import X` or `from ..models import Y`. Subpackages "
                "   without `__init__.py` cause `ImportError: attempted relative import beyond "
                "   top-level package` when the user runs `python app.py`. The ONLY allowed "
                "   subdirs are `templates/` and `static/` (framework conventions).\n"
                "8. PREFER FLASK over FastAPI for web apps unless realtime/WebSockets are "
                "   genuinely needed — Flask runs with bare `python app.py`, no ASGI server, "
                "   no async pitfalls. End your app.py with: "
                f"   `if __name__ == '__main__': app.run(host='0.0.0.0', port={self.port}, debug=True)`\n"
                "9. NO PLACEHOLDER TEXT. Banned strings in any file you emit: 'Lorem ipsum', "
                "   'Sample 1/2/3', 'Example Team', 'TBD', 'N/A' as a value, 'undefined', "
                "   'TODO', '...' as visible UI text. Every seeded value must look like REAL "
                "   production data (plausible names, realistic numbers, varied entries). "
                "   If you write sample_data.json / seed.json / *.json data files, each "
                "   top-level list MUST have at least 10 entries of varied, realistic content.\n"
                "10. RICH UI BAR (frontend agents): the template must include (a) Bootstrap 5 "
                "    or Tailwind via CDN, (b) a hero header with product title + last-updated "
                "    timestamp, (c) a responsive card grid rendering ALL seeded rows (not just "
                "    the first three), (d) at least one Chart.js / Plotly chart if numeric "
                "    data exists in the schema, (e) a dark/light theme toggle backed by "
                "    localStorage, (f) hover transitions and section dividers. Empty-state "
                "    messages must still show the seeded data, never 'No data'.\n"
                "11. DATA-FETCHER BAR: if you own fetcher.py, try keyless public sources "
                "    FIRST (scrape HTML with a real User-Agent, hit public JSON endpoints), "
                "    wrap each in try/except with a short timeout, then try a keyed API if "
                "    env var is set, then fall back to loading sample_data.json. Log which "
                "    path was taken. NEVER raise — always return data.\n"
                "12. After writing all files, output a 2-line summary: files written + how to run.\n"
                "13. Do NOT write files belonging to other agents.\n"
                "14. Do NOT output raw JSON plan objects — execute the plan, don't describe it."
            )

            response, _ = generate_response(agent_prompt, [], self.trust_level)

            # Retry once if assigned files are missing after first pass.
            # Focused retry: include hints about WHAT each missing file should
            # contain, derived from sibling files the agent already wrote.
            missing = [f for f in task.assigned_files if not os.path.exists(f)]
            if missing:
                self._emit(task.agent_id, "running",
                           f"{task.name} retrying — {len(missing)} file(s) missing")
                hints = []
                for mfile in missing:
                    base = os.path.basename(mfile).lower()
                    if base in ("requirements.txt", "requirements"):
                        # Scan all written .py files for third-party imports — give
                        # the model the exact list it needs to put in requirements.txt
                        # so it can't drop the file again.
                        pkgs = self._scan_imports(self.project_dir)
                        hints.append(
                            f"  • {mfile}\n"
                            f"    → Detected third-party imports across the project: "
                            f"{', '.join(sorted(pkgs)) or '(none — write at minimum: flask)'}\n"
                            f"    → write_file with those packages, ONE per line, pinned-or-unpinned both fine."
                        )
                    elif base in ("package.json",):
                        hints.append(f"  • {mfile}\n    → write_file with {{\"name\":\"app\",\"version\":\"1.0.0\",\"dependencies\":{{...}}}}")
                    elif base.endswith(".bat"):
                        hints.append(f"  • {mfile}\n    → write_file with: `@echo off\\ncd /d {self.project_dir}\\npython app.py`")
                    elif base.endswith(".sh"):
                        hints.append(f"  • {mfile}\n    → write_file with: `#!/usr/bin/env bash\\ncd \"{self.project_dir}\"\\npython3 app.py`")
                    else:
                        hints.append(f"  • {mfile}")
                retry_prompt = (
                    f"You are {task.name} mid-task. You already wrote some files but these "
                    f"specific assigned files are STILL MISSING and must be created NOW:\n\n"
                    + "\n".join(hints) +
                    f"\n\nProject dir: {self.project_dir}\n"
                    f"Call write_file ONCE per missing file with complete content. "
                    f"Do not re-write files that already exist. Do not skip any. "
                    f"Output nothing except the write_file tool calls."
                )
                retry_response, _ = generate_response(retry_prompt, [], self.trust_level)
                response = response + "\n\n---\n[Retry Pass]\n" + retry_response

            # Syntax-validate every .py file the agent owns. Catches the
            # double-escape bug (literal `\n` characters), unterminated
            # strings, indentation errors — anything `python -c` would crash on.
            broken_py = []
            for f in task.assigned_files:
                if not f.endswith(".py") or not os.path.exists(f):
                    continue
                try:
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        src = fh.read()
                    import ast as _ast
                    _ast.parse(src)
                except SyntaxError as se:
                    broken_py.append((f, f"{se.msg} (line {se.lineno})"))
                except OSError:
                    pass
            if broken_py:
                self._emit(task.agent_id, "running",
                           f"{task.name} fixing — {len(broken_py)} file(s) have syntax errors")
                fix_lines = []
                for fpath, errmsg in broken_py:
                    fix_lines.append(f"  • {fpath}\n    → SyntaxError: {errmsg}")
                fix_prompt = (
                    f"You are {task.name}. The following Python files YOU wrote have syntax errors "
                    f"that prevent the app from starting:\n\n"
                    + "\n".join(fix_lines) +
                    f"\n\nProject dir: {self.project_dir}\n"
                    f"Call write_file ONCE per broken file with the COMPLETE corrected source. "
                    f"Common cause: double-escaped strings (you emitted literal `\\n` instead of "
                    f"actual newlines). Use real newlines in your write_file content. "
                    f"Output nothing except the write_file tool calls."
                )
                fix_response, _ = generate_response(fix_prompt, [], self.trust_level)
                response = response + "\n\n---\n[Syntax-Fix Pass]\n" + fix_response

            result.status     = "done"
            result.output     = response
            result.end_time   = time.time()
            result.files_written = [f for f in task.assigned_files if os.path.exists(f)]
            still_missing = [f for f in task.assigned_files if not os.path.exists(f)]
            if still_missing:
                result.error = f"Missing after retry: {', '.join(os.path.basename(f) for f in still_missing)}"
            self._emit(task.agent_id, "done",
                       f"{task.name} done — {len(result.files_written)}/{len(task.assigned_files)} files written")

        except Exception as e:
            result.status   = "failed"
            result.error    = str(e)
            result.end_time = time.time()
            self._emit(task.agent_id, "failed", str(e))

    @staticmethod
    def _scan_imports(project_dir: str) -> set[str]:
        """Walk all .py files in project_dir and return third-party top-level
        package names. Used by the focused-retry to auto-derive a sane
        requirements.txt when the model drops the file."""
        STDLIB = {
            "os","sys","re","json","time","datetime","pathlib","typing","math",
            "random","threading","subprocess","collections","itertools","functools",
            "http","urllib","io","logging","asyncio","contextlib","dataclasses",
            "enum","traceback","uuid","warnings","abc","copy","textwrap","string",
            "tempfile","shutil","glob","argparse","csv","sqlite3","hashlib","base64",
            "html","xml","email","socket","ssl","platform","getpass","configparser",
        }
        # Map import-name → pip-name where they differ
        ALIAS = {"cv2":"opencv-python","PIL":"Pillow","yaml":"PyYAML","sklearn":"scikit-learn",
                 "bs4":"beautifulsoup4","dotenv":"python-dotenv","jose":"python-jose"}
        pkgs: set[str] = set()
        try:
            for root, _, files in os.walk(project_dir):
                for fn in files:
                    if not fn.endswith(".py"):
                        continue
                    try:
                        with open(os.path.join(root, fn), "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                m = re.match(r"\s*(?:from|import)\s+([A-Za-z_][\w]*)", line)
                                if not m:
                                    continue
                                top = m.group(1)
                                if top in STDLIB:
                                    continue
                                # Skip local modules — those that exist as a sibling .py
                                if os.path.exists(os.path.join(project_dir, top + ".py")):
                                    continue
                                pkgs.add(ALIAS.get(top, top))
                    except OSError:
                        continue
        except OSError:
            pass
        return pkgs

    def _team_context(self, current: AgentTask) -> str:
        lines = []
        for t in self.tasks:
            if t.agent_id == current.agent_id:
                continue
            r = self.results.get(t.agent_id)
            if r and r.status == "done" and r.files_written:
                lines.append(f"  • {t.name} has written: {', '.join(r.files_written)}")
            else:
                lines.append(f"  • {t.name} will write: {', '.join(t.assigned_files)}")
        return ("TEAM PLAN (for integration awareness):\n" + "\n".join(lines)) if lines else ""

    def _emit(self, agent_id: str, status: str, msg: str):
        if self.on_status:
            try:
                self.on_status(agent_id, status, msg)
            except Exception:
                pass

    def _build_output(self) -> str:
        header_lines = [
            f"## 🚀 Multi-Agent Project Complete",
            f"**Directory:** `{self.project_dir}`",
            f"**Agents:** {len(self.tasks)}  |  "
            f"**Succeeded:** {sum(1 for r in self.results.values() if r.status=='done')}",
            "",
        ]
        agent_sections = []
        all_files = []

        for task in self.tasks:
            r = self.results.get(task.agent_id)
            if not r: continue
            icon = "✅" if r.status == "done" else "❌"
            lines = [f"### {icon} {task.name}  `{r.elapsed}s`"]
            if r.files_written:
                lines.append("**Files written:**")
                for f in r.files_written:
                    sz = os.path.getsize(f) if os.path.exists(f) else 0
                    lines.append(f"  - `{f}` ({sz:,} bytes)")
                    all_files.append(f)
            if r.error:
                lines.append(f"**Error:** {r.error}")
            agent_sections.append("\n".join(lines))

        quick_start = []
        if all_files:
            # Detect entry point: prefer app.py > main.py > index.js > server.js > manage.py
            entry = next(
                (f for name in ("app.py", "main.py", "index.js", "server.js", "manage.py", "run.py")
                 for f in all_files if f.endswith(name)),
                None,
            )
            run_cmd = ""
            if entry:
                if entry.endswith(".py"):
                    run_cmd = f"python {entry}"
                elif entry.endswith(".js"):
                    run_cmd = f"node {entry}"
            quick_start = [
                "\n---",
                "### Quick Start",
                f"```bash\ncd {self.project_dir}" +
                (f"\npip install -r requirements.txt  # if present" if any(f.endswith("requirements.txt") for f in all_files) else "") +
                (f"\n{run_cmd}" if run_cmd else f"\nls {self.project_dir}") +
                "\n```",
            ]

        return "\n\n".join(header_lines + agent_sections + quick_start)

    # ── Status snapshot ───────────────────────────────────────────────────────

    def status_snapshot(self) -> list[dict]:
        return [
            {
                "id":     t.agent_id,
                "name":   t.name,
                "role":   t.role,
                "status": self.results.get(t.agent_id, AgentResult(t.agent_id)).status,
                "files":  t.assigned_files,
            }
            for t in self.tasks
        ]
