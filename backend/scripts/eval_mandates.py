"""Day 2 check: do real requests become the right mandates?

Run from backend/: python -m scripts.eval_mandates [model]
Calls the real model once per case. Prints no credentials.
"""

import asyncio
import sys

import openai

from app import intent
from app.config import load_settings
from app.stores import known_stores

# (request, expected fields of the reviewed mandate, words one problem must contain)
CASES = [
    ("Buy me running shoes from Trailhead, size 10, under $80",
     {"store": "Trailhead Running Co.", "max_total_cents": 8000, "size": "10", "quantity": 1}, None),
    ("I need two pairs of wool hiking socks from any store I've used, budget forty dollars",
     {"store": None, "max_total_cents": 4000, "quantity": 2, "no_detail": "store"}, None),
    ("two pairs of wool socks, any store I have used, max 30 dollars",
     {"store": None, "max_total_cents": 3000, "quantity": 2, "no_detail": "store"}, None),
    ("Get trail running shoes, size 9.5, in blue, keep it below 120 bucks total",
     {"max_total_cents": 12000, "size": "9.5", "detail": "blue"}, None),
    ("Order running shoes from Trailhead",
     {"store": "Trailhead Running Co.", "max_total_cents": None}, "spending limit"),
    ("Grab a water bottle for under 25 euros",
     {"max_total_cents": 2500}, "USD"),
    ("A headlamp, max $35, from Trai1head Running",
     {"store": None, "max_total_cents": 3500}, "haven't paid"),
]


def check(reviewed: dict, expected: dict, problem: str | None) -> list[str]:
    fields = reviewed["fields"]
    misses = []
    for key, want in expected.items():
        if key in ("detail", "no_detail"):
            got = " ".join(fields["details"]).lower()
            if (want in got) != (key == "detail"):
                misses.append(f"details {fields['details']} vs {key} {want!r}")
            continue
        got = reviewed[key] if key in ("store", "max_total_cents") else fields[key]
        if got != want:
            misses.append(f"{key}={got!r}, wanted {want!r}")
    found = " ".join(reviewed["problems"])
    if problem and problem not in found:
        misses.append(f"no problem mentioning {problem!r}")
    if not problem and reviewed["problems"]:
        misses.append(f"unexpected problems {reviewed['problems']}")
    return misses


async def main(model: str | None) -> None:
    settings = load_settings()
    model = model or settings.mandate_model
    client = openai.AsyncOpenAI(api_key=settings.llm_api_key)
    stores = known_stores(settings)
    drafts = await asyncio.gather(
        *(intent.read_request(client, model, request) for request, _, _ in CASES)
    )
    passed = 0
    for (request, expected, problem), draft in zip(CASES, drafts):
        reviewed = intent.review(intent.draft_to_fields(draft), stores)
        misses = check(reviewed, expected, problem)
        passed += not misses
        print(f"{'PASS' if not misses else 'FAIL'}: {request}")
        for miss in misses:
            print(f"    {miss}")
        if draft.questions:
            print(f"    asks: {draft.questions}")
    print(f"{model}: {passed} of {len(CASES)} correct")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
