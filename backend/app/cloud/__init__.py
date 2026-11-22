"""Hybrid mode: hand a turn over to a cloud model.

models.py   configured cloud models (cloud_models.yaml) and the LangChain model
usage.py    the cloud_usage table and the monthly budget
history.py  what the cloud model is shown of the conversation
routes.py   GET /api/cloud: what is available, and this month's spend

A chat's mode (local / auto / cloud) and whether its documents may be sent
live on chat_threads. A turn goes to the cloud when the chat is in cloud mode,
or when the user approves an escalation the local model proposed (auto mode).
"""

MODES = ("local", "auto", "cloud")
