from antlr4 import *
from antlr4 import InputStream, CommonTokenStream, ParserRuleContext
from antlr4.TokenStreamRewriter import TokenStreamRewriter


def count_nested_rules(ctx):
    """
    Number of parser rules in the subtree below `ctx` (iterative rather than 
    recursive).
    """
    count = 0
    stack = [ctx]

    while stack:
        node = stack.pop()

        for child in node.getChildren():
            if isinstance(child, ParserRuleContext):
                count += 1
                stack.append(child)

    return count


def get_token_count(tokenizer, text):
    tokens = tokenizer.encode(text)
    return len(tokens)


def create_parser(text, lexer_cls, parser_cls):
    # Consume the text input
    input_stream = InputStream(text)
    # Tokenize the input stream
    lexer = lexer_cls(input_stream)
    # Wrap the lexer in a token stream
    token_stream = CommonTokenStream(lexer)
    # Create a new parser instance
    parser = parser_cls(token_stream)
    # Create a rewriter for potential code modifications
    rewriter = TokenStreamRewriter(token_stream)

    return parser, rewriter


def round_2(x):
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
    return [f for f in files if f.endswith("." + hdl)]


def read_occurrence(entry):
    """
    Normalize a rule occurrence read from `all_mask_idx.json`.

    A single-file project stores a bare [start, stop] span, a multi-file project stores
    {"file": <relative source>, "span": [start, stop]}. Both come back here as a
    (file, span) pair, with `file` None when the project has a single source.
    """
    if isinstance(entry, dict):
        return entry.get("file"), entry["span"]

    return None, entry