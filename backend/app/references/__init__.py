"""Reference photos attached to a chat, for image generation that should look like them.

normalize.py  check and clean an upload: first frame, orientation, no metadata, max 2048 px, PNG
store.py      the chat_references table
routes.py     POST/GET/DELETE /api/references

See docs/reference-images-plan.md.
"""
