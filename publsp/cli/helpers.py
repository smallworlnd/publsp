from pydantic import ValidationError


def format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"])
        lines.append(f"{loc}: {err['msg']}")
    return "Configuration error:\n  " + "\n  ".join(lines)
