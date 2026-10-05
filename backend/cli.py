"""Terminal client for a running JARVIS server.

    python cli.py                        # talks to http://127.0.0.1:8000
    python cli.py --url http://host:port
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read() or b"{}").get("detail", e.reason)
        raise RuntimeError(f"{e.code}: {detail}") from None


def ask_approval(call: dict) -> bool:
    print(f"\n  JARVIS wants to run {call['tool']}  [{call['permission']}]")
    for key, value in call["input"].items():
        shown = value if len(str(value)) < 300 else str(value)[:300] + "..."
        print(f"    {key}: {shown}")
    return input("  Allow? [y/N] ").strip().lower() in ("y", "yes")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    base = parser.parse_args().url.rstrip("/")

    conversation_id = None
    print("JARVIS online. Type 'exit' to quit, 'new' for a fresh conversation.\n")
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not message:
            continue
        if message == "exit":
            return
        if message == "new":
            conversation_id = None
            print("(new conversation)\n")
            continue

        try:
            result = post(f"{base}/chat", {"message": message, "conversation_id": conversation_id})
            conversation_id = result["conversation_id"]
            while result["status"] == "waiting_for_user":
                if result["reply"]:
                    print(f"\njarvis> {result['reply']}")
                decisions = {call["id"]: ask_approval(call) for call in result["pending"]}
                result = post(f"{base}/chat/{conversation_id}/confirm", {"decisions": decisions})
            print(f"\njarvis> {result['reply']}\n")
        except RuntimeError as e:
            print(f"\n[error] {e}\n", file=sys.stderr)
        except urllib.error.URLError:
            print(f"\n[error] Can't reach JARVIS at {base}. Is the server running?\n", file=sys.stderr)


if __name__ == "__main__":
    main()
