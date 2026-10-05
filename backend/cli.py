"""Terminal client for a running JARVIS server.

    python cli.py                        # talks to http://127.0.0.1:8000
    python cli.py --url http://host:port

Commands: 'list' shows saved conversations, 'open <id>' resumes one, 'new' starts fresh,
'memory' shows remembered facts, 'index <path>' adds a file to searchable memory, 'exit' quits.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def request(url: str, payload: dict | None = None) -> dict | list:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
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


def handle_approvals(base: str, conversation_id: str, result: dict) -> dict:
    while result.get("pending"):
        if result.get("reply"):
            print(f"\njarvis> {result['reply']}")
        decisions = {call["id"]: ask_approval(call) for call in result["pending"]}
        result = request(f"{base}/chat/{conversation_id}/confirm", {"decisions": decisions})
    return result


def list_conversations(base: str) -> None:
    conversations = request(f"{base}/conversations?limit=20")
    if not conversations:
        print("(no saved conversations)\n")
        return
    for c in conversations:
        flag = "  [waiting for approval]" if c["waiting_for_user"] else ""
        print(f"  {c['id']}  {c['updated_at'][:16].replace('T', ' ')}  {c['title'] or '(untitled)'}{flag}")
    print()


def open_conversation(base: str, conversation_id: str) -> str:
    conversation = request(f"{base}/conversations/{conversation_id}")
    print(f"(resumed: {conversation['title'] or conversation_id})")
    for message in conversation["messages"][-6:]:
        speaker = "you" if message["role"] == "user" else "jarvis"
        print(f"{speaker}> {message['text']}")
    if conversation["pending"]:
        result = handle_approvals(base, conversation_id, conversation)
        print(f"\njarvis> {result['reply']}")
    print()
    return conversation_id


def show_memory(base: str) -> None:
    facts = request(f"{base}/memory/facts")
    documents = request(f"{base}/memory/documents")
    print("Facts:" if facts else "(no remembered facts)")
    for f in facts:
        print(f"  #{f['id']}  {f['content']}")
    if documents:
        print("Indexed documents:")
        for d in documents:
            print(f"  {d['path']}  ({d['chunk_count']} parts)")
    print()


def index_file(base: str, path: str) -> None:
    result = request(f"{base}/memory/documents", {"path": path})
    print(f"({result['status']}: {result['document']['path']}, {result['document']['chunk_count']} parts)\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    base = parser.parse_args().url.rstrip("/")

    conversation_id = None
    print("JARVIS online. Commands: list, open <id>, new, memory, index <path>, exit.\n")
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        try:
            if not message:
                continue
            if message == "exit":
                return
            if message == "new":
                conversation_id = None
                print("(new conversation)\n")
            elif message == "list":
                list_conversations(base)
            elif message == "memory":
                show_memory(base)
            elif message.startswith("index "):
                index_file(base, message.split(maxsplit=1)[1].strip('"'))
            elif message.startswith("open "):
                conversation_id = open_conversation(base, message.split(maxsplit=1)[1])
            else:
                result = request(f"{base}/chat", {"message": message, "conversation_id": conversation_id})
                conversation_id = result["conversation_id"]
                result = handle_approvals(base, conversation_id, result)
                print(f"\njarvis> {result['reply']}\n")
        except RuntimeError as e:
            print(f"\n[error] {e}\n", file=sys.stderr)
        except urllib.error.URLError:
            print(f"\n[error] Can't reach JARVIS at {base}. Is the server running?\n", file=sys.stderr)


if __name__ == "__main__":
    main()
