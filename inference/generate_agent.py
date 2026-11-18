import os
import re
import json
import shutil
import subprocess

from configs.agent_prompts import EXPORT_SUBDIR
from .parser import parse_args_agent
from .utils import align_generation, extract_hole_generation, insert_generation_into_code


"""
Run Harbor over the emitted tasks and collect what the agent wrote.

Harbor is invoked once for the whole task folder, so its own `-n` concurrency schedules
the bundles. It boots one shared Singularity image for all of them.

    stage 1  the emitted tasks are copied into a run folder, without `solution/`
    stage 2 Harbor runs the agent over that folder, talking to the model served at
             --ip/--port
    stage 3  each hole is pulled out of the exported workspace and spliced into the
             *golden* source on its own: every other source, including the ones holding
             the other holes of the same bundle, stays golden

That isolated file is the same artefact the rule completion flow produces, which is why
one broken hole cannot fail the hole next to it.
"""


RUN_DIRNAME = "tasks"
JOBS_DIRNAME = "harbor"
JOB_NAME = "run"

# So that the container kills the agent before Harbor gives up waiting for it.
AGENT_TIMEOUT_SLACK_S = 120
CONTAINER_ENV_PREFIXES = ("SINGULARITY_", "APPTAINER_")
CONTAINER_ENV_KEEP = ("CACHEDIR", "TMPDIR")


def execute_command(cmd, timeout, env=None, debug=False):
    """ Run the harness, mirroring the subprocess handling of the evaluation step """
    if debug:
        print(f"[INFO] Running: {' '.join(cmd)}")

    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        ), None

    except FileNotFoundError:
        return None, f"{cmd[0]} not found"

    except subprocess.TimeoutExpired:
        return None, f"{cmd[0]} timed out after {timeout}s"


def read_task(task_dir):
    """ The hole descriptions written next to a task, or None when it is not a task """
    holes_path = os.path.join(task_dir, "solution", "holes.json")

    if not os.path.exists(holes_path):
        return None

    with open(holes_path) as f:
        return json.load(f)


def find_tasks(tasks_path):
    """ Every emitted task folder under `tasks_path` """
    if not os.path.isdir(tasks_path):
        return []

    return [
        os.path.join(tasks_path, name)
        for name in sorted(os.listdir(tasks_path))
        if read_task(os.path.join(tasks_path, name)) is not None
    ]


def read_agent_timeout_s(task_dir):
    """
    The `[agent] timeout_sec` a task was emitted with, or None when it names none.
    """
    path = os.path.join(task_dir, "task.toml")

    if not os.path.exists(path):
        return None

    section = None

    with open(path) as f:
        for line in f:
            line = line.strip()

            if line.startswith("["):
                section = line.strip("[]")
                continue

            timeout = re.fullmatch(r"timeout_sec\s*=\s*(\d+)", line)
            if timeout and section == "agent":
                return int(timeout.group(1))

    return None


def agent_deadline_s(tasks):
    """
    The deadline the agent process itself is given inside the container.
    
    Agent that execs without a budget is bounded by the environment's own default (600s).
    Passing this as `--ak agent_timeout_sec` closes that gap.

    Harbor's clock starts first so on equal numbers the outer one always wins, and it cancels 
    rather than kills, which leaves the CLI running inside the container.
    """
    emitted = [t for t in (read_agent_timeout_s(task) for task in tasks) if t]

    if not emitted:
        return None

    deadline = min(emitted) - AGENT_TIMEOUT_SLACK_S

    return deadline if deadline > 0 else None


def prepare_run(tasks, run_path, debug=False):
    """
    Copy the emitted tasks into the folder Harbor is pointed at.
    """
    # `run_path` is derived from --output_path. The run folder is ours to recreate every 
    # time; the emitted benchmark is not.
    run_parent = os.path.dirname(os.path.abspath(run_path))
    tasks_parent = os.path.dirname(os.path.abspath(tasks[0]))

    if os.path.abspath(run_path) == tasks_parent or run_parent == tasks_parent:
        raise SystemExit(
            f"[ERROR] Refusing to clear {run_path}: it holds the emitted benchmark. "
            f"--output_path must not be the folder --tasks_path lives in."
        )

    shutil.rmtree(run_path, ignore_errors=True)
    os.makedirs(run_path, exist_ok=True)

    for task_dir in tasks:
        task_name = os.path.basename(task_dir)
        destination = os.path.join(run_path, task_name)

        shutil.copytree(
            task_dir, destination, ignore=shutil.ignore_patterns("solution")
        )

        test_path = os.path.join(destination, "tests", "test.sh")

        if not os.path.exists(test_path):
            print(f"[WARNING] {task_name}: no tests/test.sh, nothing will be exported")
            continue

        # copytree preserves the mode, but the emitted tree may have come off a filesystem
        # that does not, and Harbor runs this script directly
        os.chmod(test_path, 0o755)

        if debug:
            print(f"[INFO] Prepared {destination}")

    return run_path


def trial_finished(trial_dir):
    """
    Whether a trial ran to the end, its verifier included.

    Harbor records only the first exception of a trial, so one whose agent timed out and
    that was then cancelled while verifying reads as a plain agent timeout. Its verifier
    result tells the two apart: it only exists once tests/test.sh finished, which is also
    what exported the workspace stage 3 collects.
    """
    try:
        with open(os.path.join(trial_dir, "result.json")) as f:
            result = json.load(f)
    except (OSError, ValueError):
        return False

    exception = result.get("exception_info") or {}

    return bool(result.get("verifier_result")) and exception.get("exception_type") != "CancelledError"


def prepare_jobs_dir(jobs_dir, concurrency, debug=False):
    """
    Get the job folder the harness is about to write ready, and hand its path back.

    Harbor resumes a job folder it finds, and does not run a trial again once it has a
    result.json, so a run that outlives its SLURM job carries on when launched again. It
    would also keep the trials cut short when that job was killed, recorded as cancelled
    or half verified, so those are deleted here to be run again. Delete the folder to start
    a run over.

    """
    job_dir = os.path.join(jobs_dir, JOB_NAME)
    os.makedirs(jobs_dir, exist_ok=True)

    if os.path.isdir(job_dir):
        trials = [
            os.path.join(job_dir, name)
            for name in os.listdir(job_dir)
            if os.path.isdir(os.path.join(job_dir, name))
        ]
        cut_short = [trial for trial in trials if not trial_finished(trial)]

        for trial in cut_short:
            shutil.rmtree(trial, ignore_errors=True)

        for name in ("config.json", "lock.json"):
            path = os.path.join(job_dir, name)

            if os.path.exists(path):
                with open(path) as f:
                    saved = json.load(f)

                saved["n_concurrent_trials"] = concurrency

                with open(path, "w") as f:
                    json.dump(saved, f, indent=4)

        print(
            f"[INFO] Resuming the Harbor job at {job_dir}: {len(trials) - len(cut_short)} "
            f"trials already finished, {len(cut_short)} cut short are run again. Delete it "
            f"to start over"
        )

    if debug:
        print(f"[INFO] Harbor will write its job to {job_dir}")

    return job_dir


def build_harbor_command(run_path, jobs_dir, tasks, args):
    """
    The Harbor invocation for the whole run.
    """
    if args.provider:
        # Harbor selects its provider config from the prefix of the model argument
        model_arg = f"{args.provider}/{args.model}"
    else:
        model_arg = args.model_name or args.model

    command = [
        args.harbor_python,
        "-m", args.harness,
        "run",
        "-p", run_path,
        "-a", args.agent,
        "-m", model_arg,
        "-n", str(args.concurrency),
        "-e", args.executor,
        "-o", jobs_dir,
        "--job-name", JOB_NAME,
    ]

    deadline_s = agent_deadline_s(tasks)

    if deadline_s:
        # Harbor takes the agent phase's budget from task.toml, but has no flag that hands
        # it down to the exec that runs the agent. This is an agent kwarg rather than a job 
        # flag for that reason.
        command += ["--ak", f"agent_timeout_sec={deadline_s}"]

    if args.context_limit and args.output_limit:
        # OpenCode registers the served model with no capability metadata by default
        # and falls back to a flat max_tokens guess that ignores how full the context
        # already is; this hands it the real ceiling instead. Its config schema
        # requires both fields together, so only pass these if both are set.
        command += ["--ak", f"context_limit={args.context_limit}"]
        command += ["--ak", f"output_limit={args.output_limit}"]

    if args.executor == "singularity":
        # Many clusters delegate no /etc/subuid range, and singularity aborts the container
        # rather than degrading when --fakeroot cannot map the caller. The image
        # ships everything the run needs, so nothing is installed at run time and
        # root inside the container buys nothing.
        command += ["--ek", "singularity_fakeroot=false"]

    return command


def strip_container_env(env):
    """
    Drop the singularity configuration we did not put there ourselves.

    SINGULARITYENV_*/APPTAINERENV_* are deliberately left alone: they inject environment
    into the container rather than mounting anything, and they do not match the prefix.
    """
    keep = tuple(
        f"{prefix}{name}" for prefix in CONTAINER_ENV_PREFIXES for name in CONTAINER_ENV_KEEP
    )

    return {
        key: value
        for key, value in env.items()
        if not key.startswith(CONTAINER_ENV_PREFIXES) or key in keep
    }


def build_harbor_env(args):
    """
    The environment Harbor and the agent inside it read the served model from.
    """
    env = strip_container_env(dict(os.environ))

    if args.ip and args.port:
        env["OPENAI_BASE_URL"] = f"http://{args.ip}:{args.port}/v1"
        env["OPENAI_API_KEY"] = env.get("OPENAI_API_KEY", "EMPTY")
        env["OPENAI_STREAM"] = "false"
        env["OPENAI_USE_RESPONSES_API"] = "false"
        env["OPENAI_DISABLE_RESPONSES_API"] = "1"

    if args.harbor_src:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{args.harbor_src}:{existing}" if existing else args.harbor_src

    return env


def run_harbor(run_path, jobs_dir, tasks, args):
    """ Hand the whole run to the harness, record how it went, and whether it exited cleanly """
    command = build_harbor_command(run_path, jobs_dir, tasks, args)
    env = build_harbor_env(args)

    # One full trial budget per task, plus room for the tasks that Harbor queues behind.
    # A harness that enforces its own budget finishes first. The budget the tasks were
    # emitted with wins over --agent_timeout_s: a shorter one here would kill Harbor
    # before any agent could time out on its own.
    emitted = [t for t in (read_agent_timeout_s(task) for task in tasks) if t]
    agent_budget_s = max(emitted) if emitted else args.agent_timeout_s

    if agent_budget_s != args.agent_timeout_s:
        print(
            f"[WARNING] Tasks were emitted with an agent budget of {agent_budget_s}s, "
            f"not --agent_timeout_s {args.agent_timeout_s}s: using {agent_budget_s}s"
        )

    batches = -(-len(tasks) // max(args.concurrency, 1))
    trial_budget_s = agent_budget_s + args.verifier_timeout_s
    timeout = trial_budget_s * batches + args.harbor_overhead_s

    print(f"[INFO] Running Harbor over {len(tasks)} tasks, {args.concurrency} at a time")

    result, error_message = execute_command(command, timeout, env, args.debug)

    if error_message:
        print(f"[ERROR] Harbor: {error_message}")
    elif result.returncode != 0:
        print(f"[ERROR] Harbor exited with {result.returncode}")
        if args.debug:
            print(result.stderr)

    os.makedirs(args.output_path, exist_ok=True)

    with open(os.path.join(args.output_path, "harbor_result.json"), "w") as f:
        json.dump(
            {
                "command": command,
                "tasks": len(tasks),
                "returncode": None if result is None else result.returncode,
                "error_message": error_message or "",
            },
            f,
            indent=4,
        )

    if result is not None and result.stdout:
        with open(os.path.join(args.output_path, "harbor_stdout.log"), "w") as f:
            f.write(result.stdout)

    if result is not None and result.stderr:
        with open(os.path.join(args.output_path, "harbor_stderr.log"), "w") as f:
            f.write(result.stderr)

    return result is not None and result.returncode == 0


def collect_holes(task, workspace, task_dir, task_output, task_name, args):
    """
    Extract every hole from the exported workspace and rebuild its isolated source.

    Writes, per hole:
        holes/<id>/gen1.<hdl>       what the agent wrote between the markers
        holes/<id>/full_gen1.<hdl>  the golden source with only this hole replaced
        holes/<id>/hole.json        what the evaluation step needs to score it
    """
    hdl = task["hdl"]
    collected = 0

    # The workspace text is what the agent left, the golden text is what it is scored on
    workspace_texts = {}
    golden_texts = {}

    for hole in task["holes"]:
        source = hole["file"]

        if source not in golden_texts:
            golden_path = os.path.join(task_dir, "solution", "golden", source)
            with open(golden_path, encoding="utf-8") as f:
                golden_texts[source] = f.read()

            workspace_path = os.path.join(workspace, source)

            if os.path.exists(workspace_path):
                with open(workspace_path, encoding="utf-8") as f:
                    workspace_texts[source] = f.read()
            else:
                print(f"[WARNING] {task_name}: {source} missing from the workspace")
                workspace_texts[source] = ""

        generation, markers_intact = extract_hole_generation(
            workspace_texts[source], hole["id"], hdl
        )

        if not markers_intact and args.debug:
            print(f"[WARNING] {task_name}/{hole['id']}: markers were not preserved")

        generation = align_generation(generation, golden_texts[source], hole["span"])

        filled_code = insert_generation_into_code(
            golden_texts[source], hole["span"], generation
        )

        hole_dir = os.path.join(task_output, "holes", hole["id"])
        os.makedirs(hole_dir, exist_ok=True)

        with open(os.path.join(hole_dir, "gen1." + hdl), "w") as out_gen:
            out_gen.write(generation)

        with open(os.path.join(hole_dir, "full_gen1." + hdl), "w") as out_full_gen:
            out_full_gen.write(filled_code)

        with open(os.path.join(hole_dir, "hole.json"), "w") as out_hole:
            json.dump(
                {
                    "project": task["project"],
                    "rule": hole["rule"],
                    "file": source,
                    "span": hole["span"],
                    "index": hole.get("index"),
                    "bundle": task_name,
                    "hole_id": hole["id"],
                    "markers_intact": markers_intact,
                },
                out_hole,
                indent=4,
            )

        collected += 1

    return collected


def find_exported_workspaces(jobs_dir):
    """
    Where each task's workspace came back, keyed by task name.
    """
    job_dir = os.path.join(jobs_dir, JOB_NAME)

    if not os.path.isdir(job_dir):
        print(f"[WARNING] No Harbor job at {job_dir}, nothing was exported")
        return {}

    workspaces = {}

    for trial_name in sorted(os.listdir(job_dir)):
        exported = os.path.join(job_dir, trial_name, "verifier", EXPORT_SUBDIR)

        if not os.path.isdir(exported):
            continue

        for task_name in sorted(os.listdir(exported)):
            workspace = os.path.join(exported, task_name, "workspace")

            if os.path.isdir(workspace):
                workspaces[task_name] = workspace

    return workspaces


def collect_run(tasks, jobs_dir, args):
    """ Rebuild the isolated source of every hole of every task """
    total = 0
    missing = []

    workspaces = find_exported_workspaces(jobs_dir)

    for task_dir in tasks:
        task = read_task(task_dir)
        task_name = os.path.basename(task_dir)

        workspace = workspaces.get(task_name, os.path.join(jobs_dir, JOB_NAME, task_name))
        task_output = os.path.join(args.output_path, task_name)

        if not os.path.isdir(workspace):
            # The bundle counts as failed instead of vanishing from the run.
            missing.append(task_name)

        os.makedirs(task_output, exist_ok=True)

        collected = collect_holes(
            task, workspace, task_dir, task_output, task_name, args
        )

        total += collected

        print(f"[INFO] {task_name}: collected {collected} holes")

    if missing:
        print(
            f"[WARNING] {len(missing)} tasks exported no workspace, their holes were "
            f"collected empty: {missing}"
        )

    print(f"[INFO] Total holes collected: {total}")

    return total


def traverse_tasks(tasks_path, args):
    tasks = find_tasks(tasks_path)

    if not tasks:
        print(f"[WARNING] No emitted task found in {tasks_path}")
        return

    print(f"[INFO] Found {len(tasks)} agent tasks")

    jobs_dir = os.path.abspath(os.path.join(args.output_path, JOBS_DIRNAME))
    run_path = os.path.join(args.output_path, RUN_DIRNAME)

    if args.stage in ("run", "all"):
        prepare_jobs_dir(jobs_dir, args.concurrency, args.debug)
        prepare_run(tasks, run_path, args.debug)

        if not run_harbor(run_path, jobs_dir, tasks, args):
            # Also how Harbor refuses to resume a job whose tasks or settings changed, and
            # the workspaces in it would then be collected against other tasks
            print(
                "[ERROR] Harbor did not finish, so nothing is collected. Launching again "
                "resumes the run, unless Harbor refused to resume it: see harbor_stderr.log"
            )
            return

    if args.stage in ("collect", "all"):
        collect_run(tasks, jobs_dir, args)


if __name__ == "__main__":
    args = parse_args_agent()
    print("[INFO] Args parsed:")
    for arg, value in vars(args).items():
        print(f"[INFO]  {arg}: {value}")

    traverse_tasks(args.tasks_path, args)
