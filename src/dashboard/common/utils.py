import asyncio
import json
import logging
import os
import re
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

import aiofiles
import dateutil.parser
import google.auth
from fastapi import WebSocket
from google.auth.transport.requests import AuthorizedSession
from google.cloud import pubsub_v1
from requests import Session

from .constants import CENSOR_SYMBOL as X
from .constants import (
    CLOUD_BUILD_API,
    CLOUD_BUILD_SUBSCRIPTION_ID,
    CLOUD_BUILD_TRIGGER_ID,
    GITHUB_BRANCH,
    GITHUB_REPO,
)

logging.basicConfig(level=logging.INFO)

credentials, project = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)

loop = asyncio.get_event_loop()


def extract_app_name_from_url(url):
    m = re.search(r"^https?://(?P<name>.*?)[\.|:|/]", url, re.IGNORECASE)
    if m:
        return m.group("name").replace("_", "-").lower()
    raise ValueError("Invalid application URL")


def get_active_builds(session: Optional[Session] = None) -> List[Dict[str, Any]]:
    if session is None:
        session = AuthorizedSession(credentials)
    builds_query = {"filter": 'status="QUEUED" OR status="WORKING"'}
    project = "servian-labs-7apps"
    resp = session.get(
        f"{CLOUD_BUILD_API}/projects/{project}/builds", params=builds_query,
    )
    resp.raise_for_status()
    builds: List[Dict[str, str]] = resp.json().get("builds", [])
    return [
        {
            "id": b["id"],
            "start_time": dateutil.parser.parse(b["startTime"]),
            "finish_time": dateutil.parser.parse(b["finishTime"])
            if b.get("finishTime") is not None
            else None,
        }
        for b in builds
    ]


def trigger_build(
    substitutions: Dict[str, str], session: Optional[Session] = None
) -> str:
    if session is None:
        session = AuthorizedSession(credentials)

    source = {
        "repoName": GITHUB_REPO,
        "branchName": GITHUB_BRANCH,
        "substitutions": substitutions,
    }
    resp = session.post(f"{CLOUD_BUILD_API}/{CLOUD_BUILD_TRIGGER_ID}:run", json=source,)
    resp.raise_for_status()

    operation = resp.json()
    return operation["metadata"]["build"]["id"]


class PubSubMessageBroker:
    def __init__(self):
        self.connections: List[WebSocket] = list()
        self.pubsub = pubsub_v1.SubscriberClient()
        self.log_collection = defaultdict(lambda: deque(maxlen=50))
        self.generator = self.get_stream_generator()
        self.subscribe()

    async def get_stream_generator(self):
        while True:
            data = yield
            await self.send(data)

    async def send(self, data: str):
        living_connections = []
        while len(self.connections) > 0:
            # Looping like this is necessary in case a disconnection is handled
            # during await websocket.send_text(message)
            websocket = self.connections.pop()
            await websocket.send_text(data)
            living_connections.append(websocket)
        self.connections = living_connections

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.connections.remove(websocket)

    def parse_log_record(self, data):
        build_step = data["labels"]["build_step"]
        text = data["textPayload"]
        rec = {
            "id": data["resource"]["labels"]["build_id"],
            "step": None,
            "command": None,
            "type": None,
            "timestamp": data["timestamp"],
            "severity": data["severity"],
            "is_start": build_step.startswith("Starting"),
            "is_finish": build_step.startswith("Finishing"),
        }

        # log source
        if build_step.startswith("gsutil"):
            rec["type"] = "gsutil"
        elif build_step.startswith("MAIN"):
            rec["type"] = "main"
        elif build_step.startswith("PUSH"):
            rec["type"] = "push"
        elif build_step.startswith("Step"):
            rec["type"] = "step"
        else:
            rec["type"] = "other"

        # extract metadata from common patterns
        m = re.match(r"Step #(?P<step_no>\d{1,2}).+?\"(?P<step>.*?)\"", text)
        rec.update(m.groupdict() if m else {})
        m = re.match(r"^Step.+?: (?P<command>.+?)$", text)
        rec.update(m.groupdict() if m else {})

        # remove step prefix
        text = text.replace(f"{build_step}:", "").strip()
        text = re.sub(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} ", "", text)

        # sanitise potentially sensitive data
        text = re.sub(r"(?<=projects/)\S+?(?=/|$)", "".rjust(10, X), text)
        text = re.sub(r"(?<=gs://)(\S+?)(?=/|\s|$)", "".rjust(10, X), text)
        text = re.sub(r"(?<=gcr\.io/)(\S+?)(?=/|$)", "".rjust(10, X), text)
        text = re.sub(r"\S+?@\S+?(?=\.|$)", "@".rjust(10, X).ljust(20, X), text)
        rec["text"] = text

        return rec

    async def save_to_file(self, data: str, filename: str):
        save_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        async with aiofiles.open(os.path.join(save_dir, filename), mode="a") as f:
            await f.write(data + "\n")

    async def handle_message(self, message):
        data = json.loads(message.data, encoding="utf-8")
        log = self.parse_log_record(data)

        # persist logs so they're available to new/late-arriving clients
        self.log_collection[log["id"]].append(log)

        json_log = json.dumps(log, ensure_ascii=False, separators=(",", ":"))
        await self.save_to_file(json_log, f"{log['id']}.log")
        await self.generator.asend(json_log)

    def subscribe(self):
        """Listens for new Pub/Sub messages."""
        logging.info("subscribing to pubsub")
        future = self.pubsub.subscribe(
            CLOUD_BUILD_SUBSCRIPTION_ID, self._handle_message()
        )
        return future

    def _handle_message(self):
        def callback(message):
            future = asyncio.run_coroutine_threadsafe(
                self.handle_message(message), loop
            )
            future.result()
            message.ack()

        return callback