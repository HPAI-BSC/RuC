import argparse

"""
Parse the hyperparameters and configurations defined in the shell scripts.
"""

def parse_args_gen():
    parser = argparse.ArgumentParser(description="RuC!")
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--max_tokens", type=int, help="Maximum number of tokens to generate per output sequence")
    parser.add_argument("--sequence_length_limit", type=int, help="The longest length of the models forward graph to capture when running the VLLM.")
    parser.add_argument("--temperature", type=float, help="Sampling temperature")
    parser.add_argument("--top_p", type=float, help="Top-p sampling parameter")
    parser.add_argument("--top_k", type=int, help="Top-k sampling parameter")
    parser.add_argument("--batch_size", type=int)
    parser.add_argument("--output_path", help="Path to save generations")
    parser.add_argument("--gpu_memory_utilization", type=float, help="GPU memory utilization limit")
    parser.add_argument("--swap_space", type=int, help="Swap space for GPU")
    parser.add_argument("--precision", help="Precision for model inference")
    parser.add_argument("--tensor_parallel_size", type=int, help="Tensor parallel size for model inference")
    parser.add_argument("--prompt_mode", type=str, help="Prompt mode to use ('chat' or 'fim')")
    parser.add_argument("--hdl", help="Hardware description language (v for Verilog, sv for SystemVerilog)")
    # parser.add_argument("--fim_mode", type=str, help="FIM mode to use ('psm' or 'spm')") 

    args, _ = parser.parse_known_args()
    return args

def parse_args_gen_ray():
    parser = argparse.ArgumentParser(description="RuC!")
    parser.add_argument("--model_name", type=str)
    parser.add_argument("--model_path", help="Path of the model")
    parser.add_argument("--ip", type=str, required=True, help="IP address of the vLLM server")
    parser.add_argument("--port", type=int, required=True, help="Port of the vLLM server")
    parser.add_argument("--dataset_path")
    parser.add_argument("--max_tokens", type=int)
    parser.add_argument("--sequence_length_limit", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--top_p", type=float)
    parser.add_argument("--top_k", type=int)
    parser.add_argument("--batch_size", type=int)
    parser.add_argument("--output_path")
    parser.add_argument("--prompt_mode", type=str)
    parser.add_argument("--hdl", help="Hardware description language (v for Verilog, sv for SystemVerilog)")
    # parser.add_argument("--fim_mode", type=str, help="FIM mode to use ('psm' or 'spm')")    

    args, _ = parser.parse_known_args()
    return args