#!/usr/bin/env python3
"""Workshop launcher for The Virtual Biotech (Module 3 of "Swarm in the Lab").

Runs the real Virtual Biotech code, without editing it, with workshop adjustments:
  * a scaled-down team: any subset of its agents, plus optional new division agents
  * the Module 2 paper agents added as extra MCP tools for one specialist
  * only the MCP servers that team needs (fewer processes on a 16 GB machine)
  * a lower reasoning effort and turn caps, so a run fits a workshop slot
  * a time limit, after which the run is stopped cleanly and saved as interrupted (SIGINT does the same)
  * extra guards that block the obvious ways an agent's shell command could print credentials
    (they reduce the risk; they can't remove it, so use capped keys and revoke them afterwards)
  * a 'plan' mode that shows who would do what, with no model calls

    python vbt_workshop.py plan --config team.json
    python vbt_workshop.py run  --config team.json --query-file brief.md

Run it with the Virtual Biotech environment's Python (Python 3.11, conda env 'vbt').
Machine-readable lines start with '§' followed by JSON; everything else is log output.
"""
import argparse
import asyncio
import dataclasses
import json
import os
import signal
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

# System prompt files, keyed as the Virtual Biotech registry expects (mirrors gradio_cso_app.load_prompts).
PROMPT_FILES = {
    "cso": "cso/system_prompt.md",
    "genomics": "target_id/system_prompts/genomics_analyst_short.md",
    "functional_genomics": "target_id/system_prompts/functional_genomics_analyst_short.md",
    "single_cell": "target_id/system_prompts/single_cell_analyst_short.md",
    "fda_safety": "safety/system_prompts/fda_safety_officer_short.md",
    "bio_pathways_ppi": "safety/system_prompts/bio_pathways_ppi_analyst_short.md",
    "clinical_trialist": "safety/system_prompts/clinical_trialist_short.md",
    "target_biologist": "modality_selection/system_prompts/target_biologist_short.md",
    "medchem": "modality_selection/system_prompts/medchem_pharmacologist_short.md",
    "chief_of_staff": "chief_of_staff/system_prompt.md",
    "scientific_reviewer": "scientific_reviewer/system_prompt.md",
    "trial_matching": "trial_matching/system_prompt.md",
}
SUPPORT_AGENTS = ("chief-of-staff", "scientific-reviewer")   # pinned to Haiku by the Virtual Biotech
BASE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash", "TodoWrite", "Skill",
              "mcp__provenance__register_artifact", "mcp__provenance__list_artifacts")

# Added to the Virtual Biotech's own shell-command guards (matched case-insensitively).
CREDENTIAL_GUARDS = [
    (r"\$\{?\w*(?:API_KEY|AUTH_TOKEN|SECRET|PASSWORD|TOKEN)\b", "reading a credential variable"),
    (r"\bprintenv\b", "printing environment variables"),
    (r"(?:^|[;&|(]\s*)(?:env|export|set|declare\s+-x)\s*(?:$|[;&|)>])", "listing environment variables"),
    (r"/proc/[^\s/]+/environ", "reading a process environment"),
    (r"\bdeclare\s+-p\b|\bcompgen\s+-[ev]\b", "listing shell variables"),
    (r"\bps\b[^|;&]*\be\b|\bps\s+[^|;&]*e[a-z]*w", "listing other processes' environments"),
    (r"\.config/swarm-lab|keys\.env|\.credentials\.json", "reading a credential file"),
]


WRAPUP_PROMPT = (
    "Time is up for new work. Do not delegate to any agent and do not start any new analysis or query. Using only "
    "the results already on record in this run (the specialists' reports, their registered files and tool calls), "
    "write your final answer now under these headings: Target rationale; Safety liabilities; Suggested modality; "
    "Open questions and next steps. File a claim for every factual statement, citing the evidence on record. "
    "List any planned analysis that wasn't finished as an open question, and say plainly that the research was "
    "cut short. You have about {minutes} minutes."
)


def emit(kind, **data):
    print("§" + json.dumps({"kind": kind, **data}, default=str), flush=True)


def load_prompts(vbt_dir):
    base = Path(vbt_dir) / "src" / "agents"
    return {key: (base / rel).read_text() for key, rel in PROMPT_FILES.items()}


def workspace_note(name, workspace_dir):
    ws = Path(workspace_dir) / "work" / name
    return (f"IMPORTANT — YOUR WORKSPACE. Write every file under {ws} (scripts in code/scripts, data in data/, "
            f"results in results/figures, results/tables and results/reports), always with absolute paths. "
            "Before returning, call mcp__provenance__register_artifact for each supporting file and return the "
            "registered paths and the tool-use IDs behind your findings, so the CSO can cite them. If a tool "
            "fails, report the failure; a failed query is not evidence.\n\n")


def time_budget_note(cfg):
    """Specialists can't see the run's clock, so tell them their share of it."""
    budget = max(3, round(float(cfg.get("time_limit_min", 15)) * 0.5))
    return (
        f"\n\n## Time budget (workshop session)\n"
        f"Return your report to the CSO within about {budget} minutes of starting; the whole run is stopped soon "
        "after that. Prefer quick, targeted queries. Don't download large single-cell matrices (keep any CELLxGENE "
        "Census extract to a few thousand cells, filtered to the genes you need) and don't start any step you "
        "expect to take more than two minutes. If you run short of time, stop and return what you have, saying "
        "what is missing.\n")


def paper_note(pa, tool_names):
    return (
        "\n\n## Paper agents available to you\n"
        "You also have tools from 'paper agents': MCP servers that Paper2Agent built from published papers' own "
        "code and tested against those papers' results. Tools: " + ", ".join(tool_names) + ".\n"
        f"The workshop dataset for them is at {pa.get('dataset', '(not given)')}. Pass file paths to the tools and "
        "ask them to write outputs inside your workspace. Prefer these tools over writing your own version of the "
        "same analysis, and say which paper-agent tool produced each result.\n")


def _fields(cls):
    return {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()


def shape_team(agents, cfg, workspace_dir):
    """Keep the chosen agents, add custom ones and paper-agent tools, and apply workshop limits."""
    from claude_agent_sdk import AgentDefinition
    fields = _fields(AgentDefinition)
    team = {name: agents[name] for name in cfg.get("team", []) if name in agents}
    for name, spec in (cfg.get("custom_agents") or {}).items():
        team[name] = AgentDefinition(
            description=spec["description"],
            prompt=workspace_note(name, workspace_dir) + spec["prompt"],
            tools=list(dict.fromkeys(list(spec.get("tools", [])) + list(BASE_TOOLS))),
            model=spec.get("model", "inherit"),
        )
    pa = cfg.get("paper_agents") or {}
    holder = pa.get("attach_to")
    if holder in team:
        extra = [f"mcp__{server}__{tool}" for server, tools in (pa.get("tools") or {}).items()
                 if server in (pa.get("servers") or {}) for tool in tools]
        if extra:
            agent = team[holder]
            agent.tools = list(dict.fromkeys(list(agent.tools or []) + extra))
            agent.prompt = agent.prompt + paper_note(pa, extra)
    for name, agent in team.items():
        if name in SUPPORT_AGENTS:
            continue
        agent.prompt = (agent.prompt or "") + time_budget_note(cfg)
        if cfg.get("effort") and "effort" in fields:
            agent.effort = cfg["effort"]
        if cfg.get("specialist_max_turns") and "maxTurns" in fields:
            agent.maxTurns = cfg["specialist_max_turns"]
    return team


def servers_for(team):
    names = {"provenance"}
    for agent in team.values():
        for tool in agent.tools or []:
            if tool.startswith("mcp__") and tool.count("__") >= 2:
                names.add(tool.split("__")[1])
    return names


def team_note(team, cfg):
    lines = [f"- `{name}`: {agent.description}" for name, agent in team.items()]
    return (
        "\n\n## Workshop session: your team today\n"
        "Only these agents exist in this session. Do not delegate to any other agent name; if a planned analysis "
        "needs expertise you don't have, say so in your report instead of improvising.\n" + "\n".join(lines) + "\n\n"
        "The user's brief is detailed: do not ask clarifying questions. State your assumptions and proceed.\n"
        f"You have about {cfg.get('time_limit_min', 15)} minutes. Use one round of delegation (run specialists in "
        "parallel), then the scientific reviewer if it is on your team, then synthesise and file claims.\n"
        "Call each specialist at most once, and give it all of its analyses in that one task. Specialists have been "
        "told to report back within about half of your time, so leave the rest for review, synthesis and claims.\n")


def _describe(team, all_names, mcp_names, paper_servers):
    out = []
    for name, agent in team.items():
        by_server, builtin = {}, []
        for tool in agent.tools or []:
            if tool.startswith("mcp__") and tool.count("__") >= 2:
                _, server, short = tool.split("__", 2)
                by_server.setdefault(server, []).append(short)
            else:
                builtin.append(tool)
        out.append({"name": name, "description": agent.description, "model": agent.model,
                    "effort": getattr(agent, "effort", None), "max_turns": getattr(agent, "maxTurns", None),
                    "custom": name not in all_names, "tools_by_server": by_server, "builtin_tools": builtin,
                    "prompt_chars": len(agent.prompt or "")})
    return out


def _setup(cfg):
    vbt_dir = Path(cfg["vbt_dir"]).resolve()
    if not (vbt_dir / "src" / "agents" / "registry.py").exists():
        raise SystemExit(f"No Virtual Biotech checkout at {vbt_dir}")
    os.chdir(vbt_dir)
    sys.path.insert(0, str(vbt_dir))
    return vbt_dir


def cmd_plan(cfg):
    """The team the CSO would get, which tools each agent holds, and which servers would start. No model calls."""
    vbt_dir = _setup(cfg)
    from src.agents.registry import build_specialist_agents
    everyone = build_specialist_agents(load_prompts(vbt_dir), workspace_dir="<run folder>",
                                       specialist_model=cfg.get("model", "inherit"))
    team = shape_team(everyone, cfg, "<run folder>")
    mcp_file = vbt_dir / "mcp_config.json"
    mcp_names = set(json.loads(mcp_file.read_text()).get("mcpServers", {})) if mcp_file.exists() else set()
    paper_servers = set(((cfg.get("paper_agents") or {}).get("servers") or {}))
    servers = [{"name": s, "configured": s in mcp_names or s in paper_servers, "paper_agent": s in paper_servers}
               for s in sorted(servers_for(team))]
    emit("plan",
         available=[{"name": n, "description": a.description} for n, a in everyone.items()],
         team=_describe(team, set(everyone), mcp_names, paper_servers),
         unknown=[n for n in cfg.get("team", []) if n not in everyone],
         servers=servers, mcp_config_found=mcp_file.exists())


def _summarise(run_dir):
    if not run_dir:
        return {"run_dir": None, "status": "no run folder"}
    rd = Path(run_dir)
    def read(rel):
        try:
            return json.loads((rd / rel).read_text())
        except (OSError, ValueError):
            return None
    manifest = read("MANIFEST.json") or {}
    cost = read("logs/cost_report.json") or {}
    claims = read("evidence/claims.json") or {}
    claims = claims.get("claims", []) if isinstance(claims, dict) else claims
    turns = cost.get("turns") or [{}]
    return {"run_dir": str(rd), "status": manifest.get("status", "unknown"),
            "turn_status": turns[-1].get("status"), "cost_usd": cost.get("total_cost_usd"),
            "agents": turns[-1].get("agents_dispatched", []), "n_claims": len(claims)}


async def cmd_run(cfg, query):
    vbt_dir = _setup(cfg)
    os.environ["VBT_RUNS_DIR"] = str(Path(cfg["runs_dir"]).resolve())
    Path(cfg["runs_dir"]).mkdir(parents=True, exist_ok=True)
    emit("status", message="Loading the Virtual Biotech …")

    import claude_agent_sdk as sdk
    from src.utils import agent_hooks
    agent_hooks.SYSTEM_CMD_PATTERNS.extend(CREDENTIAL_GUARDS)
    import gradio_cso_app as app
    import run_vbt

    preview = shape_team(app.build_specialist_agents(load_prompts(vbt_dir), workspace_dir="/preview",
                                                     specialist_model=cfg["model"]), cfg, "/preview")
    keep = servers_for(preview)
    paper_servers = (cfg.get("paper_agents") or {}).get("servers") or {}

    original_build = app.build_specialist_agents
    def build(prompts, workspace_dir=None, specialist_model="inherit"):
        return shape_team(original_build(prompts, workspace_dir=workspace_dir, specialist_model=specialist_model),
                          cfg, workspace_dir)
    app.build_specialist_agents = build

    original_prompts = app.load_prompts
    def prompts():
        loaded = original_prompts()
        loaded["cso"] = loaded["cso"] + team_note(preview, cfg)
        return loaded
    app.load_prompts = prompts

    original_resolve = app.resolve_mcp_servers
    def resolve(servers, root):
        out = {name: c for name, c in original_resolve(servers, root).items() if name in keep}
        for name, c in paper_servers.items():
            if name in keep:
                out[name] = {k: v for k, v in c.items() if k in ("type", "command", "args", "env", "url", "headers")}
        return out
    app.resolve_mcp_servers = resolve

    OriginalOptions = sdk.ClaudeAgentOptions
    def options(*args, **kwargs):
        if cfg.get("cso_effort"):
            kwargs["effort"] = cfg["cso_effort"]
        if cfg.get("cso_max_turns"):
            kwargs["max_turns"] = cfg["cso_max_turns"]
        return OriginalOptions(*args, **kwargs)
    sdk.ClaudeAgentOptions = options

    emit("status", message=f"Team: {', '.join(preview)} · servers: {', '.join(sorted(keep))} · model {cfg['model']}")
    from claude_agent_sdk import AgentDefinition
    missing = [f for f in ("effort", "maxTurns") if f not in _fields(AgentDefinition)]
    if missing:
        emit("status", message=f"This Agent SDK version can't set {' or '.join(missing)} per agent, so the "
                               "specialists keep the Virtual Biotech's own settings (slower and costlier).")
    sid, t0, limit = str(uuid.uuid4()), time.monotonic(), float(cfg.get("time_limit_min", 15)) * 60
    wrap_s = float(cfg.get("wrapup_min", 4)) * 60
    state = {"run_dir": None, "stopped": False, "stop_reason": None, "stopped_at": None, "forced": False,
             "phase": "research", "deadline": t0 + limit, "research_stop": None, "wrapped_up": False}

    # Ctrl-C / SIGINT from the notebook: stop the team cleanly and save the run, like the time limit.
    def request_stop():
        if state["stop_reason"] is None:
            state["stop_reason"] = "user"
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, request_stop)
    except (NotImplementedError, RuntimeError):
        pass

    async def stop_run(s):
        """Interrupt first, so the turn ends normally and still reports its cost; force it only if that fails."""
        client = getattr(s, "client", None)
        if client is not None and hasattr(client, "interrupt"):
            try:
                await asyncio.wait_for(client.interrupt(), 30)
                return
            except Exception as e:
                emit("status", message=f"Interrupt request failed ({type(e).__name__}); forcing the turn to end.")
        state["forced"] = True
        s.abort_query()

    async def watch():
        last_progress = time.monotonic()
        while True:
            await asyncio.sleep(3)
            s = app.session_manager.sessions.get(sid)
            if s is None:
                continue
            if state["run_dir"] is None and getattr(s, "run", None) is not None:
                state["run_dir"] = str(s.run.run_dir)
                emit("run_dir", path=state["run_dir"])
            elapsed = time.monotonic() - t0
            if time.monotonic() > state["deadline"] and state["stop_reason"] is None:
                state["stop_reason"] = "time_limit"
            due = time.monotonic() - last_progress >= cfg.get("progress_every_s", 20)
            if state["run_dir"] is not None and (due or (state["stop_reason"] and not state["stopped"])):
                last_progress = time.monotonic()
                events = list(s.trace_logger.events)
                started = Counter(e.get("agent_type") for e in events if e.get("type") == "agent_start")
                ended = Counter(e.get("agent_type") for e in events if e.get("type") == "agent_stop")
                calls = [e for e in events if e.get("type") in ("tool_end", "tool_error")]
                failed = [e for e in calls if e.get("type") == "tool_error" or e.get("is_error")]
                emit("progress", elapsed_s=round(elapsed), running=sorted((started - ended).keys()),
                     finished=sorted(ended.keys()), tool_calls=len(calls), failed_calls=len(failed),
                     phase=state["phase"])
            if state["stop_reason"] and not state["stopped"]:
                state["stopped"], state["stopped_at"] = True, time.monotonic()
                if state["stop_reason"] == "time_limit" and state["phase"] == "research":
                    emit("time_limit", message=f"Time limit of {cfg.get('time_limit_min', 15)} min reached; stopping "
                                               "the team's research. The CSO then gets a few minutes to write up "
                                               "what is already on record.")
                elif state["stop_reason"] == "time_limit":
                    emit("time_limit", message="The write-up ran out of time too; stopping. The run is saved as "
                                               "interrupted, with everything done so far.")
                else:
                    emit("stopping", message="Stopping the run as asked. It is saved as interrupted, with "
                                             "everything done so far.")
                await stop_run(s)
            elif state["stopped"] and not state["forced"] and time.monotonic() - state["stopped_at"] > 90:
                emit("status", message="The run is still going after the interrupt; forcing it to end "
                                       "(its total cost may not be recorded).")
                state["forced"] = True
                s.abort_query()

    async def one_turn(text, history):
        """One CSO turn in this session (mirrors run_vbt._run_turns, without closing the session after it)."""
        previous = app.session_manager.sessions.get(sid)
        before = len(previous.turns) if previous else 0
        last = None
        async for result in app.async_process_message(text, history, sid, model_key=cfg["model"]):
            last = result
        history = last[0] if last else history
        s = app.session_manager.sessions.get(sid)
        if s is not None and len(s.turns) > before and history:
            history = history[:-1] + [{"role": "assistant", "content": s.turns[-1]["response"]}]
        return history

    async def conversation():
        try:
            history = await one_turn(query, [])
            s = app.session_manager.sessions.get(sid)
            if (state["stop_reason"] == "time_limit" and wrap_s > 0 and s is not None
                    and getattr(s, "run", None) is not None):
                # The research was cut off. Give the CSO a short, separate turn to write up what is on record,
                # so the run still ends with a report and checkable claims.
                state.update(phase="wrap-up", research_stop="time_limit", stop_reason=None, stopped=False,
                             stopped_at=None, forced=False, deadline=time.monotonic() + wrap_s)
                emit("wrapup", message=f"Writing up: the CSO has {wrap_s / 60:.0f} min to write the report and "
                                       "file claims from the work already on record (no new analyses).")
                await one_turn(WRAPUP_PROMPT.format(minutes=round(wrap_s / 60)), history)
                state["wrapped_up"] = state["stop_reason"] is None
        finally:
            s = app.session_manager.sessions.get(sid)
            if s is not None:
                try:
                    await s.cleanup()
                except Exception as e:
                    emit("status", message=f"Cleanup warning: {type(e).__name__}")

    watcher = asyncio.create_task(watch())
    task = asyncio.create_task(conversation())
    try:
        await asyncio.wait_for(asyncio.shield(task), limit + wrap_s + 360)
    except asyncio.TimeoutError:
        emit("status", message="The run didn't stop after the time limit, so it was cancelled.")
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    except Exception as e:
        emit("error", message=f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        watcher.cancel()
    s = app.session_manager.sessions.get(sid)
    run_dir = state["run_dir"] or (str(s.run.run_dir) if s is not None and getattr(s, "run", None) else None)
    by_time = state["research_stop"] == "time_limit" or state["stop_reason"] == "time_limit"
    emit("result", elapsed_s=round(time.monotonic() - t0), stopped_by_time_limit=by_time,
         stopped_by_user=state["stop_reason"] == "user", wrapped_up=state["wrapped_up"], **_summarise(run_dir))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["plan", "run"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--query-file")
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    if args.action == "plan":
        cmd_plan(cfg)
        return
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        emit("error", message="No Anthropic key in this process. Run Step 0 of the self-test notebook.")
        sys.exit(2)
    query = Path(args.query_file).read_text()
    try:
        asyncio.run(cmd_run(cfg, query))
    except KeyboardInterrupt:
        emit("error", message="Stopped from the notebook.")
        sys.exit(130)


if __name__ == "__main__":
    main()
