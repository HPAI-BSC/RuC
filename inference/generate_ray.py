import os
import json
from .parser import parse_args_gen_ray
from openai import OpenAI
from transformers import AutoTokenizer

from .utils import postprocess_generation, complete_code, clean_generation, mask, get_prompt, find_hdl_files
from configs.rules import RULES

"""
Run inference on the model using vLLM and Ray.
"""


def traverse_dataset(root_dir, tokenizer, args):

    for subdir, _, files in os.walk(root_dir):
        modules = find_hdl_files(files, args.hdl)


        if not modules or "mask_idx.json" not in files:
            print(f"Skipping {subdir} - missing required files")
            continue
        
        if len(modules) > 1:
            print(f"Warning: multiple HDL files in {subdir}, picking first: {modules[0]}")

        module_file = os.path.join(subdir, modules[0])  
        mask_file = os.path.join(subdir, "mask_idx.json")

        with open(module_file, "r") as f:
            text = f.read()

        with open(mask_file, "r") as mf:
            mask_lines = json.load(mf)

        base_name = os.path.basename(subdir)

        batch_raw_prompts = []
        batch_lines = []
        batch_indices = []
        batch_rules = []

        for rule in mask_lines:  # rules guaranteed ordered
            if rule not in RULES:
                continue

            tasks = mask_lines[rule]

            for i, line in enumerate(tasks, start=1):

                raw_prompt = mask(text, line, args)

                batch_raw_prompts.append(raw_prompt)
                batch_lines.append(line)
                batch_indices.append(i)
                batch_rules.append(rule)

                if len(batch_raw_prompts) == args.batch_size:
                    process_batch(
                        batch_raw_prompts,
                        batch_lines,
                        batch_indices,
                        batch_rules,
                        text,
                        base_name,
                        tokenizer,
                        args,
                    )

                    batch_raw_prompts = []
                    batch_lines = []
                    batch_indices = []
                    batch_rules = []

        if batch_raw_prompts:
            process_batch(
                batch_raw_prompts,
                batch_lines,
                batch_indices,
                batch_rules,
                text,
                base_name,
                tokenizer,
                args,
            )


def process_batch(
    batch_raw_prompts,
    batch_lines,
    batch_indices,
    batch_rules,
    text,
    base_name,
    tokenizer,
    args,
):

    # Build prompts
    batch_prompts = [
        get_prompt(rp, tokenizer, args)
        for rp in batch_raw_prompts
    ]

    try:
        response = client.completions.create(
            model=args.model_name,
            prompt=batch_prompts,   # list of strings = proper batching
            temperature=args.temperature,
            top_p=args.top_p,
            max_tokens=args.max_tokens,
            n=1,
        )

    except Exception as e:
        print(f"Batch generation failed: {e}")
        return

    choices = response.choices

    if len(choices) != len(batch_prompts):
        print(f"[WARNING] Batch mismatch: got {len(choices)} outputs for {len(batch_prompts)} prompts")

    for choice, line, idx, rule in zip(
        choices,
        batch_lines,
        batch_indices,
        batch_rules,
    ):
        raw_output = choice.text  # correct for completions endpoint

        _, gen_text = postprocess_generation(raw_output)
        gen_text = clean_generation(gen_text)

        print(f"\nGenerated text for rule '{rule}', line {line}, task {idx}:")
        print(gen_text)

        filled_code = complete_code(text, line, gen_text)

        save_dir = f"{base_name}-task{idx}"
        
        gen_name = "gen1.txt"
        full_gen_name = "full_gen1.txt"

        save_path_gen = os.path.join(args.output_path, rule, save_dir, gen_name)
        save_path_full_gen = os.path.join(args.output_path, rule, save_dir, full_gen_name)

        os.makedirs(os.path.dirname(save_path_gen), exist_ok=True)
        os.makedirs(os.path.dirname(save_path_full_gen), exist_ok=True)

        with open(save_path_gen, "w") as out_gen:
            out_gen.write(gen_text)

        with open(save_path_full_gen, "w") as out_full_gen:
            out_full_gen.write(filled_code)


if __name__ == "__main__":

    args = parse_args_gen_ray()

    client = OpenAI(
        base_url=f"http://{args.ip}:{args.port}/v1",
        api_key="EMPTY",
        timeout=600,
    )

    tokenizer = AutoTokenizer.from_pretrained(
        args.model_path,
        trust_remote_code=True,
        local_files_only=True
    )

    traverse_dataset(args.dataset_path, tokenizer, args)