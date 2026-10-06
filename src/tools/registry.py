"""Tool registry and invocation wrapper.

Every tool - whether a model chose it or the orchestrator called it directly -
goes through `ToolRunner.call`. That gives one place for telemetry, one place
for error containment, and one schema list to hand to a tool-calling model, so
architectures A and B are measured on exactly the same instrumentation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

ToolFn = Callable[..., Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    fn: ToolFn
    deterministic: bool


class ToolError(Exception):
    """Raised by a tool when it cannot produce a result.

    Tools surface failure rather than returning a neutral-looking default, so
    the policy engine can distinguish 'verified as fine' from 'not verified'.
    """


_REGISTRY: dict[str, ToolSpec] = {}


def register(name: str, description: str, parameters: dict, *, deterministic: bool) -> Callable[[ToolFn], ToolFn]:
    def decorator(fn: ToolFn) -> ToolFn:
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            fn=fn,
            deterministic=deterministic,
        )
        return fn

    return decorator


def get(name: str) -> ToolSpec:
    if name not in _REGISTRY:
        raise ToolError(f"Unknown tool: {name}")
    return _REGISTRY[name]


def all_specs() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def schemas(names: list[str] | None = None) -> list[dict]:
    """Tool schemas in Anthropic tool-use format."""
    specs = all_specs() if names is None else [get(n) for n in names]
    return [
        {"name": s.name, "description": s.description, "input_schema": s.parameters}
        for s in specs
    ]


@dataclass
class ToolRunner:
    """Executes tools and records what was called."""

    calls: int = 0
    names: list[str] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)

    def call(self, name: str, **kwargs: Any) -> Any:
        spec = get(name)
        self.calls += 1
        self.names.append(name)
        try:
            result = spec.fn(**kwargs)
        except ToolError as exc:
            self.log.append({"tool": name, "input": kwargs, "ok": False, "error": str(exc)})
            raise
        except Exception as exc:  # a tool bug must not be read as a clean result
            self.log.append({"tool": name, "input": kwargs, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            raise ToolError(f"{name} failed: {type(exc).__name__}: {exc}") from exc
        self.log.append({"tool": name, "input": kwargs, "ok": True})
        return result
