import os
import json
import numpy as np
from antlr4 import ParseTreeWalker

from .antlr.SystemVerilogParser import SystemVerilogParser
from .antlr.SystemVerilogParserListener import SystemVerilogParserListener

from configs.rules import RULES
from .utils import (
    count_nested_rules,
    get_token_count,
    fresh_parser,
    r2,
    extend_stop_to_line_end,
    find_hdl_files,
)



class RuleListener(SystemVerilogParserListener):
    def __init__(self, tokenizer, text, stats, lines):
        super().__init__()
        self.tokenizer = tokenizer
        self.text = text

        self.stats = stats
        self.lines = lines

    def enterEveryRule(self, ctx):
        rule = SystemVerilogParser.ruleNames[ctx.getRuleIndex()]
        if rule not in RULES:
            return

        s = self.stats[rule]

        # Stats
        s["count"] += 1

        length = ctx.stop.line - ctx.start.line + 1
        s["len"].append(length)

        text = ctx.start.getInputStream().getText(ctx.start.start, ctx.stop.stop)
        s["tok"].append(get_token_count(self.tokenizer, text))

        s["nest"].append(count_nested_rules(ctx))

        # Mask extraction
        start = ctx.start.start
        stop = ctx.stop.stop
        stop = extend_stop_to_line_end(self.text, ctx.stop.stop) # In the paper we extend to line end
        

        entry = [start, stop]

        if entry not in self.lines[rule]:
            self.lines[rule].append(entry)


def init_stats():
    return {
        r: {"count": 0, "len": [], "tok": [], "nest": []}
        for r in RULES
    }


def compute_summary(stats):
    summary = {}

    for rule, s in stats.items():
        summary[rule] = {
            "count": s["count"],
            "avg_length": r2(np.mean(s["len"])) if s["len"] else 0,
            "med_length": r2(np.median(s["len"])) if s["len"] else 0,
            "std_length": r2(np.std(s["len"])) if s["len"] else 0,
            "avg_tok": r2(np.mean(s["tok"])) if s["tok"] else 0,
            "med_tok": r2(np.median(s["tok"])) if s["tok"] else 0,
            "std_tok": r2(np.std(s["tok"])) if s["tok"] else 0,
            "avg_nest": r2(np.mean(s["nest"])) if s["nest"] else 0,
            "med_nest": r2(np.median(s["nest"])) if s["nest"] else 0,
            "std_nest": r2(np.std(s["nest"])) if s["nest"] else 0,
        }

    return summary


def process_dataset(root_dir, tokenizer, hdl, debug):
    stats = init_stats()

    for subdir, _, files in os.walk(root_dir):
        # modules is the name of the top module in the file with ".sv" or ".v"
        modules = find_hdl_files(files, hdl)

        if not modules:
            continue

        if len(modules) > 1:
            print(f"Warning: multiple HDL files in {subdir}, picking first: {modules[0]}")

        file_path = os.path.join(subdir, modules[0])

        try:
            with open(file_path, encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print(f"Skipping {file_path}: {e}")
            continue
        
        if debug:
            print(f"Processing {subdir}")

        mask_path = os.path.join(subdir, "all_mask_idx.json")

        # if os.path.exists(mask_path):
        #     with open(mask_path) as f:
        #         lines = json.load(f)
        # else:
        lines = {r: [] for r in RULES}

        parser, _ = fresh_parser(text)
        tree = parser.source_text()

        listener = RuleListener(tokenizer, text, stats, lines)
        ParseTreeWalker().walk(listener, tree)

        with open(mask_path, "w") as f:
            json.dump(lines, f)

    return compute_summary(stats)
