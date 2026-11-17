"""A simple command-line calculator supporting +, -, *, and /."""

import sys


def calculate(left: float, operator: str, right: float) -> float:
    """Apply operator to left and right and return the result."""
    if operator == "+":
        return left + right
    if operator == "-":
        return left - right
    if operator == "*":
        return left * right
    if operator == "/":
        if right == 0:
            raise ZeroDivisionError("division by zero")
        return left / right
    raise ValueError(f"unsupported operator: {operator!r}")


def format_number(value: float) -> str:
    """Render whole numbers without a trailing .0."""
    if value.is_integer():
        return str(int(value))
    return str(value)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            f"Usage: python3 {sys.argv[0]} <number> <operator> <number>",
            file=sys.stderr,
        )
        return 2

    raw_left, operator, raw_right = argv
    try:
        left = float(raw_left)
        right = float(raw_right)
    except ValueError:
        print("Error: operands must be numbers.", file=sys.stderr)
        return 2

    try:
        result = calculate(left, operator, right)
    except (ZeroDivisionError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(format_number(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
