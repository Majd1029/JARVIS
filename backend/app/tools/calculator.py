"""Safe arithmetic evaluation (no eval())."""

import ast
import math
import operator

from app.security.permissions import PermissionLevel
from app.tools.base import Tool, ToolError

_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    name: getattr(math, name)
    for name in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10", "log2",
                 "exp", "floor", "ceil", "factorial", "radians", "degrees")
}
_FUNCTIONS.update(abs=abs, round=round, min=min, max=max)
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MAX_EXPONENT = 10_000


def _eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ToolError("Exponent too large.")
        return _BINARY_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS and not node.keywords):
        return _FUNCTIONS[node.func.id](*(_eval(arg) for arg in node.args))
    raise ToolError(f"Unsupported expression element: {ast.dump(node)[:80]}")


def calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_eval(tree))
    except ToolError:
        raise
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError) as e:
        raise ToolError(f"Could not evaluate '{expression}': {e}") from e


calculator_tool = Tool(
    name="calculator",
    description=(
        "Evaluate an arithmetic expression exactly. Supports + - * / // % **, parentheses, "
        "constants pi/e/tau and functions sqrt, sin, cos, tan, log, log10, log2, exp, floor, "
        "ceil, factorial, abs, round, min, max. Use this instead of doing math in your head."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "e.g. 'sqrt(2) * (3 + 4) ** 2'"},
        },
        "required": ["expression"],
        "additionalProperties": False,
    },
    permission=PermissionLevel.PUBLIC_READ,
    handler=calculate,
)
