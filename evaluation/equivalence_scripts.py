
# Drop the assertions
SYNTHESIS_DEFINES = ("SYNTHESIS",)


def read_options(include_dirs=(), defines=()) -> str:
    """
    The `-I` / `-D` prefix shared by `read_verilog` and `read_slang`.
    """
    options = [f"-I{directory}" for directory in include_dirs]
    options += [f"-D{define}" for define in defines]

    return " ".join(options)


def join_sources(sources) -> str:
    """
    Join the sources of a design into a single argument list for Yosys.

    A single-file design produces exactly the previous one-file argument, a multi-file
    design is read in the analysis order given by `ruc_project.json`.
    """
    if isinstance(sources, str):
        return sources

    return " ".join(str(source) for source in sources)


def generate_yosys_script_nst(
    reference_file, generated_file, top_module: str, include_dirs=(), defines=()
) -> str:
    """
    Create Yosys script for equivalence checking with full optimization pipeline.
    Uses inductive checking for better sequential circuit handling.

    Args:
        reference_file: Absolute path, or list of paths, to the reference sources
        generated_file: Absolute path, or list of paths, to the generated sources
        top_module: Name of the top module to check
        include_dirs: Where `` `include `` resolves from, for designs whose sources are
            not self contained
        defines: Macros defined for both reads. Empty for a self contained design, which
            leaves the command exactly as it was.

    Returns:
        Yosys script content as string
    """
    options = read_prefix(include_dirs, defines)

    script_content = f"""# ===== Golden Reference =====
read_verilog -sv {options}{join_sources(reference_file)}
hierarchy -check -top {top_module}
proc
opt -full
memory_map
opt
clk2fflogic
async2sync
opt
alumacc
opt -full
wreduce
peepopt
opt_clean
flatten
clean
design -save gold

# ===== LLM-Generated =====
design -reset
read_verilog -sv {options}{join_sources(generated_file)}
hierarchy -check -top {top_module}
proc
opt -full
memory_map
opt
clk2fflogic
async2sync
opt
alumacc
opt -full
wreduce
peepopt
opt_clean
flatten
clean
design -save gate

# ===== Equivalence Checking =====
design -copy-from gold -as gold A:top
design -copy-from gate -as gate A:top

equiv_make gold gate miter
hierarchy -top miter

equiv_simple -undef
equiv_induct -undef -seq 30

equiv_status
"""
    return script_content

def read_prefix(include_dirs=(), defines=()) -> str:
    """ `read_options` ready to splice before a file list, empty when there is nothing """
    options = read_options(include_dirs, defines)

    return options + " " if options else ""


def generate_yosys_script_nst_slang(
    reference_file, generated_file, top_module: str, include_dirs=(), defines=()
) -> str:
    """
    Create Yosys script for equivalence checking with full optimization pipeline.
    Uses inductive checking for better sequential circuit handling.

    Args:
        reference_file: Absolute path, or list of paths, to the reference sources
        generated_file: Absolute path, or list of paths, to the generated sources
        top_module: Name of the top module to check
        include_dirs: Where `` `include `` resolves from, for designs whose sources are
            not self contained
        defines: Macros defined for both reads. Empty for a self contained design, which
            leaves the command exactly as it was.

    Returns:
        Yosys script content as string

    `--single-unit` matters as soon as a design is more than one file. Per-file compilation
    units are slang's default, unlike `read_verilog`/`verilator`, which treat the whole file
    list as one preprocessing pass. Without it, a `` `define `` reaches only the file that
    (directly or by include) produced it.
    """
    options = read_prefix(include_dirs, defines)

    script_content = f"""# ===== Load slang plugin =====
plugin -i slang

# ===== Golden Reference =====
read_slang --single-unit {options}{join_sources(reference_file)}
hierarchy -check -top {top_module}
proc
opt -full
memory_map
opt
clk2fflogic
async2sync
opt
alumacc
opt -full
wreduce
peepopt
opt_clean
flatten
clean
design -save gold

# ===== LLM-Generated =====
design -reset
read_slang --single-unit {options}{join_sources(generated_file)}
hierarchy -check -top {top_module}
proc
opt -full
memory_map
opt
clk2fflogic
async2sync
opt
alumacc
opt -full
wreduce
peepopt
opt_clean
flatten
clean
design -save gate

# ===== Equivalence Checking =====
design -copy-from gold -as gold A:top
design -copy-from gate -as gate A:top

equiv_make gold gate miter
hierarchy -top miter

equiv_simple -undef
equiv_induct -undef -seq 30

equiv_status
"""
    return script_content

def generate_yosys_script_baseonly_verilog(
    reference_file, generated_file, top_module: str, include_dirs=(), defines=()
) -> str:
    """Create Yosys script for equivalence checking."""
    options = read_prefix(include_dirs, defines)

    script_content = f"""
# =============================================================================
# Load Designs
# =============================================================================
read_verilog -sv {options}{join_sources(reference_file)}
prep -flatten -top {top_module}
memory
clk2fflogic
design -stash gold

read_verilog -sv {options}{join_sources(generated_file)}
prep -flatten -top {top_module}
memory
clk2fflogic
design -stash gate

# =============================================================================
# Equivalence Check
# =============================================================================

design -copy-from gold -as gold A:top
design -copy-from gate -as gate A:top

miter -equiv -make_outputs gold gate miter
prep -flatten -top miter

sat -verify -prove trigger 0 -tempinduct-baseonly -maxsteps 20 -set-init-zero -set in_rst_n 1 miter
"""
    return script_content

def generate_yosys_script_baseonly_slang(
    reference_file, generated_file, top_module: str
) -> str:
    """Create Yosys script for equivalence checking."""
    script_content = f"""
# ===== Load slang plugin =====
plugin -i slang

# =============================================================================
# Load Designs
# =============================================================================
read_slang --single-unit {join_sources(reference_file)}
prep -flatten -top {top_module}
memory
clk2fflogic
design -stash gold

read_slang --single-unit {join_sources(generated_file)}
prep -flatten -top {top_module}
memory
clk2fflogic
design -stash gate

# =============================================================================
# Equivalence Check
# =============================================================================

design -copy-from gold -as gold A:top
design -copy-from gate -as gate A:top

miter -equiv -make_outputs gold gate miter
prep -flatten -top miter

sat -verify -prove trigger 0 -tempinduct-baseonly -maxsteps 20 -set-init-zero -set in_rst_n 1 miter
"""
    return script_content


def generate_yosys_script_ghdl(
    reference_file, generated_file, top_module: str
) -> str:
    """
    Create Yosys script for equivalence checking with full optimization pipeline.
    Uses inductive checking for better sequential circuit handling.

    Args:
        reference_file: Absolute path, or list of paths, to the reference sources
        generated_file: Absolute path, or list of paths, to the generated sources
        top_module: Name of the top module to check

    Returns:
        Yosys script content as string
    """
    script_content = f"""# ===== Golden Reference =====
ghdl --std=08 {join_sources(reference_file)} -e {top_module}
hierarchy -check -top {top_module}
proc
opt -full
memory_map
opt
clk2fflogic
async2sync
opt
alumacc
opt -full
wreduce
peepopt
opt_clean
flatten
clean
design -save gold

# ===== LLM-Generated =====
design -reset
ghdl --std=08 {join_sources(generated_file)} -e {top_module}
hierarchy -check -top {top_module}
proc
opt -full
memory_map
opt
clk2fflogic
async2sync
opt
alumacc
opt -full
wreduce
peepopt
opt_clean
flatten
clean
design -save gate

# ===== Equivalence Checking =====
design -copy-from gold -as gold A:{top_module}
design -copy-from gate -as gate A:{top_module}

equiv_make gold gate miter
hierarchy -top miter

equiv_simple -undef
equiv_induct -undef -seq 30

equiv_status
"""
    return script_content
