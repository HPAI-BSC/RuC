import json
import re


def read_path(project_name):
    """ Extract the information from the project name """
    project, task_part = project_name.split("-task", 1)
    task = int(task_part)

    return project, task


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