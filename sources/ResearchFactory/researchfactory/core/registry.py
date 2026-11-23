"""为 HEP 任务类型和运行框架注册具名实现。"""
from collections.abc import Iterator, MutableMapping
from typing import Any, TypeVar

T = TypeVar("T")

def register_named_class(
    entries: MutableMapping[str, type[T]],
    cls: type[T],
    *,
    base: type[T],
    kind: str,
) -> type[T]:
    """Register one named implementation without accidental replacement."""
    if not isinstance(cls, type) or not issubclass(cls, base):
        raise TypeError(f"{cls!r} must inherit from {base.__name__}")
    name = str(getattr(cls, "name", "") or "").strip()
    if not name:
        raise ValueError(f"{cls.__name__} must set a non-empty `name`")
    previous = entries.get(name)
    if previous is not None and previous is not cls:
        raise ValueError(f"duplicate {kind} {name!r} ({cls.__name__})")
    entries[name] = cls
    return cls



def registered_module_classes(module: Any, base: type[T]) -> Iterator[type[T]]:
    """Yield named subclasses from an already-imported extension module."""
    for value in vars(module).values():
        if (
            isinstance(value, type)
            and value is not base
            and issubclass(value, base)
            and str(getattr(value, "name", "") or "").strip()
        ):
            yield value

