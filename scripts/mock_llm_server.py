"""A local OpenAI-compatible endpoint for exercising the `custom` provider.

Why this exists
---------------
The LLM-assisted path involves a real HTTP provider, so it cannot be verified
by unit tests alone, and verifying it against a commercial endpoint costs money
and requires credentials that CI does not have. This server implements just
enough of the OpenAI API for the application to talk to something real:

    GET  /v1/models            -> a small model catalogue
    POST /v1/chat/completions  -> a schema-valid analysis payload

It deliberately returns a mix of good and bad observations so the application's
defences are exercised rather than assumed:

  1. an observation quoting real SOP text            -> must be KEPT
  2. an observation quoting text that does not exist -> must be DISCARDED
  3. a real quote attributed to the wrong document   -> must be DISCARDED

Every request is appended to a JSONL log so you can inspect exactly what the
application sent - in particular that untrusted document text was neutralised
and that the API key never appeared in the prompt body.

Usage
-----
    python scripts/mock_llm_server.py &

    export LLM_PROVIDER=custom
    export LLM_BASE_URL=http://127.0.0.1:8777/v1
    export LLM_API_KEY=sk-anything
    export LLM_MODEL=mock-analyst-v1
    export LLM_ALLOW_PAID_REQUESTS=true     # safe: localhost, nothing billable

    cd backend && python eval/harness.py --mode llm_assisted --out eval/results/llm.json

This is a test fixture. It is not a model, and its output is not an analysis.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

LOG = Path(os.environ.get("MOCK_LLM_LOG", "mock_llm_requests.jsonl"))
LOG.write_text("", encoding="utf-8")


def build_analysis(user_prompt: str) -> dict:
    """Produce a schema-valid response exercising three distinct paths.

    1. a legitimate observation quoting real SOP text  -> must be KEPT
    2. a fabricated quote                              -> must be DISCARDED
    3. a quote attributed to the wrong document        -> must be DISCARDED
    """
    block = ""
    start = user_prompt.find("<<<UNTRUSTED_SOP_TEXT_BEGIN>>>")
    end = user_prompt.find("<<<UNTRUSTED_SOP_TEXT_END>>>")
    if start != -1 and end != -1:
        block = user_prompt[start + len("<<<UNTRUSTED_SOP_TEXT_BEGIN>>>") : end]
    candidates = [
        ln.strip().lstrip("0123456789. )-*#").strip()
        for ln in block.splitlines()
        if 40 <= len(ln.strip()) <= 140 and not ln.strip().startswith((">", "#", "**", "|"))
    ]
    quote = candidates[0] if candidates else "Final assembly of the rotor housing."

    observations = [
        {
            "category": "ambiguous_requirement",
            "classification": "possible_deviation",
            "severity": "minor",
            "title": "Scope wording could be read two ways",
            "expected": "An unambiguous statement of what the procedure covers.",
            "observed": "The scope sentence does not state whether reworked units are included.",
            "explanation": (
                "The purpose statement does not say whether reworked units follow the same "
                "procedure, so a reviewer should confirm the intended scope."
            ),
            "suggested_action": "Confirm with the process owner whether reworked units are in scope.",
            "confidence": 0.45,
            "evidence": [{"source": "sop", "quote": quote}],
        },
        {
            "category": "unsupported_claim",
            "classification": "possible_deviation",
            "severity": "major",
            "title": "Fabricated observation that must be discarded",
            "expected": "Evidence that exists.",
            "observed": "Evidence that does not exist.",
            "explanation": "This observation cites text present in no stored document.",
            "confidence": 0.95,
            "evidence": [
                {"source": "report", "quote": "ZZZ this sentence appears in no stored document ZZZ"}
            ],
        },
        {
            "category": "missing_record",
            "classification": "insufficient_evidence",
            "severity": "major",
            "title": "Real quote attributed to the wrong document",
            "expected": "A quote attributed to the document it came from.",
            "observed": "SOP text claimed to be report text.",
            "explanation": "The quote is genuine but is attributed to the process report.",
            "confidence": 0.8,
            "evidence": [{"source": "report", "quote": quote}],
        },
    ]
    return {"observations": observations, "notes": ["Mock endpoint: deterministic fixture response."]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence stderr noise
        return

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "mock-analyst-v1", "object": "model", "context_length": 32768},
                        {"id": "mock-analyst-mini", "object": "model", "context_length": 8192},
                    ],
                },
            )
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        request = json.loads(raw or "{}")

        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization", ""),
                        "model": request.get("model"),
                        "temperature": request.get("temperature"),
                        "response_format": request.get("response_format"),
                        "messages": request.get("messages", []),
                    }
                )
                + "\n"
            )

        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": "not found"}})
            return

        messages = request.get("messages", [])
        user_prompt = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        content = json.dumps(build_analysis(user_prompt))

        self._send(
            200,
            {
                "id": "chatcmpl-mock-1",
                "object": "chat.completion",
                "created": 1760000000,
                "model": request.get("model", "mock-analyst-v1"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": max(1, len(user_prompt) // 4),
                    "completion_tokens": max(1, len(content) // 4),
                    "total_tokens": max(2, (len(user_prompt) + len(content)) // 4),
                },
            },
        )


if __name__ == "__main__":
    print("mock OpenAI-compatible server on http://127.0.0.1:8777/v1", flush=True)
    HTTPServer(("127.0.0.1", 8777), Handler).serve_forever()
