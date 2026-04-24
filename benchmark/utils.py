from antlr4 import *
from antlr4 import InputStream, CommonTokenStream, ParserRuleContext
from antlr4.TokenStreamRewriter import TokenStreamRewriter
from .antlr.SystemVerilogLexer import SystemVerilogLexer
from .antlr.SystemVerilogParser import SystemVerilogParser


def count_nested_rules(ctx):
    count = 0

    for child in ctx.getChildren():
        if isinstance(child, ParserRuleContext):
            count += 1
            count += count_nested_rules(child)
    
    return count


def get_token_count(tokenizer, text):
    tokens = tokenizer.encode(text)
    return len(tokens)


def fresh_parser(text):
    # Consume the text input
    input_stream = InputStream(text)
    # Tokenize the input stream
    lexer = SystemVerilogLexer(input_stream)
    # Wrap the lexer in a token stream
    token_stream = CommonTokenStream(lexer)
    # Create a new parser instance
    parser = SystemVerilogParser(token_stream)
    # Create a rewriter for potential code modifications
    rewriter = TokenStreamRewriter(token_stream)

    return parser, rewriter


def r2(x):
    return round(x, 2)


def extend_stop_to_line_end(text, stop_idx):
    """
    Given a character index `stop_idx`, extend it to the end of that line.
    """
    line_end = text.find("\n", stop_idx)
    if line_end == -1:
        return len(text) - 1
    return line_end


def extend_start_to_line_begin(text, start_idx):
    """
    Given a character index `start_idx`, extend it to the beginning of that line.
    """
    line_begin = text.rfind("\n", 0, start_idx)
    if line_begin == -1:
        return 0
    return line_begin + 1


def extract_project_task(name):
    project, task = name.rsplit("-task", 1)
    return project, int(task)


def complete_code(text, line, generation):
    start_mask, stop_mask = line
    filled_code = text[:start_mask] + generation + text[stop_mask:]
    
    return filled_code


def find_hdl_files(files, hdl):
    if hdl == "sv":
        return [f for f in files if f.endswith(".sv")]
    elif hdl == "v":
        return [f for f in files if f.endswith(".v")]