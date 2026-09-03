"""The fake generator of the test suite: the program `[generator] command` names in a test vault.

Its first argument is a state directory that the `FakeGenerator` helper in `tests/conftest.py` fills:
`output` is what to print on stdout, `stderr` what to print on stderr, `status` the exit status (0 without it),
and `sleep` seconds to wait before answering. The script records the prompt it reads on stdin under `prompts/`
and any further arguments in `args.json`, so a test can check what reached it. Without an `output` file it fails
loudly, since a test that runs the generator must say what it answers.
"""

import json
import sys
import time
from pathlib import Path


def main() -> int:
    state = Path(sys.argv[1])
    prompts = state / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    prompt = sys.stdin.read()
    (prompts / f"{len(list(prompts.iterdir())) + 1:03d}.md").write_text(prompt, encoding="utf-8")
    (state / "args.json").write_text(json.dumps(sys.argv[2:]), encoding="utf-8")
    sleep = state / "sleep"
    if sleep.is_file():
        time.sleep(float(sleep.read_text(encoding="utf-8")))
    stderr = state / "stderr"
    if stderr.is_file():
        sys.stderr.write(stderr.read_text(encoding="utf-8"))
        sys.stderr.flush()
    output = state / "output"
    if not output.is_file():
        sys.stderr.write(f"fake generator: no reply scripted in {state}\n")
        return 3
    sys.stdout.write(output.read_text(encoding="utf-8"))
    sys.stdout.flush()
    status = state / "status"
    return int(status.read_text(encoding="utf-8")) if status.is_file() else 0


if __name__ == "__main__":
    sys.exit(main())
