"""Test questions for measuring how well a local model knows its own limits.

Every question has one exact answer that a program can check, so grading needs
no second model and no judgement. Where an answer can be computed it is
computed here (arithmetic, dates, letter counts, the output of code snippets),
never typed in by hand. Questions are generated from a fixed seed, so a run is
repeatable and runs of different models are comparable.

Categories, each with easy and hard questions:

- arithmetic      products, small and large
- word_problem    short multi-step problems
- code_trace      what a Python snippet prints (snippets are run here, once,
                  to get the answer; the model's own code is never run)
- letters         counting a letter in a string (hard for tokenised models)
- dates           day of the week, days between two dates
- logic           classic puzzles with a single answer
- facts           well-known facts
- unknowable      answers no offline model can know; the right answer is UNKNOWN

`expect_escalate` marks questions where escalating, or answering UNKNOWN, is
the only right move. For the rest, whether escalating was right is decided
afterwards from whether the model actually got the question wrong.
"""

import contextlib
import io
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    difficulty: str  # "easy" or "hard"
    question: str
    answer: str  # canonical answer; "UNKNOWN" for unknowable questions
    kind: str = "text"  # how to compare: "int", "text", or "unknown"
    aliases: tuple[str, ...] = field(default=())
    expect_escalate: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _arithmetic(rng: random.Random) -> list[Case]:
    cases = []
    for i in range(6):
        a, b = rng.randint(12, 99), rng.randint(12, 99)
        cases.append(Case(f"arith-easy-{i}", "arithmetic", "easy",
                          f"What is {a} × {b}?", str(a * b), "int"))
    for i in range(6):
        a, b = rng.randint(100_000, 999_999), rng.randint(100_000, 999_999)
        cases.append(Case(f"arith-hard-{i}", "arithmetic", "hard",
                          f"What is {a} × {b}? Give the exact integer.", str(a * b), "int"))
    return cases


def _word_problems() -> list[Case]:
    rows = [
        ("easy", "A shop sells pens at 3 for $2. How many dollars do 18 pens cost?", 18 // 3 * 2),
        ("easy", "A train leaves at 09:40 and arrives at 13:15. How many minutes is the trip?",
         (13 * 60 + 15) - (9 * 60 + 40)),
        ("easy", "Tom has 5 boxes with 12 apples each. He gives away 17 apples. How many are left?",
         5 * 12 - 17),
        ("hard", "A tank fills at 7 litres per minute and leaks 2 litres per minute. It starts with "
                 "35 litres and holds 600 litres. After how many whole minutes is it first full?",
         -(-(600 - 35) // (7 - 2))),
        ("hard", "Three workers paint a fence: A alone takes 6 hours, B alone 10 hours, C alone 15 "
                 "hours. Working together, how many minutes do they take?",
         int(60 / (1 / 6 + 1 / 10 + 1 / 15))),
        ("hard", "How many integers from 1 to 1000 inclusive are divisible by 3 or 5 but not by 7?",
         sum(1 for n in range(1, 1001) if (n % 3 == 0 or n % 5 == 0) and n % 7 != 0)),
    ]
    return [Case(f"word-{d}-{i}", "word_problem", d, q, str(a), "int")
            for i, (d, q, a) in enumerate(rows)]


# Each snippet is run once, here, to get the expected output.
SNIPPETS = [
    ("easy", "x = [1, 2, 3]\ny = x\ny.append(4)\nprint(len(x))"),
    ("easy", "s = 'hello'\nprint(s[1:4].upper())"),
    ("easy", "print(sum(range(1, 11)))"),
    ("easy", "d = {'a': 1, 'b': 2}\nd['a'] += 5\nprint(d['a'] * d['b'])"),
    ("easy", "print(7 // 2 + 7 % 2)"),
    ("hard", "fs = [lambda: i for i in range(3)]\nprint(sum(f() for f in fs))"),
    ("hard", "def f(x, acc=[]):\n    acc.append(x)\n    return len(acc)\nf(1); f(2)\nprint(f(3))"),
    ("hard", "print(sum(int(c) for c in str(2 ** 50)))"),
    ("hard", "a = [[0] * 3] * 3\na[0][1] = 5\nprint(sum(sum(r) for r in a))"),
    ("hard", "from functools import reduce\nprint(reduce(lambda a, b: a ^ b, range(1, 101)))"),
    ("hard", "x = 0\nfor i in range(10):\n    for j in range(i):\n        if (i + j) % 3 == 0:\n            x += j\nprint(x)"),
]


def _run_snippet(code: str) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(code, {})  # our own fixed snippets, never model output
    return buffer.getvalue().strip()


def _code_trace() -> list[Case]:
    cases = []
    for i, (difficulty, code) in enumerate(SNIPPETS):
        out = _run_snippet(code)
        kind = "int" if out.lstrip("-").isdigit() else "text"
        cases.append(Case(f"code-{difficulty}-{i}", "code_trace", difficulty,
                          f"What does this Python code print?\n```python\n{code}\n```", out, kind))
    return cases


def _letters(rng: random.Random) -> list[Case]:
    cases = []
    for i, word in enumerate(["banana", "strawberry", "mississippi"]):
        letter = {"banana": "a", "strawberry": "r", "mississippi": "s"}[word]
        cases.append(Case(f"letters-easy-{i}", "letters", "easy",
                          f"How many times does the letter '{letter}' appear in the word '{word}'?",
                          str(word.count(letter)), "int"))
    for i in range(4):
        text = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(48))
        letter = rng.choice("aeiou")
        cases.append(Case(f"letters-hard-{i}", "letters", "hard",
                          f"How many times does the letter '{letter}' appear in this string?\n{text}",
                          str(text.count(letter)), "int"))
    return cases


def _dates(rng: random.Random) -> list[Case]:
    cases = []
    for i in range(2):
        d = date(2024, 1, 1) + timedelta(days=rng.randint(0, 365))
        cases.append(Case(f"dates-easy-{i}", "dates", "easy",
                          f"If today is {WEEKDAYS[d.weekday()]}, {d.isoformat()}, what day of the week "
                          f"is {(d + timedelta(days=3)).isoformat()}?",
                          WEEKDAYS[(d + timedelta(days=3)).weekday()]))
    for i in range(4):
        d = date(1900, 1, 1) + timedelta(days=rng.randint(0, 50_000))
        cases.append(Case(f"dates-hard-{i}", "dates", "hard",
                          f"What day of the week was {d.isoformat()}?", WEEKDAYS[d.weekday()]))
    for i in range(2):
        a = date(1990, 1, 1) + timedelta(days=rng.randint(0, 12_000))
        b = a + timedelta(days=rng.randint(400, 9000))
        cases.append(Case(f"dates-span-{i}", "dates", "hard",
                          f"How many days are there from {a.isoformat()} to {b.isoformat()}? "
                          "(Count the difference, not including the start day.)",
                          str((b - a).days), "int"))
    return cases


def _logic() -> list[Case]:
    rows = [
        ("easy", "Alice has 3 brothers. Each of her brothers has 2 sisters. How many sisters does "
                 "Alice have?", "1", "int"),
        ("easy", "If all bloops are razzies and all razzies are lazzies, are all bloops lazzies? "
                 "Answer yes or no.", "yes", "text"),
        ("hard", "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How "
                 "many cents does the ball cost?", "5", "int"),
        ("hard", "It takes 5 machines 5 minutes to make 5 widgets. How many minutes would it take "
                 "100 machines to make 100 widgets?", "5", "int"),
        ("hard", "On an island, knights always tell the truth and knaves always lie. A says: 'B is a "
                 "knave.' B says: 'A and I are both knights.' Is A a knight or a knave? Answer "
                 "knight or knave.", "knight", "text"),
        ("hard", "A lily pad patch doubles in size every day. It covers the whole lake on day 48. On "
                 "which day did it cover half the lake?", "47", "int"),
    ]
    return [Case(f"logic-{d}-{i}", "logic", d, q, a, k) for i, (d, q, a, k) in enumerate(rows)]


def _facts() -> list[Case]:
    rows = [
        ("What is the capital of Australia?", "Canberra", ()),
        ("What is the chemical symbol for gold?", "Au", ()),
        ("How many bits are in a byte?", "8", ()),
        ("Which planet is closest to the Sun?", "Mercury", ()),
        ("In which year did the Berlin Wall fall?", "1989", ()),
    ]
    return [Case(f"facts-easy-{i}", "facts", "easy", q, a,
                 "int" if a.isdigit() else "text", aliases)
            for i, (q, a, aliases) in enumerate(rows)]


def _unknowable() -> list[Case]:
    rows = [
        "What was the closing price of Apple (AAPL) stock on 2026-11-20, in US dollars?",
        "What is the current temperature in Ottawa right now, in °C?",
        "What is the exact number of messages in this user's email inbox?",
        "Who won the most recent football match played yesterday between the two top teams in "
        "the Spanish league, and what was the score?",
    ]
    return [Case(f"unknowable-{i}", "unknowable", "hard", q, "UNKNOWN", "unknown",
                 expect_escalate=True) for i, q in enumerate(rows)]


def build_cases(seed: int = 20261121) -> list[Case]:
    rng = random.Random(seed)
    return (_arithmetic(rng) + _word_problems() + _code_trace() + _letters(rng)
            + _dates(rng) + _logic() + _facts() + _unknowable())


if __name__ == "__main__":
    for case in build_cases():
        print(f"{case.id:18} {case.difficulty:5} {case.answer[:30]!r:34} {case.question[:60]!r}")
