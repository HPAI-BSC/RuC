import os
import json
import re
from transformers import AutoTokenizer
from vllm import LLM, SamplingParams
from .parser import parse_args_gen
from configs.rules import RULES
from .utils import mask, postprocess_generation, complete_code, clean_generation, get_prompt, find_hdl_files

"""
Process the dataset and generate the code
"""


def traverse_dataset(root_dir, model, tokenizer, sampling, args):
    for subdir, _, files in os.walk(root_dir):
        print(f"Processing directory: {subdir}")
        print(f"Files found: {files}")

        modules = find_hdl_files(files, args.hdl)


        if not modules or "mask_idx.json" not in files:
            print(f"Skipping {subdir} - missing required files")
            continue
        
        if len(modules) > 1:
            print(f"Warning: multiple HDL files in {subdir}, picking first: {modules[0]}")

        module_file = os.path.join(subdir, modules[0])  
        mask_file = os.path.join(subdir, "mask_idx.json")

        print(f"Modules path: {module_file}")

        with open(module_file, 'r') as f:
            text = f.read()

        with open(mask_file, 'r') as mf:
            mask_lines = json.load(mf)

        base_name = os.path.basename(subdir)

        # Batch containers
        batch_prompts = []
        batch_lines = []
        batch_indices = []
        batch_rules = []

        for rule in mask_lines:
            if rule not in RULES:
                continue

            for i, line in enumerate(mask_lines[rule], start=1):
                raw_prompt = mask(text, line, args)
                prompt = get_prompt(raw_prompt, tokenizer, args)

                batch_prompts.append(prompt)
                batch_lines.append(line)
                batch_indices.append(i)
                batch_rules.append(rule)

                if len(batch_prompts) == args.batch_size:
                    process_batch(
                        batch_prompts,
                        batch_lines,
                        batch_indices,
                        batch_rules,
                        text,
                        base_name,
                        model,
                        sampling,
                        args,
                    )

                    batch_prompts = []
                    batch_lines = []
                    batch_indices = []
                    batch_rules = []

        # Process remaining
        if batch_prompts:
            process_batch(
                batch_prompts,
                batch_lines,
                batch_indices,
                batch_rules,
                text,
                base_name,
                model,
                sampling,
                args,
            )


def process_batch(
    batch_prompts,
    batch_lines,
    batch_indices,
    batch_rules,
    text,
    base_name,
    model,
    sampling,
    args,
):
    try:
        outputs = model.generate(
            prompts=batch_prompts,
            sampling_params=sampling,
        )
    except Exception as e:
        print(f"Batch generation failed: {e}")
        return

    if len(outputs) != len(batch_prompts):
        print(f"[WARNING] Batch mismatch: got {len(outputs)} outputs for {len(batch_prompts)} prompts")

    # Iterate per prompt
    for output, line, idx, rule in zip(
        outputs,
        batch_lines,
        batch_indices,
        batch_rules,
    ):
        generations = output.outputs

        for j, gen in enumerate(generations, start=1):
            if gen.finish_reason == "stop":
                think, gen_text = postprocess_generation(gen.text)

                print("\n\nGenerated text:")
                print(gen_text)

                gen_text = clean_generation(gen_text)
                filled_code = complete_code(text, line, gen_text)

            else:
                reason = f"Generation stopped {gen.finish_reason}"
                print(reason)
                filled_code = reason
                gen_text = reason

            save_dir = base_name + "-task" + str(idx)

            gen_name = "gen" + str(j) + ".txt"
            full_gen_name = "full_gen" + str(j) + ".txt"

            save_path_gen = os.path.join(args.output_path, rule, save_dir, gen_name)
            save_path_full_gen = os.path.join(args.output_path, rule, save_dir, full_gen_name)

            os.makedirs(os.path.dirname(save_path_gen), exist_ok=True)
            os.makedirs(os.path.dirname(save_path_full_gen), exist_ok=True)

            print(f"save_path_gen: {save_path_gen}")

            with open(save_path_gen, 'w') as out_gen:
                out_gen.write(gen_text)

            with open(save_path_full_gen, 'w') as out_full_gen:
                out_full_gen.write(filled_code)
                    
            

if __name__ == "__main__":
    args = parse_args_gen()

    print("Loading model...")
    model = LLM(
        model=args.model,
        trust_remote_code=True,
        gpu_memory_utilization=args.gpu_memory_utilization,
        swap_space=args.swap_space,
        dtype=args.precision,
        tensor_parallel_size=args.tensor_parallel_size,
        max_seq_len_to_capture=args.sequence_length_limit, 
        max_model_len=args.sequence_length_limit
    )

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)

    sampling = SamplingParams(
                    temperature=args.temperature,
                    top_p=args.top_p,
                    top_k=args.top_k,
                    max_tokens=args.max_tokens,
                    n=1,
                )
    
    print("Generating...")
    traverse_dataset(args.dataset_path, model, tokenizer, sampling, args)