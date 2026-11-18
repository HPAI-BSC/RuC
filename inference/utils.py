import json
import os
import re
from configs.prompts import SYSTEM_PROMPT, FIM_TEMPLATE


def build_conversation(raw_prompt):
    """ The system and user turns a chat model is given for one masked task """

    inst_prompt = f"""
            Fill in the missing SystemVerilog code marked by <MASK> in the following project:
            {raw_prompt}
            """

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": inst_prompt}
    ]


def build_model_prompt(raw_prompt, tokenizer, args):
    """ Create the actual prompt to give to the model (only changes for the chat template version) """

    if args.prompt_mode == "fim":
        return raw_prompt

    elif args.prompt_mode == "chat":
        instruction = (SYSTEM_PROMPT)

        if tokenizer.chat_template:
            prompt = tokenizer.apply_chat_template(
                build_conversation(raw_prompt),
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            if args.debug: print("[WARNING] No chat template available")
            prompt = instruction + "\n\n" + raw_prompt
    
        return prompt



def apply_mask(text, line, args):
    """ Mask lines from start to stop in the text"""
    start_mask, stop_mask = line

    #masked_text = text[max(0, int(prev_context)):start_mask] + "<MASK>" + text[stop_mask:(min(len(text), int(foll_context)))]
    if args.debug:
        print("[INFO]\n\nRemoved Text")
        print(f"       {text[start_mask:stop_mask]}")

    
    prefix = text[0:start_mask]
    suffix = text[stop_mask:len(text)]

    if args.prompt_mode == "fim":

        raw_prompt = FIM_TEMPLATE.format(prefix=prefix, suffix=suffix)
        

    elif args.prompt_mode == "chat":
        raw_prompt = prefix + "<MASK>" + suffix

    return raw_prompt


def strip_markdown_fences(raw_generation):
    """Cleans a generated chat output by removing Markdown code fences and language tags."""
    
    # Match proper fenced blocks
    pattern = re.compile(r"```(?:\w+)?\s*([\s\S]+?)\s*```")
    match = pattern.search(raw_generation)

    if match:
        generation = match.group(1)
    else:
        generation = raw_generation.strip()

        # Remove leading ```
        generation = re.sub(r"^```(?:\w+)?\s*", "", generation)

        # Remove trailing ```
        generation = re.sub(r"\s*```$", "", generation)

    return generation.strip()



def extract_generation_from_reasoning(generation):
        # For reasoning models, we keep only the final answer
        if "assistantfinal" in generation:  # gpt-oss
            delimiter = "assistantfinal"
            reasoning, generation = generation.rsplit(delimiter, 1)
            reasoning = reasoning.strip()
        elif "</seed:think>" in generation:  # seed-oss-36b
            delimiter = "</seed:think>"
            reasoning, generation = generation.rsplit(delimiter, 1)
            reasoning = reasoning.strip()
        elif "</think>" in generation:
            delimiter = "</think>"
            reasoning, generation = generation.rsplit(delimiter, 1)
            reasoning = reasoning.strip()
        else:
            reasoning = None
        return reasoning, generation


HOLE_COMMENT = {"v": "//", "sv": "//", "vhd": "--"}


def extract_hole_generation(text, hole_id, hdl):
    """
    Pull back what an agent wrote between the two markers of one hole.

    Returns (generation, markers_intact). An agent that deleted or rewrote its markers
    leaves nothing to extract, and that is reported rather than guessed at, so the hole
    can be counted as failed instead of silently scored against empty code.
    """
    comment = re.escape(HOLE_COMMENT.get(hdl, "//"))
    hole = re.escape(hole_id)

    pattern = re.compile(
        rf"{comment}\s*RUC_HOLE_BEGIN\s+id={hole}\b[^\n]*\n"
        rf"(.*?)"
        rf"^[ \t]*{comment}\s*RUC_HOLE_END\s+id={hole}\b[^\n]*$",
        re.DOTALL | re.MULTILINE,
    )

    match = pattern.search(text)

    if not match:
        return "", False

    generation = match.group(1)

    return generation.strip("\n"), True


def align_generation(generation, text, line):
    """
    Make an extracted block splice back exactly where the original code was.

    A rule occurrence spans from its first token to the end of its last line, so the
    text before it already ends with that line's indentation and the text after it still
    begins with the newline. The block pulled out of the workspace carries the
    indentation the marker block was written with, so the first line has to give it back
    or every generation would be spliced one indent too deep and one newline too short.
    """
    generation = generation.strip("\n")

    if not generation:
        return ""

    start_mask, _ = line

    line_begin = text.rfind("\n", 0, start_mask) + 1
    indent = text[line_begin:start_mask]

    if not indent:
        return generation

    # The text before the hole already supplies whatever precedes it on that line, so
    # the first line always gives its indentation back. An occurrence that starts mid
    # line, such as a port inside a port list, was emitted with a blank indent as wide
    # as the code before it, which does not match that code but is just as unwanted.
    first, separator, rest = generation.partition("\n")

    if first.startswith(indent):
        first = first[len(indent):]
    else:
        first = first.lstrip(" \t")

    return first + separator + rest


def insert_generation_into_code(text, line, generation):
    start_mask, stop_mask = line
    filled_code = text[:start_mask] + generation + text[stop_mask:]
    
    return filled_code


# Finish reason recorded for the tasks of a batch whose request failed. They got no answer,
# so a relaunch tries them again.
GENERATION_FAILED = "error"


def task_dir(output_path, rule, base_name, idx):
    """ Folder of one task: <output_path>/<rule>/<project>-task<idx> """
    return os.path.join(output_path, rule, f"{base_name}-task{idx}")


def save_generation(output_path, rule, base_name, idx, hdl, generation, filled_code,
                    span, finish_reason, reasoning=None, raw_output=None, sample=1):
    """
    Write one generation where the evaluation reads it, next to what the model returned:
    <output_path>/<rule>/<project>-task<idx>/
        gen<sample>.<hdl>        the completion
        full_gen<sample>.<hdl>   the source rebuilt with it
        gen<sample>.json         the span it fills, the finish reason, the reasoning, and the
                                 raw output (the error, when the request failed)

    The json goes last, so a task only counts as generated once all three files are there.
    """
    save_dir = task_dir(output_path, rule, base_name, idx)
    os.makedirs(save_dir, exist_ok=True)

    with open(os.path.join(save_dir, f"gen{sample}.{hdl}"), "w") as out_gen:
        out_gen.write(generation)

    with open(os.path.join(save_dir, f"full_gen{sample}.{hdl}"), "w") as out_full_gen:
        out_full_gen.write(filled_code)

    with open(os.path.join(save_dir, f"gen{sample}.json"), "w") as out_meta:
        json.dump({
            "span": list(span),
            "finish_reason": finish_reason,
            "reasoning": reasoning,
            "raw_output": raw_output,
        }, out_meta, indent=4)

    return save_dir


def is_generated(output_path, rule, base_name, idx, span, sample=1):
    """
    Whether an earlier run already answered this task, so a relaunch can skip it.
    """
    meta_path = os.path.join(task_dir(output_path, rule, base_name, idx), f"gen{sample}.json")

    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return False

    return meta.get("finish_reason") != GENERATION_FAILED and meta.get("span") == list(span)


def find_hdl_files(files, hdl):
    return [f for f in files if f.endswith("." + hdl)]


def print_inference_stats(stats):
    """Prints a brief summary of the inference performance."""
    if not stats["input_tokens"] and not stats["output_tokens"] and not stats["batch_times"]:
        print("\n[WARNING] No successful inference data to report.")
        print(f"[WARNING] Total failures: {stats['failures']}")
        return

    def get_metrics(data):
        if not data:
            return 0, 0, 0
        return sum(data) / len(data), max(data), min(data)

    avg_in, max_in, min_in = get_metrics(stats["input_tokens"])
    avg_out, max_out, min_out = get_metrics(stats["output_tokens"])
    avg_time, max_time, min_time = get_metrics(stats["batch_times"])

    print("\n" + "="*30)
    print("[INFO] Inference Summary")
    print("="*30)
    print(f"Input Tokens:  Avg: {avg_in:.2f}, Max: {max_in}, Min: {min_in}")
    print(f"Output Tokens: Avg: {avg_out:.2f}, Max: {max_out}, Min: {min_out}")
    print(f"Batch Time:    Avg: {avg_time:.4f}s, Max: {max_time:.4f}s, Min: {min_time:.4f}s")
    print(f"Failures:      {stats['failures']}")
    print("="*30 + "\n")

