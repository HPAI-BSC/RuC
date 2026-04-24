import json
import re
from configs.prompts import SYSTEM_PROMPT, FIM_TEMPLATE


def get_prompt(raw_prompt, tokenizer, args):
    """ Create the actual prompt to give to the model (only changes for the chat template version) """
    instruction = (SYSTEM_PROMPT)

    if args.prompt_mode == "fim":
        return raw_prompt

    elif args.prompt_mode == "chat":
        if tokenizer.chat_template:

            inst_prompt = f"""
            Fill in the missing Verilog code marked by <MASK> in the following module:
            {raw_prompt}
            """

            conversation = [
                {"role": "system", "content": instruction},
                {"role": "user", "content": inst_prompt}
            ]

            prompt = tokenizer.apply_chat_template(
                conversation,
                tokenize=False,
                add_generation_prompt=True,
                #think=True
            )
        else:
            print("No chat template available")
            prompt = instruction + "\n\n" + raw_prompt
    
        return prompt



def mask(text, line, args):
    """ Mask lines from start to stop in the text"""
    start_mask, stop_mask = line

    #masked_text = text[max(0, int(prev_context)):start_mask] + "<MASK>" + text[stop_mask:(min(len(text), int(foll_context)))]
    print("\n\nRemoved Text")
    print(text[start_mask:stop_mask])

    
    prefix = text[0:start_mask]
    suffix = text[stop_mask:len(text)]

    if args.prompt_mode == "fim":

        raw_prompt = FIM_TEMPLATE.format(prefix=prefix, suffix=suffix)
        

    elif args.prompt_mode == "chat":
        raw_prompt = prefix + "<MASK>" + suffix

    return raw_prompt


def clean_generation(raw_generation):
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



def postprocess_generation(generation):
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


def complete_code(text, line, generation):
    start_mask, stop_mask = line
    filled_code = text[:start_mask] + generation + text[stop_mask:]
    
    return filled_code


def read_path(project_name):
    """ Extract the information from the project name """
    project, task_part = project_name.split("-task", 1)
    task = int(task_part)

    return project, task


def read_path_shuttle(project_name):
    """ Extract the information from the project name """
    shuttle, rest = project_name.split("-", 1)
    project, task_part = rest.rsplit("-task", 1)
    task = int(task_part)

    return shuttle, project, task


def save_generation_eqy_results(results, output_path):
    """ Used to analyze passed and failed samples """
    with open(output_path, "w") as f:
        json.dump(results, f, indent=4)


def find_hdl_files(files, hdl):
    if hdl == "sv":
        return [f for f in files if f.endswith(".sv")]
    elif hdl == "v":
        return [f for f in files if f.endswith(".v")]


# def add_generation_to_project(generation_path, dataset_project_path, task, rule):
#     mask_lines_path = os.path.join(dataset_project_path, "mask_lines.json")
#     with open(mask_lines_path, "r") as ml:
#         mask_lines = json.load(ml)
#     idx = task - 1
#     line = mask_lines[rule][idx]

#     with open(generation_path, "r") as g:
#         raw_generation = g.read()

#     modules_path = os.path.join(dataset_project_path, "modules.v")
#     with open(modules_path, "r") as m:
#         modules = m.read()

#     #generation = postprocess_generation(raw_generation)
#     generation = clean_generation(raw_generation)

#     full_gen_proc = complete_code(modules, line, generation)

#     task_dir = os.path.dirname(generation_path)
#     full_gen_proc_path = os.path.join(task_dir, "full_gen_proc.txt")

#     with open(full_gen_proc_path, 'w') as out_gen:
#         out_gen.write(full_gen_proc)

#     return full_gen_proc_path


# def run_multiple_syntax_check(folder_path: str, debug: bool):
#     """ Run syntax check on all files in the folder """
#     results = {}
#     for subdir, _, files in os.walk(folder_path):
#         if "modules.v" not in files or "mask_lines.json" not in files:
#             continue

#         module_file = os.path.join(subdir, "modules.v")

#         result = run_syntax_check(module_file, debug)
#         results[module_file] = result

#     return results