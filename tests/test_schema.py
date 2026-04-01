from dataclasses import dataclass
from norless.schema import field, parse, as_list, optfield

from typing import assert_type


def check_types() -> None:
    @dataclass
    class Boo:
        foo: int = field(int)
        optfoo: int = optfield(int)  # type: ignore[assignment]
        zoo: int = field(str)  # type: ignore[assignment]
        optzoo: int | None = optfield(int)

    assert_type(Boo().foo, int)


def test_simple() -> None:
    @dataclass(frozen=True)
    class Boo:
        far: int = field(int, src='foo')
        bar: str | None = optfield(str)

    @dataclass(frozen=True)
    class Zoo:
        boos: list[Boo] = field(as_list(Boo))

    result = parse(Zoo, {'boos': [{'foo': '42'}]})
    assert result == Zoo([Boo(42, None)])
