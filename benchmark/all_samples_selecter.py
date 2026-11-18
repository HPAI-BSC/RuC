import os
import json
import sys
import threading
import numpy as np
from antlr4 import ParseTreeWalker
from concurrent.futures import ProcessPoolExecutor
import multiprocessing


from configs.rules import RULES
from .project_manifest import MANIFEST_NAME, read_manifest
from .utils import (
    count_nested_rules,
    create_parser,
    round_2,
    extend_stop_to_line_end,
    find_hdl_files,
)


PARSE_RECURSION_LIMIT = 20000
PARSE_STACK_BYTES = 64 * 1024 * 1024


def run_with_deep_stack(function, *args):
    """
    Call `function` on a thread with a large stack and a raised recursion limit.

    The recursion limit is per interpreter while the stack is per thread, so the limit is
    raised here and restored as soon as the parse is done.

    Whatever the call raises is re-raised here, so callers still see a RecursionError they
    can handle per source instead of losing the worker process.
    """
    outcome = {}

    def target():
        try:
            outcome["value"] = function(*args)
        except BaseException as error:
            outcome["error"] = error

    try:
        previous_size = threading.stack_size(PARSE_STACK_BYTES)
    except (ValueError, RuntimeError):
        # The platform refused the size, so fall back to parsing on this thread. A deeply
        # nested source then still raises, and is reported per source rather than crashing.
        return function(*args)

    previous_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(PARSE_RECURSION_LIMIT)

    try:
        worker = threading.Thread(target=target)
        worker.start()
        worker.join()
    finally:
        sys.setrecursionlimit(previous_limit)
        threading.stack_size(previous_size)

    if "error" in outcome:
        raise outcome["error"]

    return outcome["value"]


def make_rule_listener(parser_cls, listener_base):
    class RuleListener(listener_base):
        def __init__(self, text, stats, lines):
            super().__init__()
            self.text = text
            self.stats = stats
            self.lines = lines

        def enterEveryRule(self, ctx):
            rule = parser_cls.ruleNames[ctx.getRuleIndex()]

            if rule not in RULES:
                return

            s = self.stats[rule]
            s["count"] += 1

            length = ctx.stop.line - ctx.start.line + 1
            s["len"].append(length)
            s["nest"].append(count_nested_rules(ctx))

            start = ctx.start.start
            stop = extend_stop_to_line_end(self.text, ctx.stop.stop)

            entry = [start, stop]
            self.lines[rule].append(entry)

    return RuleListener


def init_stats():
    return {
        r: {"count": 0, "len": [], "nest": []}
        for r in RULES
    }


def compute_summary(stats):
    summary = {}

    for rule, s in stats.items():
        summary[rule] = {
            "count": s["count"],
            "avg_length": round_2(np.mean(s["len"])) if s["len"] else 0,
            "med_length": round_2(np.median(s["len"])) if s["len"] else 0,
            "std_length": round_2(np.std(s["len"])) if s["len"] else 0,
            "avg_nest": round_2(np.mean(s["nest"])) if s["nest"] else 0,
            "med_nest": round_2(np.median(s["nest"])) if s["nest"] else 0,
            "std_nest": round_2(np.std(s["nest"])) if s["nest"] else 0,
        }

    return summary


def get_grammar(hdl):
    """ Resolve the ANTLR lexer, parser, listener and root rule of an HDL """
    if hdl in ["v", "sv"]:
        from .antlr.SystemVerilog.SystemVerilogLexer import SystemVerilogLexer
        from .antlr.SystemVerilog.SystemVerilogParser import SystemVerilogParser
        from .antlr.SystemVerilog.SystemVerilogListener import SystemVerilogListener

        return SystemVerilogLexer, SystemVerilogParser, SystemVerilogListener, "source_text"

    elif hdl == "vhd":
        from .antlr.VHDL.vhdlLexer import vhdlLexer
        from .antlr.VHDL.vhdlParser import vhdlParser
        from .antlr.VHDL.vhdlListener import vhdlListener

        return vhdlLexer, vhdlParser, vhdlListener, "design_file"

    return None


def parse_occurrences(text, hdl, stats):
    """
    Walk the parse tree of one source and collect the span of every selected rule.

    Returns a dict of rule name to a list of [start, stop] character offsets, and fills
    `stats` in place. Shared by the single-file and the multi-file flows so both find
    exactly the same occurrences.
    """
    return run_with_deep_stack(_parse_occurrences, text, hdl, stats)


def _parse_occurrences(text, hdl, stats):
    """ The body of `parse_occurrences`, run on the deep stack it needs """
    grammar = get_grammar(hdl)

    if grammar is None:
        return None

    lexer_cls, parser_cls, listener_base, root_rule = grammar

    RuleListener = make_rule_listener(parser_cls, listener_base)

    lines = {r: [] for r in RULES}

    parser, _ = create_parser(text, lexer_cls, parser_cls)

    tree = getattr(parser, root_rule)()

    listener = RuleListener(text, stats, lines)

    ParseTreeWalker().walk(listener, tree)

    return lines


def read_text(file_path, debug):
    try:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        if debug:
            print(f"[ERROR] Skipping {file_path}: {e}")
        return None


def process_single_file(args):
    file_path, mask_path, hdl, debug = args

    local_stats = init_stats()

    if get_grammar(hdl) is None:
        return None

    text = read_text(file_path, debug)

    if text is None:
        return None

    if debug:
        print(f"[INFO] Processing {file_path}")

    try:
        lines = parse_occurrences(text, hdl, local_stats)
    except Exception as e:
        print(f"[ERROR] Could not parse {file_path}, no occurrences collected from it: {e!r}")
        return None

    with open(mask_path, "w") as f:
        json.dump(lines, f)

    return local_stats


def process_single_project(args):
    """
    Collect the rule occurrences of every source of a multi-file project.

    Occurrences are stored as {"file": <relative source>, "span": [start, stop]} so that
    a span can still be resolved once the sources are no longer a single file. The
    result goes to the same `all_mask_idx.json` the single-file flow writes.
    """
    project_dir, mask_path, hdl, debug = args

    local_stats = init_stats()

    if get_grammar(hdl) is None:
        return None

    manifest = read_manifest(project_dir)

    if manifest is None:
        return None

    lines = {r: [] for r in RULES}
    failed_sources = []

    for source in manifest["sources"]:
        text = read_text(os.path.join(project_dir, source), debug)

        if text is None:
            continue

        if debug:
            print(f"[INFO] Processing {os.path.join(project_dir, source)}")

        try:
            source_lines = parse_occurrences(text, hdl, local_stats)
        except Exception as e:
            print(f"[ERROR] {os.path.basename(project_dir)}/{source}: not parsed, no "
                  f"occurrences collected from it: {e!r}")
            failed_sources.append(source)
            continue

        for rule, spans in source_lines.items():
            for span in spans:
                lines[rule].append({"file": source, "span": span})

    if failed_sources:
        print(
            f"[WARNING] {os.path.basename(project_dir)}: {len(failed_sources)} of "
            f"{len(manifest['sources'])} sources were not parsed, so no hole can land in "
            f"them: {failed_sources}"
        )

    with open(mask_path, "w") as f:
        json.dump(lines, f)

    return local_stats


def merge_stats(global_stats, local_stats):
    for rule in RULES:
        global_stats[rule]["count"] += local_stats[rule]["count"]
        global_stats[rule]["len"].extend(local_stats[rule]["len"])
        global_stats[rule]["nest"].extend(local_stats[rule]["nest"])


def process_dataset(root_dir, hdl, debug):

    stats = init_stats()

    tasks = []

    for subdir, _, files in os.walk(root_dir):

        modules = find_hdl_files(files, hdl)

        if not modules:
            continue

        if debug and len(modules) > 1:
            print(
                f"[WARNING] multiple HDL files in {subdir}, "
                f"picking first: {modules[0]}"
            )

        file_path = os.path.join(subdir, modules[0])
        mask_path = os.path.join(subdir, "all_mask_idx.json")

        tasks.append(
            (
                file_path,
                mask_path,
                hdl,
                debug
            )
        )

    workers = min(multiprocessing.cpu_count(), len(tasks), 60)

    print(f"[INFO] Processing {len(tasks)} files using {workers} workers")

    with ProcessPoolExecutor(max_workers=workers) as executor:

        for result in executor.map(process_single_file, tasks):

            if result is None:
                continue

            merge_stats(stats, result)

    return compute_summary(stats)


def process_agentic_dataset(root_dir, hdl, debug):
    """ Same as `process_dataset`, over the sources listed in each project manifest """

    stats = init_stats()

    tasks = []

    for project in sorted(os.listdir(root_dir)):

        project_dir = os.path.join(root_dir, project)

        if not os.path.isdir(project_dir):
            continue

        if read_manifest(project_dir) is None:
            if debug:
                print(f"[WARNING] No {MANIFEST_NAME} in {project_dir}, skipping")
            continue

        tasks.append(
            (
                project_dir,
                os.path.join(project_dir, "all_mask_idx.json"),
                hdl,
                debug
            )
        )

    if not tasks:
        print(f"[WARNING] No project with a {MANIFEST_NAME} found in {root_dir}")
        return compute_summary(stats)

    workers = min(multiprocessing.cpu_count(), len(tasks), 60)

    print(f"[INFO] Processing {len(tasks)} projects using {workers} workers")

    with ProcessPoolExecutor(max_workers=workers) as executor:

        for result in executor.map(process_single_project, tasks):

            if result is None:
                continue

            merge_stats(stats, result)

    return compute_summary(stats)
