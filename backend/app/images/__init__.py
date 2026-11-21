"""Text-to-image: generated on ComfyUI on AI-1, kept in Postgres per chat.

comfyui.py  the ComfyUI client and the workflows for each model
service.py  one image for a chat: GPU hand-off, checks, storage
store.py    the chat_images table
routes.py   GET/DELETE /api/images/...

The agent reaches it through the generate_image tool in
app/tools/builtin/images.py.
"""
