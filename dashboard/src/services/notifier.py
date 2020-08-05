import logging
from collections import defaultdict, deque
from copy import deepcopy
from pathlib import Path
from typing import Any, DefaultDict, Iterator, List

from fastapi import WebSocket

from config import settings
from models import Message

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self):
        self.connections: List[WebSocket] = list()
        self.notification_generator = self.get_notification_generator()
        self.save_file_generator = self.get_save_file_generator()
        self.history: DefaultDict[str, deque[Message]] = defaultdict(
            lambda: deque(maxlen=80)
        )
        if settings.debug:
            self.load_history_from_file()

    async def get_notification_generator(self):
        while True:
            message = yield
            await self._notify(message)

    async def _notify(self, message: str):
        # https://github.com/tiangolo/fastapi/issues/258
        living_connections = []
        while len(self.connections) > 0:
            # Looping like this is necessary in case a disconnection is handled
            # during await websocket.send_text(message)
            websocket = self.connections.pop()
            await websocket.send_text(message)
            living_connections.append(websocket)
        self.connections = living_connections

    async def send(self, message=None, **data: Any):
        if message is None:
            message = Message.parse_obj(data)
        await self.notification_generator.asend(message.json())
        self.save_message(message)

    async def connect(self, websocket: WebSocket):
        logger.debug("New websocket connection from %s", websocket.client.host)
        await websocket.accept()
        self.connections.append(websocket)

        # send new client copies of any recent messages
        history = deepcopy(self.history)
        for messages in history.values():
            for message in messages:
                message.historical = True
                await self.send(message)

    def disconnect(self, websocket: WebSocket):
        self.connections.remove(websocket)

    def save_message(self, message: Message):
        self.history[message.topic].append(message)
        if settings.debug:
            self.save_file_generator.send(message)

    def get_save_file_generator(self, filename="messages.json"):
        counter = 0
        with open(filename, mode="w") as fp:
            while True:
                message = yield
                fp.write(message.json() + "\n")
                counter += 1
                if counter > 100:
                    fp.flush()
                    counter = 0

    def load_history_from_file(self, filename="messages.json"):
        logger.info("Loading message history from file")
        if not Path(filename).exists():
            return
        with open(filename, "r") as fp:
            for d in fp.readlines():
                message = Message.parse_raw(d, content_type="application/json")
                self.history[message.topic].append(message)

    def purge_history(self):
        logger.info("Purging message history")
        for topic in self.history.keys():
            self.history[topic].clear()
